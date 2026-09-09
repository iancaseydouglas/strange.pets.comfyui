import copy
import datetime
import glob
import json
import math
import os
import random
import re

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from .common import (
    SAVE_FORMATS,
    NodeError,
    comfy_directory,
    load_image_file,
    pil_to_tensor,
    resolve_path,
    sanitize_filename,
    tensor_to_pil,
)
from .runtime import (
    cache_directory,
    dotenv_notes,
    env_file_paths,
    cache_enabled,
    cache_purge,
    cache_stats,
    ledger_entries,
    ledger_reset,
    secret_sources,
)

IO_CATEGORY = "strange-pets/IO"
UTIL_CATEGORY = "strange-pets/Utils"

# Stability's nine supported ratios, reused so one widget can drive both APIs.
STABILITY_RATIOS = ["1:1", "16:9", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"]
RATIO_CHOICES = STABILITY_RATIOS + ["custom"]

# Per-family size rules. FLUX.2 figures come from BFL's published resolution limits:
# min 256px a side, up to 4MP, recommended <=2MP, and dimensions snapped to /32 by the
# API itself. Stability's generate endpoints take an aspect-ratio enum, not pixels, and
# render around 1MP.
TARGETS = {
    "flux-2": {"multiple": 32, "min_side": 256, "max_mp": 4.0, "default_mp": 2.0},
    "stability": {"multiple": 64, "min_side": 512, "max_mp": 1.0, "default_mp": 1.0},
    "custom": {"multiple": 8, "min_side": 64, "max_mp": 16.0, "default_mp": 1.0},
}


def parse_ratio(text):
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:xX/]\s*(\d+(?:\.\d+)?)\s*$", text or "")
    if not match:
        raise NodeError(
            "Could not read {!r} as an aspect ratio. Use forms like '16:9', '4:5' or '1.85:1'.".format(text)
        )
    width, height = float(match.group(1)), float(match.group(2))
    if width <= 0 or height <= 0:
        raise NodeError("Aspect ratio sides must both be greater than zero, got {!r}.".format(text))
    return width / height


def nearest_stability_ratio(value):
    return min(STABILITY_RATIOS, key=lambda name: abs(math.log(parse_ratio(name) / value)))


def fit_to_ratio(value, megapixels, multiple, min_side, max_mp):
    """Pick the width/height closest to `value` that fits the pixel budget and grid.

    Both sides are snapped to the grid together and the best combination is chosen, rather
    than shaving one side to fit the budget — trimming a single side skews the ratio badly
    at coarse grids (16:10 on a /32 grid drifts to 1.57 if you shave the width).
    """
    budget = max(0.01, min(float(megapixels), float(max_mp))) * 1_000_000
    ideal_width = math.sqrt(budget * value)
    ideal_height = math.sqrt(budget / value)
    step = max(1, int(multiple))

    def candidates(ideal):
        base = (int(ideal) // step) * step
        return {side for side in (base - step, base, base + step) if side >= min_side} or {min_side}

    best_key, best_size = None, (min_side, min_side)
    for width in candidates(ideal_width):
        for height in candidates(ideal_height):
            area = width * height
            fits = area <= budget
            error = abs(math.log((width / height) / value))
            # fitting first, then closest ratio, then largest area among fitting sizes
            key = (0 if fits else 1, round(error, 6), -area if fits else area)
            if best_key is None or key < best_key:
                best_key, best_size = key, (width, height)
    return int(best_size[0]), int(best_size[1])


class AspectRatioSize:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("INT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("width", "height", "aspect_ratio", "summary")
    FUNCTION = "compute"
    DESCRIPTION = (
        "Pick an aspect ratio and get back pixel dimensions that respect the target model's "
        "grid and megapixel budget. Also emits the nearest Stability aspect-ratio enum, so one "
        "node can drive both the FLUX.2 width/height inputs and the Stability aspect_ratio widget."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "aspect_ratio": (RATIO_CHOICES, {"default": "1:1"}),
                "custom_ratio": ("STRING", {
                    "default": "16:10",
                    "tooltip": "Used only when aspect_ratio is 'custom'. Forms like 16:10, 1.85:1, 4x5.",
                }),
                "target": (list(TARGETS), {
                    "default": "flux-2",
                    "tooltip": "flux-2: /32 grid, min 256px, up to 4MP (BFL snaps to /32 server-side "
                               "anyway). stability: generate endpoints take the ratio enum, not pixels. "
                               "custom: use the megapixels and multiple_of widgets as given.",
                }),
                "megapixels": ("FLOAT", {
                    "default": 2.0, "min": 0.05, "max": 16.0, "step": 0.05,
                    "tooltip": "Total pixel budget. Clamped to the target's maximum. BFL recommends "
                               "staying at or below 2MP for quality and speed.",
                }),
                "multiple_of": ("INT", {
                    "default": 0, "min": 0, "max": 256,
                    "tooltip": "Snap each side to this grid. 0 uses the target's own value (32 for flux-2).",
                }),
                "orientation": (["as-written", "landscape", "portrait"], {"default": "as-written"}),
            }
        }

    def compute(self, aspect_ratio, custom_ratio, target, megapixels, multiple_of, orientation):
        rules = TARGETS[target]
        value = parse_ratio(custom_ratio if aspect_ratio == "custom" else aspect_ratio)

        if orientation == "landscape" and value < 1:
            value = 1 / value
        elif orientation == "portrait" and value > 1:
            value = 1 / value

        step = multiple_of or rules["multiple"]
        width, height = fit_to_ratio(
            value, megapixels, step, rules["min_side"], rules["max_mp"])

        enum = nearest_stability_ratio(value)
        actual_mp = (width * height) / 1_000_000
        summary = "{}x{} ({:.2f}MP, {}, /{} grid, stability enum {})".format(
            width, height, actual_mp, target, step, enum)
        return (width, height, enum, summary)


class LoadImagesFromDirectory:
    CATEGORY = IO_CATEGORY
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "filenames")
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "load"
    DESCRIPTION = (
        "Load every image in a directory as a list, so downstream nodes run once per image. "
        "Sort, filter by glob, and take a slice of the results."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "directory": ("STRING", {
                    "default": "",
                    "tooltip": "Absolute path, or relative to the ComfyUI input directory.",
                }),
                "pattern": ("STRING", {
                    "default": "*",
                    "tooltip": "Glob applied inside the directory, e.g. *.png or shot_*.jpg",
                }),
                "sort_by": (["name", "modified", "size"], {"default": "name"}),
                "descending": ("BOOLEAN", {"default": False}),
                "start_index": ("INT", {"default": 0, "min": 0, "max": 100000}),
                "limit": ("INT", {
                    "default": 0, "min": 0, "max": 10000,
                    "tooltip": "0 loads every match from start_index onward.",
                }),
            }
        }

    @classmethod
    def IS_CHANGED(cls, directory, pattern, sort_by, descending, start_index, limit):
        resolved = resolve_path(directory, "input")
        try:
            entries = sorted(os.listdir(resolved))
            stamp = [(name, os.path.getmtime(os.path.join(resolved, name))) for name in entries]
        except OSError:
            return "{}|missing".format(directory)
        return json.dumps([stamp, pattern, sort_by, descending, start_index, limit])

    def load(self, directory, pattern, sort_by, descending, start_index, limit):
        resolved = resolve_path(directory, "input")
        if not resolved or not os.path.isdir(resolved):
            raise NodeError("{!r} is not a directory.".format(resolved or directory))

        matches = [
            path for path in glob.glob(os.path.join(glob.escape(resolved), pattern or "*"))
            if os.path.isfile(path) and path.lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"))
        ]
        if not matches:
            raise NodeError(
                "No images in {!r} matching {!r}.".format(resolved, pattern or "*"))

        keys = {
            "name": lambda p: os.path.basename(p).lower(),
            "modified": os.path.getmtime,
            "size": os.path.getsize,
        }
        matches.sort(key=keys[sort_by], reverse=descending)

        selected = matches[start_index:]
        if limit:
            selected = selected[:limit]
        if not selected:
            raise NodeError(
                "start_index {} skips past all {} matching images.".format(start_index, len(matches)))

        images = [load_image_file(path) for path in selected]
        names = [os.path.splitext(os.path.basename(path))[0] for path in selected]
        return (images, names)


def redact_api_keys(payload, node_class_mappings):
    """Blank every api_key value in a serialised ComfyUI prompt/workflow.

    Widget values are stored positionally, so the index of api_key is recovered from
    each class's INPUT_TYPES, mirroring how the frontend lays widgets out (including
    the control_after_generate slot the frontend inserts after an INT named seed).
    """
    data = copy.deepcopy(payload)

    def widget_names_for(class_type):
        node_class = node_class_mappings.get(class_type)
        if node_class is None:
            return None
        try:
            required = node_class.INPUT_TYPES().get("required", {})
        except Exception:
            return None
        names = []
        for name, spec in required.items():
            names.append(name)
            if name in ("seed", "noise_seed") and spec[0] == "INT":
                names.append("control_after_generate")
        return names

    def scrub(node):
        """Walk the whole structure — callers pass the workflow itself, ComfyUI's
        extra_pnginfo wrapper around it, or the API-format prompt."""
        if isinstance(node, list):
            for item in node:
                scrub(item)
            return
        if not isinstance(node, dict):
            return

        # API-format node: {"class_type": ..., "inputs": {...}}
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "api_key" in inputs:
            inputs["api_key"] = ""

        # UI-format node: {"type": ..., "widgets_values": [...]}
        values = node.get("widgets_values")
        if isinstance(values, list):
            names = widget_names_for(node.get("type"))
            if names and "api_key" in names:
                index = names.index("api_key")
                if index < len(values):
                    values[index] = ""

        for value in node.values():
            if isinstance(value, (dict, list)):
                scrub(value)

    scrub(data)
    return data


class SaveImagesToDirectory:
    CATEGORY = IO_CATEGORY
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("paths",)
    OUTPUT_NODE = True
    FUNCTION = "save"
    DESCRIPTION = (
        "Save images to any directory with a templated filename, optionally embedding the "
        "prompt and generation settings into the file and/or a sidecar JSON."
    )

    TOKENS = ("{prefix} {label} {index} {date} {time} {datetime} {seed} {model} {width} "
              "{height} {prompt} {ext}")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "output_dir": ("STRING", {
                    "default": "",
                    "tooltip": "Absolute path, or relative to the ComfyUI output directory. Created if missing.",
                }),
                "filename_template": ("STRING", {
                    "default": "{prefix}_{index:04d}",
                    "tooltip": "Tokens: " + cls.TOKENS + ". Format specs work, e.g. {index:04d}. "
                               "The extension is added automatically.",
                }),
                "prefix": ("STRING", {"default": "strange-pets"}),
                "format": (list(SAVE_FORMATS), {"default": "png"}),
                "quality": ("INT", {
                    "default": 95, "min": 1, "max": 100,
                    "tooltip": "jpeg and webp only; ignored for png.",
                }),
                "embed_metadata": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "PNG only: writes the prompt and settings into the file's text chunks.",
                }),
                "embed_workflow": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "PNG only: also embed the ComfyUI workflow so the image reopens as a "
                               "graph. Any api_key widget is blanked first. Off by default because "
                               "the workflow travels with the file.",
                }),
                "save_sidecar_json": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Write <name>.json beside each image with the same settings. "
                               "Works for every format, unlike the embedded text chunks.",
                }),
                "overwrite": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Off: a numeric suffix is added rather than replacing an existing file.",
                }),
            },
            "optional": {
                "prompt_text": ("STRING", {"forceInput": True}),
                "label": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Per-image name for the {label} token — wire a deck's card "
                               "slugs in and each file lands under its own card's name.",
                }),
                "index": ("INT", {
                    "forceInput": True,
                    "tooltip": "Overrides the running counter behind {index}. Wire a deck's "
                               "slot numbers in and the files number by position, not by "
                               "the order they happened to be written.",
                }),
                "model": ("STRING", {"forceInput": True}),
                "seed": ("INT", {"forceInput": True}),
                "extra_metadata": ("STRING", {"forceInput": True}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    def _next_index(self, directory, prefix):
        pattern = os.path.join(glob.escape(directory), "{}*".format(glob.escape(prefix or "")))
        highest = 0
        for path in glob.glob(pattern):
            found = re.findall(r"(\d+)", os.path.basename(path))
            if found:
                highest = max(highest, int(found[-1]))
        return highest + 1

    def _render_name(self, template, values):
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError) as exc:
            raise NodeError(
                "filename_template {!r} could not be rendered: {}. Available tokens: {}".format(
                    template, exc, self.TOKENS)
            ) from exc

    def save(self, images, output_dir, filename_template, prefix, format, quality,
             embed_metadata, embed_workflow, save_sidecar_json, overwrite,
             prompt_text=None, label=None, index=None, model=None, seed=None,
             extra_metadata=None, prompt=None, extra_pnginfo=None):
        directory = resolve_path(output_dir, "output") or comfy_directory("output")
        os.makedirs(directory, exist_ok=True)

        pil_format, extension = SAVE_FORMATS.get(format, ("PNG", "png"))
        now = datetime.datetime.now()
        first = self._next_index(directory, prefix) if index is None else int(index)
        batch = images if images.dim() == 4 else images.unsqueeze(0)

        saved = []
        for offset in range(batch.shape[0]):
            pil = tensor_to_pil(batch[offset])
            values = {
                "prefix": prefix,
                "label": sanitize_filename(label) if label else "",
                "index": first + offset,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H%M%S"),
                "datetime": now.strftime("%Y-%m-%d_%H%M%S"),
                "seed": "" if seed is None else seed,
                "model": model or "",
                "width": pil.width,
                "height": pil.height,
                "prompt": sanitize_filename((prompt_text or "")[:40]),
                "ext": extension,
            }
            stem = sanitize_filename(self._render_name(filename_template, values))
            path = os.path.join(directory, "{}.{}".format(stem, extension))
            if not overwrite:
                bump = 1
                while os.path.exists(path):
                    path = os.path.join(directory, "{}_{}.{}".format(stem, bump, extension))
                    bump += 1

            settings = {
                "prompt": prompt_text or "",
                "label": label or "",
                "index": first + offset,
                "model": model or "",
                "seed": seed,
                "width": pil.width,
                "height": pil.height,
                "format": format,
                "saved_at": now.isoformat(timespec="seconds"),
            }
            if extra_metadata:
                try:
                    settings["extra"] = json.loads(extra_metadata)
                except (ValueError, TypeError):
                    settings["extra"] = extra_metadata

            if pil_format == "PNG":
                info = PngInfo()
                if embed_metadata:
                    parameters = prompt_text or ""
                    trailer = ", ".join(
                        "{}: {}".format(key, settings[key])
                        for key in ("model", "seed", "width", "height")
                        if settings[key] not in (None, "")
                    )
                    if trailer:
                        parameters = "{}\n{}".format(parameters, trailer).strip()
                    if parameters:
                        info.add_text("parameters", parameters)
                    info.add_text("strange_pets", json.dumps(settings))
                if embed_workflow:
                    node_map = _node_class_mappings()
                    if extra_pnginfo:
                        for key, value in redact_api_keys(extra_pnginfo, node_map).items():
                            info.add_text(key, json.dumps(value))
                    if prompt is not None:
                        info.add_text("prompt", json.dumps(redact_api_keys(prompt, node_map)))
                pil.save(path, "PNG", pnginfo=info)
            elif pil_format == "JPEG":
                pil.convert("RGB").save(path, "JPEG", quality=int(quality))
            else:
                pil.save(path, pil_format, quality=int(quality))

            if save_sidecar_json:
                with open(os.path.splitext(path)[0] + ".json", "w") as handle:
                    json.dump(settings, handle, indent=2)

            saved.append(path)
            print("[strange-pets] saved {}".format(path))

        return ("\n".join(saved),)


def _node_class_mappings():
    """Fetched lazily so this module does not import the package root at load time."""
    try:
        from .. import NODE_CLASS_MAPPINGS

        return NODE_CLASS_MAPPINGS
    except Exception:
        return {}



# =========================================================================== ops

class KeyStatus:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    OUTPUT_NODE = True
    FUNCTION = "report"
    DESCRIPTION = (
        "Show which source each API key is coming from, without ever revealing the key. "
        "Precedence is .env > environment > node widget."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "check": (["all", "BFL_API_KEY", "STABILITY_API_KEY"], {"default": "all"}),
        }}

    @classmethod
    def IS_CHANGED(cls, check):
        return float("nan")  # always re-run; the environment can change underneath us

    def report(self, check):
        names = ["BFL_API_KEY", "STABILITY_API_KEY"] if check == "all" else [check]
        lines = []
        for name in names:
            found = secret_sources(name)
            winner = next((source for source, value in found if value), None)
            present = [source for source, value in found if value]
            if winner:
                lines.append("{}: using {} (also set in: {})".format(
                    name, winner, ", ".join(s for s in present if s != winner) or "nowhere else"))
            else:
                lines.append("{}: NOT SET in .env, environment, or any node widget".format(name))
        lines.append("")
        lines.append("Looked for .env at:")
        for path in env_file_paths():
            lines.append("  {} {}".format("[found]" if os.path.isfile(path) else "[  -  ]", path))
        notes = dotenv_notes()
        if notes:
            lines.append("")
            lines.append("Encrypted values (dotenvx):")
            lines.extend("  " + note for note in notes)
        report = "\n".join(lines)
        print("[strange-pets]\n" + report)
        return (report,)


class CostReport:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("STRING", "FLOAT", "INT")
    RETURN_NAMES = ("report", "credits", "calls")
    OUTPUT_NODE = True
    FUNCTION = "report"
    DESCRIPTION = (
        "Summarise the API calls made this session. BFL reports credit cost per request, so "
        "those totals are real; Stability does not return cost, so those are call counts only."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"reset_after": ("BOOLEAN", {"default": False})}}

    @classmethod
    def IS_CHANGED(cls, reset_after):
        return float("nan")

    def report(self, reset_after):
        entries = ledger_entries()
        if not entries:
            return ("No API calls recorded this session.", 0.0, 0)

        by_endpoint = {}
        for entry in entries:
            key = "{} {}".format(entry["provider"], entry["endpoint"])
            bucket = by_endpoint.setdefault(key, {"calls": 0, "cached": 0, "cost": 0.0, "priced": 0})
            bucket["calls"] += 1
            if entry.get("cached"):
                bucket["cached"] += 1
            if entry.get("cost") is not None:
                bucket["cost"] += float(entry["cost"])
                bucket["priced"] += 1

        total_cost = sum(b["cost"] for b in by_endpoint.values())
        billed = sum(b["calls"] - b["cached"] for b in by_endpoint.values())
        lines = ["{:<42} {:>6} {:>7} {:>10}".format("endpoint", "calls", "cached", "credits")]
        for key in sorted(by_endpoint):
            bucket = by_endpoint[key]
            cost = "{:.3f}".format(bucket["cost"]) if bucket["priced"] else "-"
            lines.append("{:<42} {:>6} {:>7} {:>10}".format(
                key[:42], bucket["calls"], bucket["cached"], cost))
        lines.append("")
        lines.append("{} calls, {} served from cache, {} billed.".format(
            len(entries), len(entries) - billed, billed))
        lines.append("Credits shown only where the API reports them (BFL does; Stability does not).")
        if reset_after:
            ledger_reset()
            lines.append("Ledger reset.")
        report = "\n".join(lines)
        print("[strange-pets]\n" + report)
        return (report, float(total_cost), len(entries))


class CacheTools:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    OUTPUT_NODE = True
    FUNCTION = "run"
    DESCRIPTION = (
        "Inspect or clear the on-disk result cache. Identical requests reuse a stored image "
        "instead of paying again; only requests with an explicit non-random seed are cached."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"action": (["status", "purge"], {"default": "status"})}}

    @classmethod
    def IS_CHANGED(cls, action):
        return float("nan")

    def run(self, action):
        if action == "purge":
            removed = cache_purge()
            report = "Purged {} cache files from {}".format(removed, cache_directory())
        else:
            stats = cache_stats()
            report = "Cache {}: {} entries, {:.1f} MB in {}".format(
                "enabled" if cache_enabled() else "DISABLED (STRANGE_PETS_CACHE)",
                stats["entries"], stats["bytes"] / 1e6, stats["directory"])
        print("[strange-pets] " + report)
        return (report,)





# =========================================================================== limits

_LIMIT_CACHE = {}

# FLUX.2 rules come from BFL's published resolution limits, not their OpenAPI file,
# which documents only `minimum: 64`.
FLUX2_LIMITS = {"min_side": 256, "max_side": 4096, "min_px": 256 * 256,
                "max_px": 4_000_000, "multiple": 32}


def _parse_rules(text):
    rules = {}
    flat = " ".join((text or "").split())

    def number(raw):
        return int(raw.replace(",", ""))

    match = re.search(r"Width must be between ([\d,]+) and ([\d,]+) pixels", flat)
    if match:
        rules["min_side"] = number(match.group(1))
        rules["max_side"] = number(match.group(2))
    match = re.search(r"Every side must be at least ([\d,]+) pixels", flat)
    if match:
        rules["min_side"] = number(match.group(1))
    match = re.search(r"Total pixel count must be between ([\d,]+) and ([\d,]+) pixels", flat)
    if match:
        rules["min_px"] = number(match.group(1))
        rules["max_px"] = number(match.group(2))
    match = re.search(r"total pixel count cannot exceed ([\d,]+) pixels", flat)
    if match:
        rules["max_px"] = number(match.group(1))
    match = re.search(r"Total pixel count must be at least ([\d,]+) pixels", flat)
    if match:
        rules["min_px"] = number(match.group(1))
    return rules


def endpoint_limits():
    """Image constraints per Stability endpoint, parsed from the shipped spec so they
    cannot drift from the API, plus the FLUX.2 rules from BFL's docs."""
    if _LIMIT_CACHE:
        return _LIMIT_CACHE

    _LIMIT_CACHE["flux-2 (BFL, any model)"] = dict(FLUX2_LIMITS)
    spec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "stability-openapi.json")
    try:
        with open(spec_path) as handle:
            spec = json.load(handle)
    except (OSError, ValueError):
        return _LIMIT_CACHE

    for path, item in sorted(spec.get("paths", {}).items()):
        if "v2beta/stable-image" not in path or "post" not in item:
            continue
        schema = (item["post"].get("requestBody", {}).get("content", {})
                  .get("multipart/form-data", {}).get("schema", {}))
        for field, prop in (schema.get("properties") or {}).items():
            if prop.get("format") != "binary":
                continue
            rules = _parse_rules(prop.get("description", ""))
            if not rules:
                continue
            endpoint = path.split("v2beta/stable-image/")[-1]
            label = "stability {}".format(endpoint)
            if field not in ("image", "subject_image", "init_image"):
                label += " ({})".format(field)
            rules.setdefault("multiple", 1)
            _LIMIT_CACHE[label] = rules
    return _LIMIT_CACHE


class FitImageToEndpoint:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "summary")
    FUNCTION = "fit"
    DESCRIPTION = (
        "Rescale an image so it satisfies a chosen endpoint's documented size limits, "
        "before you spend a call finding out. Stability's rules are read from the shipped "
        "OpenAPI spec; the FLUX.2 rules come from BFL's published limits."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "target": (list(endpoint_limits()), {"default": "flux-2 (BFL, any model)"}),
                "mode": (["fit (up or down)", "shrink only"], {"default": "fit (up or down)"}),
                "multiple_of": ("INT", {
                    "default": 0, "min": 0, "max": 256,
                    "tooltip": "Snap each side to this grid. 0 uses the target's own value.",
                }),
                "resample": (["lanczos", "bicubic", "bilinear", "nearest"], {"default": "lanczos"}),
            }
        }

    def fit(self, image, target, mode, multiple_of, resample):
        rules = endpoint_limits()[target]
        pil = tensor_to_pil(image)
        start = (pil.width, pil.height)
        width, height = float(pil.width), float(pil.height)

        max_px, min_px = rules.get("max_px"), rules.get("min_px")
        max_side, min_side = rules.get("max_side"), rules.get("min_side")

        scale = 1.0
        if max_px and width * height > max_px:
            scale = min(scale, math.sqrt(max_px / (width * height)))
        if max_side and max(width, height) * scale > max_side:
            scale = min(scale, max_side / max(width, height))
        width, height = width * scale, height * scale

        if mode != "shrink only":
            grow = 1.0
            if min_side and min(width, height) < min_side:
                grow = max(grow, min_side / min(width, height))
            if min_px and width * height * grow * grow < min_px:
                grow = max(grow, math.sqrt(min_px / (width * height)))
            width, height = width * grow, height * grow

        step = multiple_of or rules.get("multiple", 1)
        step = max(1, int(step))
        # the minimum-side floor is a form of growing, so it must not apply in shrink-only mode
        floor = int(min_side) if (min_side and mode != "shrink only") else step
        new_width = max(floor, int(round(width / step)) * step)
        new_height = max(floor, int(round(height / step)) * step)

        # snapping up can breach the ceiling again; walk back one grid step if so
        while max_px and new_width * new_height > max_px and (new_width > step and new_height > step):
            if new_width >= new_height:
                new_width -= step
            else:
                new_height -= step
        if max_side:
            new_width = min(new_width, int(max_side))
            new_height = min(new_height, int(max_side))

        notes = []
        if (new_width, new_height) != start:
            filters = {"lanczos": Image.LANCZOS, "bicubic": Image.BICUBIC,
                       "bilinear": Image.BILINEAR, "nearest": Image.NEAREST}
            pil = pil.resize((new_width, new_height), filters[resample])
            notes.append("{}x{} -> {}x{}".format(start[0], start[1], new_width, new_height))
        else:
            notes.append("{}x{} already within limits".format(*start))

        if max_px and new_width * new_height > max_px:
            notes.append("STILL over the {:,}px ceiling".format(max_px))
        if min_side and min(new_width, new_height) < min_side:
            notes.append("STILL under the {}px minimum side (shrink-only mode?)".format(min_side))

        summary = "{}: {} ({:,}px)".format(target, "; ".join(notes), new_width * new_height)
        return (pil_to_tensor(pil), summary)


# =========================================================================== helpers

class PromptList:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("prompts", "count")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "build"
    DESCRIPTION = "Turn a block of text into a list of prompts, one per line, so downstream nodes run once per prompt."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
                "separator": (["newline", "comma", "semicolon", "double-newline"], {"default": "newline"}),
                "prefix": ("STRING", {"default": "", "tooltip": "Prepended to every prompt."}),
                "suffix": ("STRING", {"default": "", "tooltip": "Appended to every prompt."}),
                "skip_comments": ("BOOLEAN", {"default": True, "tooltip": "Ignore lines starting with #."}),
            }
        }

    def build(self, text, separator, prefix, suffix, skip_comments):
        splitters = {"newline": "\n", "comma": ",", "semicolon": ";", "double-newline": "\n\n"}
        parts = (text or "").split(splitters[separator])
        prompts = []
        for part in parts:
            cleaned = part.strip()
            if not cleaned or (skip_comments and cleaned.startswith("#")):
                continue
            prompts.append("{}{}{}".format(prefix, cleaned, suffix))
        if not prompts:
            raise NodeError("No prompts found — the text is empty or every line was skipped.")
        return (prompts, len(prompts))


class SeedList:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("seeds",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "build"
    DESCRIPTION = "Produce a list of seeds — explicit, sequential, or random — for reproducible comparisons."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["sequential", "list", "random"], {"default": "sequential"}),
                "start": ("INT", {"default": 1000, "min": 0, "max": 0x7FFFFFFF}),
                "count": ("INT", {"default": 4, "min": 1, "max": 512}),
                "step": ("INT", {"default": 1, "min": 1, "max": 100000}),
                "values": ("STRING", {"default": "", "tooltip": "Used when mode is 'list': 1,2,3"}),
                "random_seed": ("INT", {
                    "default": 0, "min": 0, "max": 0x7FFFFFFF,
                    "tooltip": "Used when mode is 'random'. 0 draws a fresh set each run; any "
                               "other value makes the random set reproducible.",
                }),
            }
        }

    @classmethod
    def IS_CHANGED(cls, mode, start, count, step, values, random_seed):
        if mode == "random" and not random_seed:
            return float("nan")
        return json.dumps([mode, start, count, step, values, random_seed])

    def build(self, mode, start, count, step, values, random_seed):
        if mode == "list":
            seeds = []
            for chunk in re.split(r"[,\s]+", values or ""):
                if not chunk:
                    continue
                try:
                    seeds.append(int(chunk))
                except ValueError as exc:
                    raise NodeError("{!r} in values is not a whole number.".format(chunk)) from exc
            if not seeds:
                raise NodeError("mode is 'list' but values is empty.")
            return (seeds,)
        if mode == "random":
            rng = random.Random(random_seed or None)
            return ([rng.randint(0, 0x7FFFFFFF) for _ in range(count)],)
        return ([start + index * step for index in range(count)],)


class ImageSwitch:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "pick"
    DESCRIPTION = "Pick one of several images by index — A/B between providers without rewiring."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "select": ("INT", {"default": 1, "min": 1, "max": 6,
                                   "tooltip": "Which connected input to pass through."}),
            },
            "optional": {"image_{}".format(n): ("IMAGE",) for n in range(1, 7)},
        }

    def pick(self, select, **kwargs):
        chosen = kwargs.get("image_{}".format(select))
        if chosen is None:
            connected = sorted(name for name, value in kwargs.items() if value is not None)
            raise NodeError(
                "select is {} but image_{} is not connected. Connected inputs: {}.".format(
                    select, select, ", ".join(connected) or "none")
            )
        return (chosen,)


class JoinImageAlpha:
    CATEGORY = UTIL_CATEGORY
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "join"
    DESCRIPTION = (
        "Attach a mask to an image as an alpha channel, for the endpoints that take their "
        "mask from the image's transparency rather than a separate mask field."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "mask": ("MASK",),
                "invert": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "On (default): mask white becomes transparent, which is what "
                               "Stability's alpha route expects — transparent pixels are the "
                               "ones edited. Off: mask white becomes opaque.",
                }),
            }
        }

    def join(self, image, mask, invert):
        frame = image[0] if image.dim() == 4 else image
        alpha = mask
        if alpha.dim() == 3:
            alpha = alpha[0]
        elif alpha.dim() == 4:
            alpha = alpha[0, :, :, 0]

        if alpha.shape != frame.shape[:2]:
            resized = Image.fromarray(
                (alpha.detach().cpu().numpy() * 255).astype("uint8"), mode="L"
            ).resize((frame.shape[1], frame.shape[0]), Image.BILINEAR)
            alpha = torch.from_numpy(np.asarray(resized).astype("float32") / 255.0)

        if invert:
            alpha = 1.0 - alpha
        rgb = frame[:, :, :3]
        joined = torch.cat([rgb, alpha.unsqueeze(-1).to(rgb.dtype)], dim=-1)
        return (joined.unsqueeze(0),)


class ContactSheet:
    CATEGORY = UTIL_CATEGORY
    INPUT_IS_LIST = True
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("sheet",)
    FUNCTION = "build"
    DESCRIPTION = (
        "Lay a list of images out in a labelled grid — the companion to the Batch / Sweep "
        "node, so a sweep can be judged at a glance instead of file by file."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "columns": ("INT", {"default": 0, "min": 0, "max": 16,
                                    "tooltip": "0 picks a roughly square grid."}),
                "cell_size": ("INT", {"default": 512, "min": 64, "max": 2048}),
                "padding": ("INT", {"default": 8, "min": 0, "max": 128}),
                "label_height": ("INT", {"default": 28, "min": 0, "max": 200,
                                         "tooltip": "0 hides labels."}),
                "background": ("STRING", {"default": "#1e1e1e"}),
            },
            "optional": {"labels": ("STRING",)},
        }

    def build(self, images, columns=None, cell_size=None, padding=None,
              label_height=None, background=None, labels=None):
        # INPUT_IS_LIST hands every widget over as a list; the scalars are uniform.
        def first(value, fallback):
            if isinstance(value, list):
                return value[0] if value else fallback
            return fallback if value is None else value

        columns = int(first(columns, 0))
        cell = int(first(cell_size, 512))
        pad = int(first(padding, 8))
        strip = int(first(label_height, 28))
        colour = first(background, "#1e1e1e")

        frames = []
        for item in (images if isinstance(images, list) else [images]):
            batch = item if item.dim() == 4 else item.unsqueeze(0)
            for index in range(batch.shape[0]):
                frames.append(tensor_to_pil(batch[index]))
        if not frames:
            raise NodeError("Contact sheet received no images.")

        captions = labels if isinstance(labels, list) else ([labels] if labels else [])
        captions = [str(text) for text in captions]

        if not columns:
            columns = max(1, int(math.ceil(math.sqrt(len(frames)))))
        rows = int(math.ceil(len(frames) / columns))

        tile_w, tile_h = cell, cell + strip
        sheet = Image.new("RGB", (columns * tile_w + pad * (columns + 1),
                                  rows * tile_h + pad * (rows + 1)), colour)
        draw = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.load_default(size=max(10, strip - 12)) if strip else None
        except TypeError:
            font = ImageFont.load_default() if strip else None

        for index, frame in enumerate(frames):
            row, column = divmod(index, columns)
            thumb = frame.copy()
            thumb.thumbnail((cell, cell), Image.LANCZOS)
            x = pad + column * (tile_w + pad) + (cell - thumb.width) // 2
            y = pad + row * (tile_h + pad) + (cell - thumb.height) // 2
            sheet.paste(thumb, (x, y))
            if strip and index < len(captions):
                text = captions[index]
                tx = pad + column * (tile_w + pad)
                ty = pad + row * (tile_h + pad) + cell + 4
                draw.text((tx + 4, ty), text[:80], fill="#e6e6e6", font=font)

        return (pil_to_tensor(sheet),)


NODE_CLASS_MAPPINGS = {
    "SPAspectRatioSize": AspectRatioSize,
    "SPFitImageToEndpoint": FitImageToEndpoint,
    "SPLoadImagesFromDirectory": LoadImagesFromDirectory,
    "SPSaveImagesToDirectory": SaveImagesToDirectory,
    "SPContactSheet": ContactSheet,
    "SPPromptList": PromptList,
    "SPSeedList": SeedList,
    "SPImageSwitch": ImageSwitch,
    "SPJoinImageAlpha": JoinImageAlpha,
    "SPKeyStatus": KeyStatus,
    "SPCostReport": CostReport,
    "SPCacheTools": CacheTools,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SPAspectRatioSize": "Aspect Ratio → Size",
    "SPFitImageToEndpoint": "Fit Image To Endpoint Limits",
    "SPLoadImagesFromDirectory": "Load Images From Directory",
    "SPSaveImagesToDirectory": "Save Images To Directory",
    "SPContactSheet": "Contact Sheet",
    "SPPromptList": "Prompt List",
    "SPSeedList": "Seed List",
    "SPImageSwitch": "Image Switch",
    "SPJoinImageAlpha": "Join Image + Alpha",
    "SPKeyStatus": "API Key Status",
    "SPCostReport": "Cost / Call Report",
    "SPCacheTools": "Cache Tools",
}
