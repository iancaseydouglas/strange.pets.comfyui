"""Vendor-neutral helpers shared by every node pack in this repo."""

import glob
import io
import os
import re

import numpy as np
import torch
from PIL import Image, ImageOps

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")
SAVE_FORMATS = {"jpeg": ("JPEG", "jpg"), "png": ("PNG", "png"), "webp": ("WEBP", "webp")}


class NodeError(RuntimeError):
    """Base for user-facing node errors, so packs can subclass and stay catchable."""


# --------------------------------------------------------------------------- tensors

def tensor_to_pil(image):
    array = image
    if array.dim() == 4:
        array = array[0]
    array = np.clip(array.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    if array.ndim == 3 and array.shape[2] == 1:
        array = array[:, :, 0]
    return Image.fromarray(array)


def pil_to_tensor(pil):
    pil = ImageOps.exif_transpose(pil).convert("RGB")
    array = np.asarray(pil).astype(np.float32) / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def bytes_to_tensor(payload):
    return pil_to_tensor(Image.open(io.BytesIO(payload)))


def tensor_to_png_bytes(image):
    buffer = io.BytesIO()
    tensor_to_pil(image).save(buffer, format="PNG")
    return buffer.getvalue()


def tensor_to_jpeg_bytes(image, quality=95):
    buffer = io.BytesIO()
    tensor_to_pil(image).convert("RGB").save(
        buffer, "JPEG", quality=int(quality), subsampling=0, optimize=True)
    return buffer.getvalue()


def encode_for_upload(image, prefer="auto", quality=95):
    """Encode an IMAGE for a multipart upload. Returns (filename, bytes, mime type).

    PNG whenever the tensor carries alpha, because several endpoints mask by the image's
    alpha channel and flattening it would silently change what they do.

    Otherwise `auto` encodes both and sends the smaller, because which format wins depends
    entirely on the picture and guessing is worse than measuring. Flat colour and linework —
    a tarot card — compress several times better as PNG; a photographic frame goes several
    times smaller as JPEG. The upload is the fragile half of one of these calls, so the
    cost of encoding twice is trivial against sending the wrong one. The shipped OpenAPI
    description lists jpeg, png and webp for every image field.
    """
    array = image[0] if image.dim() == 4 else image
    if array.shape[-1] == 4 or prefer == "png":
        return ("upload.png", tensor_to_png_bytes(image), "image/png")

    jpeg = tensor_to_jpeg_bytes(image, quality)
    if prefer == "jpeg":
        return ("upload.jpg", jpeg, "image/jpeg")
    png = tensor_to_png_bytes(image)
    if len(png) <= len(jpeg):
        return ("upload.png", png, "image/png")
    return ("upload.jpg", jpeg, "image/jpeg")


def mask_to_png_bytes(mask):
    """Encode a ComfyUI MASK (B,H,W in 0..1) as an 8-bit greyscale PNG.

    ComfyUI and Stability agree on polarity — white is the area to act on — so the
    values pass straight through with no inversion.
    """
    array = mask
    if array.dim() == 3:
        array = array[0]
    if array.dim() == 4:
        array = array[0, :, :, 0]
    array = np.clip(array.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(array, mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


def raise_if_interrupted():
    try:
        import comfy.model_management
    except ImportError:
        return
    comfy.model_management.throw_exception_if_processing_interrupted()


# --------------------------------------------------------------------------- paths

def comfy_directory(kind):
    try:
        import folder_paths

        return folder_paths.get_input_directory() if kind == "input" else folder_paths.get_output_directory()
    except Exception:
        return os.getcwd()


def resolve_path(path, kind):
    path = os.path.expanduser((path or "").strip())
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.join(comfy_directory(kind), path)


def list_input_images(input_path, pattern="*"):
    """Resolve input_path (a file, directory, or glob) to a sorted list of image files."""
    resolved = resolve_path(input_path, "input")
    if not resolved:
        return []
    if os.path.isdir(resolved):
        matches = [
            path for path in glob.glob(os.path.join(glob.escape(resolved), pattern or "*"))
            if path.lower().endswith(IMAGE_EXTS) and os.path.isfile(path)
        ]
    elif any(ch in resolved for ch in "*?[") and not os.path.exists(resolved):
        matches = [m for m in glob.glob(resolved) if m.lower().endswith(IMAGE_EXTS)]
    elif os.path.isfile(resolved):
        matches = [resolved]
    else:
        raise NodeError("No image found at {!r}.".format(resolved))
    if not matches:
        raise NodeError(
            "No image files ({}) found under {!r} matching {!r}.".format(
                ", ".join(IMAGE_EXTS), resolved, pattern or "*")
        )
    return sorted(matches)


def load_image_file(path):
    try:
        return pil_to_tensor(Image.open(path))
    except Exception as exc:
        raise NodeError("Could not read image {!r}: {}".format(path, exc)) from exc


def sanitize_filename(text):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")
    return cleaned or "image"


def save_tensor_to_dir(image, out_dir, stem, output_format):
    fmt, ext = SAVE_FORMATS.get(output_format, ("PNG", "png"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "{}.{}".format(sanitize_filename(stem), ext))
    pil = tensor_to_pil(image)
    if fmt == "JPEG":
        pil.save(path, "JPEG", quality=95)
    else:
        pil.save(path, fmt)
    return path
