import base64
import io
import json
import os
import random
import re
import time

import numpy as np
import requests
import torch
from PIL import Image, ImageOps

API_BASE = "https://api.bfl.ai"
REQUEST_TIMEOUT = 60
POLL_INTERVAL = 0.5
POLL_TIMEOUT = 600

IN_PROGRESS_STATUSES = ("Pending", "Reasoning", "Generating")
IMAGE_SLOT_RE = re.compile(r"^input_image(?:_(\d+))?$")


class BFLApiError(RuntimeError):
    pass


def image_slot_name(index):
    return "input_image" if index == 1 else "input_image_{}".format(index)


def resolve_api_key(fallback):
    key = os.environ.get("BFL_API_KEY", "").strip() or (fallback or "").strip()
    if not key:
        raise BFLApiError(
            "No BFL API key. Set the BFL_API_KEY environment variable before starting "
            "ComfyUI, or paste a key into the node's api_key widget."
        )
    return key


def headers(api_key):
    return {
        "x-key": api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }


def tensor_to_base64_png(image):
    array = image
    if array.dim() == 4:
        array = array[0]
    array = np.clip(array.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    if array.ndim == 3 and array.shape[2] == 1:
        array = array[:, :, 0]
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def bytes_to_tensor(payload):
    pil = Image.open(io.BytesIO(payload))
    pil = ImageOps.exif_transpose(pil).convert("RGB")
    array = np.asarray(pil).astype(np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def raise_if_interrupted():
    try:
        import comfy.model_management
    except ImportError:
        return
    comfy.model_management.throw_exception_if_processing_interrupted()


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


class Flux2ApiNode:
    ENDPOINT = ""
    MAX_IMAGES = 8
    EXTRA_FIELDS = ()

    CATEGORY = "BFL/FLUX.2"
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
                        "tooltip": "-1 draws a fresh random seed for each run.",
                    },
                ),
                "width": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 4096,
                        "step": 64,
                        "tooltip": "0 lets the API match the reference image (or pick its own size for "
                        "text-to-image). Any other value must be at least 64.",
                    },
                ),
                "height": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 4096,
                        "step": 64,
                        "tooltip": "0 lets the API match the reference image (or pick its own size for "
                        "text-to-image). Any other value must be at least 64.",
                    },
                ),
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
        task_id, polling_url = self.submit(payload, api_key)
        sample_url = self.poll(task_id, polling_url, api_key)
        return (self.download(sample_url),)

    def build_payload(self, kwargs):
        seed = kwargs.pop("seed", -1)
        width = kwargs.pop("width", 0)
        height = kwargs.pop("height", 0)

        payload = {
            "prompt": kwargs.pop("prompt", ""),
            "seed": random.randint(0, 0x7FFFFFFF) if seed is None or seed < 0 else int(seed),
            "safety_tolerance": int(kwargs.pop("safety_tolerance", 2)),
            "output_format": kwargs.pop("output_format", "jpeg"),
        }

        for name, value in (("width", width), ("height", height)):
            if not value:
                continue
            if value < 64:
                raise BFLApiError(
                    "{} must be 0 (match the input image) or at least 64, got {}.".format(name, value)
                )
            payload[name] = int(value)

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

    def submit(self, payload, api_key):
        url = "{}/v1/{}".format(API_BASE, self.ENDPOINT)
        try:
            response = requests.post(url, json=payload, headers=headers(api_key), timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise BFLApiError("Could not reach {}: {}".format(url, exc)) from exc

        if response.status_code >= 400:
            raise BFLApiError(
                "{} rejected the request (HTTP {}): {}".format(
                    self.ENDPOINT, response.status_code, error_detail(response)
                )
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise BFLApiError("{} returned a non-JSON response: {}".format(self.ENDPOINT, response.text[:500])) from exc

        task_id = data.get("id")
        polling_url = data.get("polling_url") or "{}/v1/get_result".format(API_BASE)
        if not task_id:
            raise BFLApiError("{} returned no task id: {}".format(self.ENDPOINT, json.dumps(data)[:500]))
        return task_id, polling_url

    def poll(self, task_id, polling_url, api_key):
        params = None if "id=" in polling_url else {"id": task_id}
        deadline = time.monotonic() + POLL_TIMEOUT
        last_status = "Pending"

        while True:
            raise_if_interrupted()
            try:
                response = requests.get(
                    polling_url, params=params, headers=headers(api_key), timeout=REQUEST_TIMEOUT
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

    def download(self, url):
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
