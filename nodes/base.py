import base64
import json
import os
import random
import re
import time

import requests

from .common import (
    IMAGE_EXTS,
    NodeError,
    SAVE_FORMATS,
    bytes_to_tensor,
    comfy_directory,
    list_input_images,
    load_image_file,
    pil_to_tensor,
    raise_if_interrupted,
    resolve_path,
    sanitize_filename,
    save_tensor_to_dir,
    tensor_to_pil,
    tensor_to_png_bytes,
)
from .runtime import (
    cache_enabled,
    cache_get,
    cache_key,
    cache_put,
    record_call,
    request_with_retry,
    resolve_secret,
)

API_BASE = "https://api.bfl.ai"
REQUEST_TIMEOUT = 60
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 600
MAX_DIMENSION = 16384

IN_PROGRESS_STATUSES = ("Pending", "Reasoning", "Generating")
IMAGE_SLOT_RE = re.compile(r"^input_image(?:_(\d+))?$")


class BFLApiError(NodeError):
    pass


def image_slot_name(index):
    return "input_image" if index == 1 else "input_image_{}".format(index)


def resolve_api_key(fallback):
    key, _source = resolve_secret("BFL_API_KEY", fallback)
    if not key:
        raise BFLApiError(
            "No BFL API key. Put BFL_API_KEY in a .env file beside this pack, or set it as "
            "an environment variable before starting ComfyUI, or paste a key into the "
            "node's api_key widget."
        )
    return key


def headers(api_key):
    return {
        "x-key": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }


def dimension_widget(label):
    return (
        "INT",
        {
            "default": 0,
            "min": 0,
            "max": MAX_DIMENSION,
            "step": 1,
            "tooltip": "0 lets the API match the reference image (or pick its own size for "
            "text-to-image). Otherwise sent verbatim — the API enforces its own {} limits.".format(label),
        },
    )


def apply_dimensions(payload, width, height):
    """Include width/height only when non-zero. No client-side range check —
    the API is the source of truth for what it accepts."""
    if width:
        payload["width"] = int(width)
    if height:
        payload["height"] = int(height)


def tensor_to_base64_png(image):
    return base64.b64encode(tensor_to_png_bytes(image)).decode("ascii")


def error_detail(response):
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or "HTTP {}".format(response.status_code)
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict) and "msg" in item:
                location = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                parts.append("{}: {}".format(location, item["msg"]) if location else item["msg"])
            else:
                parts.append(json.dumps(item) if isinstance(item, (dict, list)) else str(item))
        return "; ".join(part for part in parts if part)
    if isinstance(detail, (dict, list)):
        return json.dumps(detail)
    return str(detail)


def describe(value):
    if not value:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def submit_task(endpoint, payload, api_key):
    url = "{}/v1/{}".format(API_BASE, endpoint)
    try:
        response = request_with_retry(
            lambda: requests.post(url, json=payload, headers=headers(api_key),
                                  timeout=REQUEST_TIMEOUT),
            label=endpoint,
        )
    except requests.RequestException as exc:
        raise BFLApiError("Could not reach {}: {}".format(url, exc)) from exc

    if response.status_code >= 400:
        raise BFLApiError(
            "{} rejected the request (HTTP {}): {}".format(
                endpoint, response.status_code, error_detail(response)
            )
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise BFLApiError("{} returned a non-JSON response: {}".format(endpoint, response.text[:500])) from exc

    task_id = data.get("id")
    polling_url = data.get("polling_url") or "{}/v1/get_result".format(API_BASE)
    if not task_id:
        raise BFLApiError("{} returned no task id: {}".format(endpoint, json.dumps(data)[:500]))
    # BFL reports billing on the submit response; surface it instead of discarding it.
    record_call("bfl", endpoint, cost=data.get("cost"),
                input_mp=data.get("input_mp"), output_mp=data.get("output_mp"))
    return task_id, polling_url


def poll_task(task_id, polling_url, api_key):
    params = None if "id=" in polling_url else {"id": task_id}
    deadline = time.monotonic() + POLL_TIMEOUT
    last_status = "Pending"

    while True:
        raise_if_interrupted()
        try:
            response = request_with_retry(
                lambda: requests.get(polling_url, params=params, headers=headers(api_key),
                                     timeout=REQUEST_TIMEOUT),
                label="{} poll".format(task_id),
            )
        except requests.RequestException as exc:
            raise BFLApiError("Polling {} failed: {}".format(polling_url, exc)) from exc

        if response.status_code >= 400:
            raise BFLApiError(
                "Polling task {} failed (HTTP {}): {}".format(
                    task_id, response.status_code, error_detail(response)
                )
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise BFLApiError("Polling task {} returned non-JSON: {}".format(task_id, response.text[:500])) from exc

        last_status = data.get("status")
        details = describe(data.get("details"))

        if last_status == "Ready":
            result = data.get("result")
            sample = result.get("sample") if isinstance(result, dict) else None
            if not sample:
                raise BFLApiError(
                    "Task {} is Ready but carries no result URL: {}".format(task_id, json.dumps(result)[:500])
                )
            return sample

        if last_status in IN_PROGRESS_STATUSES:
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL)
            continue

        if last_status == "Request Moderated":
            raise BFLApiError(
                "Moderation: the BFL API refused the prompt for task {} before generating. "
                "Rewrite the prompt, or raise safety_tolerance if the content is genuinely allowed.{}".format(
                    task_id, " Details: {}".format(details) if details else ""
                )
            )

        if last_status == "Content Moderated":
            raise BFLApiError(
                "Moderation: the image generated for task {} was blocked after the fact. "
                "Adjust the prompt or reference images, or raise safety_tolerance.{}".format(
                    task_id, " Details: {}".format(details) if details else ""
                )
            )

        if last_status == "Task not found":
            raise BFLApiError(
                "The BFL API no longer knows about task {}. Results expire, so this usually means "
                "the polling loop started too late.".format(task_id)
            )

        if last_status == "Error":
            raise BFLApiError(
                "Task {} failed.{}".format(task_id, " {}".format(details) if details else "")
            )

        raise BFLApiError(
            "Task {} reported an unrecognised status {!r}.{}".format(
                task_id, last_status, " {}".format(details) if details else ""
            )
        )

    raise BFLApiError(
        "Task {} was still {} after {} seconds; giving up.".format(task_id, last_status, POLL_TIMEOUT)
    )


def download_image(url):
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BFLApiError(
            "Could not download the result image: {}. Signed URLs expire 10 minutes "
            "after generation.".format(exc)
        ) from exc

    if response.status_code >= 400:
        raise BFLApiError(
            "Could not download the result image (HTTP {}): {}. Signed URLs expire 10 minutes "
            "after generation.".format(response.status_code, response.text[:200])
        )

    try:
        return bytes_to_tensor(response.content)
    except Exception as exc:
        raise BFLApiError("The downloaded result was not a readable image: {}".format(exc)) from exc


def generate(endpoint, payload, api_key):
    """Submit one task, poll it to completion, and download the result as an IMAGE tensor.

    Cached on the exact request body. BFL always receives a concrete seed (a -1 widget is
    resolved to a random integer first), so a cache hit only happens when every input
    including the seed is identical — a pinned-seed rerun, never a fresh random one.
    """
    key = cache_key("bfl", endpoint, payload) if cache_enabled() else None
    if key:
        hit = cache_get(key)
        if hit is not None:
            record_call("bfl", endpoint, cached=True)
            print("[strange-pets] cache hit for {} (seed {})".format(endpoint, payload.get("seed")))
            return hit

    task_id, polling_url = submit_task(endpoint, payload, api_key)
    sample_url = poll_task(task_id, polling_url, api_key)
    image = download_image(sample_url)
    if key:
        cache_put(key, image, {"provider": "bfl", "endpoint": endpoint,
                               "seed": payload.get("seed"), "prompt": payload.get("prompt", "")[:500]})
    return image


def resolve_seed(seed):
    return random.randint(0, 0x7FFFFFFF) if seed is None or int(seed) < 0 else int(seed)


class Flux2ApiNode:
    ENDPOINT = ""
    MAX_IMAGES = 8
    EXTRA_FIELDS = ()

    CATEGORY = "strange-pets/BFL/FLUX.2"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "execute"

    @classmethod
    def build_input_types(cls, extras=None):
        required = {
            "prompt": ("STRING", {"multiline": True, "default": ""}),
        }
        if extras:
            required.update(extras)
        required.update(
            {
                "seed": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": 0x7FFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "-1 draws a fresh random seed for each run.",
                    },
                ),
                "width": dimension_widget("width"),
                "height": dimension_widget("height"),
                "safety_tolerance": (
                    "INT",
                    {
                        "default": 2,
                        "min": 0,
                        "max": 5,
                        "display": "slider",
                        "tooltip": "0 is the strictest moderation setting, 5 the most permissive.",
                    },
                ),
                "output_format": (["jpeg", "png", "webp"], {"default": "jpeg"}),
                "api_key": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "password": True,
                        "tooltip": "Only used when BFL_API_KEY is unset. Anything typed here is saved "
                        "into the workflow JSON in plain text.",
                    },
                ),
            }
        )
        return {
            "required": required,
            "optional": {
                "input_image": (
                    "IMAGE",
                    {"tooltip": "Reference image. Connect it and another socket appears, up to "
                     "{} of them.".format(cls.MAX_IMAGES)},
                ),
            },
        }

    def execute(self, **kwargs):
        api_key = resolve_api_key(kwargs.pop("api_key", ""))
        payload = self.build_payload(kwargs)
        return (generate(self.ENDPOINT, payload, api_key),)

    def build_payload(self, kwargs):
        seed = kwargs.pop("seed", -1)
        width = kwargs.pop("width", 0)
        height = kwargs.pop("height", 0)

        payload = {
            "prompt": kwargs.pop("prompt", ""),
            "seed": resolve_seed(seed),
            "safety_tolerance": int(kwargs.pop("safety_tolerance", 2)),
            "output_format": kwargs.pop("output_format", "jpeg"),
        }
        apply_dimensions(payload, width, height)

        for field in self.EXTRA_FIELDS:
            if field in kwargs:
                payload[field] = kwargs.pop(field)

        for index in range(1, self.MAX_IMAGES + 1):
            slot = image_slot_name(index)
            image = kwargs.pop(slot, None)
            if image is not None:
                payload[slot] = tensor_to_base64_png(image)

        leftover = sorted(name for name in kwargs if IMAGE_SLOT_RE.match(name))
        if leftover:
            raise BFLApiError(
                "{} accepts at most {} reference images; disconnect {}.".format(
                    self.ENDPOINT, self.MAX_IMAGES, ", ".join(leftover)
                )
            )
        return payload
