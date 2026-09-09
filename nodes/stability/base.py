import hashlib
import json
import time

import requests

from ..runtime import (
    cache_enabled,
    cache_get,
    cache_key,
    cache_put,
    record_call,
    request_with_retry,
    resolve_secret,
    setting,
    setting_int,
    transport_advice,
)
from ..common import (
    NodeError,
    bytes_to_tensor,
    encode_for_upload,
    mask_to_png_bytes,
    raise_if_interrupted,
)

API_BASE = "https://api.stability.ai"
STABLE_IMAGE = "/v2beta/stable-image"
RESULTS_PATH = "/v2beta/results"
REQUEST_TIMEOUT = 180
POLL_INTERVAL = 2.0
POLL_TIMEOUT = 900

SEED_MAX = 4294967294
OUTPUT_FORMATS = ["png", "jpeg", "webp"]
TRANSPARENT_FORMATS = ["png", "webp"]
ASPECT_RATIOS = ["1:1", "16:9", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"]
STYLE_PRESETS = [
    "3d-model", "analog-film", "anime", "cinematic", "comic-book", "digital-art",
    "enhance", "fantasy-art", "isometric", "line-art", "low-poly",
    "modeling-compound", "neon-punk", "origami", "photographic", "pixel-art",
    "tile-texture",
]
NONE = "none"


class StabilityApiError(NodeError):
    pass


def resolve_api_key(fallback):
    key, _source = resolve_secret("STABILITY_API_KEY", fallback)
    if not key:
        raise StabilityApiError(
            "No Stability API key. Put STABILITY_API_KEY in a .env file beside this pack, or "
            "set it as an environment variable before starting ComfyUI, or paste a key into "
            "the node's api_key widget."
        )
    return key


def headers(api_key):
    return {"authorization": "Bearer {}".format(api_key), "accept": "image/*"}


# --------------------------------------------------------------------------- widgets

def prompt_widget(multiline=True, tooltip=None):
    options = {"multiline": multiline, "default": "", "maxLength": 10000}
    if tooltip:
        options["tooltip"] = tooltip
    return ("STRING", options)


def negative_prompt_widget():
    return ("STRING", {"multiline": True, "default": "", "maxLength": 10000,
                       "tooltip": "Leave empty to omit."})


def seed_widget():
    return ("INT", {"default": 0, "min": 0, "max": SEED_MAX, "control_after_generate": True,
                    "tooltip": "0 lets the API pick a random seed."})


def output_format_widget(choices=None):
    return (list(choices or OUTPUT_FORMATS), {"default": "png"})


def style_preset_widget():
    return ([NONE] + STYLE_PRESETS, {"default": NONE, "tooltip": "'none' omits the field."})


def aspect_ratio_widget():
    return (list(ASPECT_RATIOS), {"default": "1:1"})


def float_widget(default, low, high, step=0.05, tooltip=None):
    options = {"default": default, "min": low, "max": high, "step": step}
    if tooltip:
        options["tooltip"] = tooltip
    return ("FLOAT", options)


def int_widget(default, low, high, tooltip=None):
    options = {"default": default, "min": low, "max": high}
    if tooltip:
        options["tooltip"] = tooltip
    return ("INT", options)


def api_key_widget():
    return ("STRING", {"default": "", "multiline": False, "password": True,
                       "tooltip": "Only used when STABILITY_API_KEY is unset. Saved into the "
                                  "workflow JSON in plain text."})


# --------------------------------------------------------------------------- transport

def error_message(response):
    """Stability errors are {id, name, errors[]} — errors is the human-readable part."""
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:500] or "HTTP {}".format(response.status_code)
    if not isinstance(payload, dict):
        return json.dumps(payload)[:500]
    parts = payload.get("errors")
    if isinstance(parts, list) and parts:
        message = "; ".join(str(part) for part in parts)
    else:
        message = str(payload.get("message") or payload.get("name") or json.dumps(payload)[:500])
    name = payload.get("name")
    return "{} ({})".format(message, name) if name and name not in message else message


def _raise_for_error(response, endpoint):
    name = ""
    try:
        body = response.json()
        name = body.get("name", "") if isinstance(body, dict) else ""
    except ValueError:
        pass
    detail = error_message(response)
    if response.status_code == 403 and name == "content_moderation":
        raise StabilityApiError(
            "Moderation: Stability refused this request for {} before generating. {}\n"
            "The Stable Image API exposes no safety/permissiveness setting to relax — "
            "the prompt or image has to change.".format(endpoint, detail)
        )
    raise StabilityApiError(
        "{} failed (HTTP {}): {}".format(endpoint, response.status_code, detail)
    )


def _check_filtered(response, endpoint):
    """CONTENT_FILTERED means a 200 carrying a *blurred* image. Bail out before
    reading the body so the blurred bytes are never downloaded."""
    if response.headers.get("finish-reason") == "CONTENT_FILTERED":
        response.close()
        raise StabilityApiError(
            "Moderation: {} generated an image that tripped Stability's content filter, so the "
            "API blurred it. The blurred image was not downloaded. The Stable Image API exposes "
            "no safety/permissiveness setting to relax — adjust the prompt or input image.".format(endpoint)
        )


def form_fingerprint(form):
    """A hashable view of a multipart form: file parts collapse to a digest."""
    view = {}
    for name, item in form.items():
        if isinstance(item, tuple):
            payload = item[1] if len(item) > 1 else None
            if isinstance(payload, (bytes, bytearray)):
                view[name] = "sha256:" + hashlib.sha256(bytes(payload)).hexdigest()
            else:
                view[name] = payload
        else:
            view[name] = item
    return view


def _post(endpoint, form, api_key, stream):
    url = "{}{}/{}".format(API_BASE, STABLE_IMAGE, endpoint)
    try:
        return request_with_retry(
            lambda: requests.post(url, headers=headers(api_key), files=form,
                                  timeout=REQUEST_TIMEOUT, stream=stream),
            label=endpoint,
        )
    except requests.RequestException as exc:
        raise StabilityApiError(
            "Could not reach {}: {}{}".format(url, exc, transport_advice(exc))) from exc


def request_sync(endpoint, form, api_key):
    response = _post(endpoint, form, api_key, stream=True)
    if response.status_code != 200:
        _raise_for_error(response, endpoint)
    _check_filtered(response, endpoint)
    return bytes_to_tensor(response.content)


def request_async(endpoint, form, api_key):
    response = _post(endpoint, form, api_key, stream=False)
    if response.status_code != 200:
        _raise_for_error(response, endpoint)
    try:
        generation_id = response.json()["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise StabilityApiError(
            "{} did not return a generation id: {}".format(endpoint, (response.text or "")[:300])
        ) from exc
    return poll_result(generation_id, endpoint, api_key)


def poll_result(generation_id, endpoint, api_key):
    url = "{}{}/{}".format(API_BASE, RESULTS_PATH, generation_id)
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        raise_if_interrupted()
        try:
            response = request_with_retry(
                lambda: requests.get(url, headers=headers(api_key),
                                     timeout=REQUEST_TIMEOUT, stream=True),
                label="{} poll".format(endpoint),
            )
        except requests.RequestException as exc:
            raise StabilityApiError("Polling {} failed: {}".format(url, exc)) from exc

        if response.status_code == 202:
            response.close()
            if time.monotonic() >= deadline:
                raise StabilityApiError(
                    "{} (id {}) was still running after {} seconds; giving up. Results stay "
                    "fetchable for 24h.".format(endpoint, generation_id, POLL_TIMEOUT)
                )
            time.sleep(POLL_INTERVAL)
            continue

        if response.status_code == 404:
            response.close()
            raise StabilityApiError(
                "Stability has no result for id {}. Results expire after 24 hours, and must be "
                "fetched with the same API key that created them.".format(generation_id)
            )

        if response.status_code != 200:
            _raise_for_error(response, endpoint)

        _check_filtered(response, endpoint)
        return bytes_to_tensor(response.content)


def run_request(endpoint, form, api_key, is_async):
    """Send one request, with the result cache in front of it.

    Only deterministic requests are cached. Stability treats seed 0 as "pick a random
    one", so an unseeded request would otherwise keep returning its first result
    forever; those always go to the API.
    """
    seed = form.get("seed")
    seed_value = seed[1] if isinstance(seed, tuple) else seed
    deterministic = bool(seed_value) and str(seed_value) not in ("0", "0.0")

    key = cache_key("stability", endpoint, form_fingerprint(form)) \
        if (cache_enabled() and deterministic) else None
    if key:
        hit = cache_get(key)
        if hit is not None:
            record_call("stability", endpoint, cached=True)
            print("[strange-pets] cache hit for {} (seed {})".format(endpoint, seed_value))
            return hit

    send = request_async if is_async else request_sync
    image = send(endpoint, form, api_key)
    record_call("stability", endpoint)
    if key:
        prompt = form.get("prompt")
        cache_put(key, image, {"provider": "stability", "endpoint": endpoint,
                               "seed": seed_value,
                               "prompt": (prompt[1] if isinstance(prompt, tuple) else prompt) or ""})
    return image


# --------------------------------------------------------------------------- node base

class StabilityNode:
    ENDPOINT = ""
    IS_ASYNC = False
    IMAGE_FIELDS = ("image",)
    MASK_FIELDS = ()
    OMIT_WHEN_EMPTY = ("negative_prompt", "prompt", "background_prompt", "foreground_prompt")
    BOOL_FIELDS = ()

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "execute"

    def validate(self, kwargs):
        """Hook for rules the API only reports after a paid round-trip."""

    def build_form(self, kwargs):
        form = {}
        prefer = setting("STRANGE_PETS_UPLOAD_FORMAT", "auto").lower()
        quality = setting_int("STRANGE_PETS_UPLOAD_QUALITY", 95)
        for name in self.IMAGE_FIELDS:
            image = kwargs.pop(name, None)
            if image is not None:
                _stem, payload, mime = encode_for_upload(image, prefer, quality)
                form[name] = ("{}.{}".format(name, mime.rsplit("/", 1)[-1]), payload, mime)
        for name in self.MASK_FIELDS:
            mask = kwargs.pop(name, None)
            if mask is not None:
                form[name] = ("{}.png".format(name), mask_to_png_bytes(mask), "image/png")

        for name, value in kwargs.items():
            if value is None:
                continue
            if name in self.BOOL_FIELDS:
                form[name] = (None, "true" if value else "false")
                continue
            if isinstance(value, str):
                text = value.strip()
                if not text or text == NONE:
                    continue
                form[name] = (None, text)
                continue
            if isinstance(value, bool):
                form[name] = (None, "true" if value else "false")
                continue
            form[name] = (None, str(value))
        return form

    def execute(self, **kwargs):
        api_key = resolve_api_key(kwargs.pop("api_key", ""))
        self.validate(kwargs)
        form = self.build_form(kwargs)
        return (run_request(self.ENDPOINT, form, api_key, self.IS_ASYNC),)
