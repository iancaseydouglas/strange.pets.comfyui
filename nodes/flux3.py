import json
import re

from .base import (
    BFLApiError,
    IMAGE_SLOT_RE,
    download_image,
    image_slot_name,
    poll_result,
    resolve_api_key,
    resolve_seed,
    submit_task,
    tensor_to_base64_png,
)
from .runtime import cache_enabled, cache_get, cache_get_meta, cache_key, cache_put, record_call

CATEGORY = "strange-pets/BFL/FLUX 3"
MAX_IMAGES = 10
MAX_DIMENSION = 6256
SEED_MAX = 0xFFFFFFFF

ASPECT_RATIOS = ["auto", "21:9", "2:1", "16:9", "3:2", "7:5", "4:3", "5:4", "1:1", "4:5",
                 "3:4", "5:7", "2:3", "9:16", "1:2", "9:21"]
TIERS = ["768sq", "1k", "1.5k", "2k", "4k"]
BOX_KINDS = ["layout", "keep", "move", "new", "remove"]
BOX_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def generate_with_prompt(endpoint, payload, api_key, salt=None):
    keyed = dict(payload, _salt=salt) if salt is not None else payload
    key = cache_key("bfl", endpoint, keyed) if cache_enabled() else None
    if key:
        hit = cache_get(key)
        if hit is not None:
            record_call("bfl", endpoint, cached=True)
            print("[strange-pets] cache hit for {}".format(endpoint))
            return hit, cache_get_meta(key).get("expanded_prompt", "")

    task_id, polling_url = submit_task(endpoint, payload, api_key)
    result = poll_result(task_id, polling_url, api_key)
    image = download_image(result["sample"])
    expanded = result.get("prompt") or ""
    if not isinstance(expanded, str):
        expanded = json.dumps(expanded)
    if result.get("duration") is not None:
        print("[strange-pets] {} task {} took {}s".format(endpoint, task_id, result["duration"]))
    if key:
        cache_put(key, image, {"provider": "bfl", "endpoint": endpoint,
                               "seed": payload.get("seed"), "prompt": payload.get("prompt", "")[:500],
                               "expanded_prompt": expanded})
    return image, expanded


def dimension(low):
    return ("INT", {
        "default": 0, "min": 0, "max": MAX_DIMENSION, "step": 1,
        "tooltip": "0 leaves it unset. Set width and height together, [{}, {}] each; together "
                   "they override resolution and aspect_ratio.".format(low, MAX_DIMENSION),
    })


def common_widgets(prompt_tooltip):
    return {
        "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": prompt_tooltip}),
        "aspect_ratio": (ASPECT_RATIOS, {"default": "auto"}),
    }


def tail_widgets():
    return {
        "safety_tolerance": ("INT", {
            "default": 2, "min": 0, "max": 7, "display": "slider",
            "tooltip": "0 is the strictest. Your route and organisation set the approved "
                       "maximum; an unapproved level comes back as a 422 naming the approved ones.",
        }),
    }


def key_widgets():
    return {
        "image_urls": ("STRING", {
            "multiline": True, "default": "",
            "tooltip": "Extra references, one per line: an http(s) URL or base64. They follow "
                       "the connected image sockets in order, so socket 1 stays image 1.",
        }),
        "user": ("STRING", {
            "default": "",
            "tooltip": "Opaque end-user identifier (1-256 chars). Blank omits it.",
        }),
        "api_key": ("STRING", {
            "default": "", "multiline": False, "password": True,
            "tooltip": "Only used when BFL_API_KEY is unset. Anything typed here is saved "
                       "into the workflow JSON in plain text.",
        }),
    }


def image_socket():
    return {
        "input_image": ("IMAGE", {
            "tooltip": "Reference image 1 (ref_image_0 in box rows) — the image being edited. "
                       "Connect it and another socket appears, up to {}.".format(MAX_IMAGES),
        }),
    }


class Flux3Base:
    ENDPOINT = ""
    CATEGORY = CATEGORY
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "expanded_prompt")
    OUTPUT_TOOLTIPS = ("The generated image.",
                       "result.prompt: the prompt the model actually used after expanding yours, "
                       "including any boxes it added for an edit.")
    FUNCTION = "execute"

    def collect_images(self, kwargs):
        images = []
        for index in range(1, MAX_IMAGES + 1):
            image = kwargs.pop(image_slot_name(index), None)
            if image is not None:
                images.append(tensor_to_base64_png(image))
        leftover = sorted(name for name in kwargs if IMAGE_SLOT_RE.match(name))
        if leftover:
            raise BFLApiError("{} accepts at most {} reference images; disconnect {}.".format(
                self.ENDPOINT, MAX_IMAGES, ", ".join(leftover)))
        images.extend(line.strip() for line in kwargs.pop("image_urls", "").splitlines()
                      if line.strip())
        if len(images) > MAX_IMAGES:
            raise BFLApiError("{} accepts at most {} reference images; got {} (sockets plus "
                              "image_urls).".format(self.ENDPOINT, MAX_IMAGES, len(images)))
        return images

    def base_payload(self, kwargs):
        prompt = kwargs.pop("prompt", "")
        if not prompt.strip():
            raise BFLApiError("{} needs a prompt.".format(self.ENDPOINT))
        payload = {"prompt": prompt}
        images = self.collect_images(kwargs)
        if images:
            payload["images"] = images
        payload["aspect_ratio"] = kwargs.pop("aspect_ratio", "auto")

        width, height = int(kwargs.pop("width", 0)), int(kwargs.pop("height", 0))
        if bool(width) != bool(height):
            raise BFLApiError("Set width and height together, or leave both at 0.")
        if width:
            payload["width"], payload["height"] = width, height
        return payload

    def finish_payload(self, payload, kwargs):
        payload["safety_tolerance"] = int(kwargs.pop("safety_tolerance", 2))
        user = kwargs.pop("user", "").strip()
        if user:
            payload["user"] = user
        return payload


class Flux3Image(Flux3Base):
    ENDPOINT = "flux-3-image"
    DESCRIPTION = ("FLUX 3 Image via the Black Forest Labs API: text-to-image, instruction "
                   "editing, up to 10 references, and bounding-box layouts written into the prompt.")

    @classmethod
    def INPUT_TYPES(cls):
        required = common_widgets(
            "Text-to-image description, or an edit instruction. Refer to references as "
            "\"image 1\", \"image 2\"… or <ref_image_0>… For boxes, feed this from "
            "FLUX 3 Layout Prompt.")
        required.update({
            "resolution": (TIERS + ["match_input"], {
                "default": "1k",
                "tooltip": "Equal-area tier; exact size follows the aspect ratio. match_input "
                           "(edits only) keeps image 1's size, up to 4 MP. Ignored when width "
                           "and height are set.",
            }),
            "width": dimension(256),
            "height": dimension(256),
        })
        required.update(tail_widgets())
        required.update({
            "grounding": ("BOOLEAN", {
                "default": True,
                "tooltip": "Let the prompt be grounded in web and image search. Off disables both.",
            }),
            "version": (["latest"], {
                "default": "latest",
                "tooltip": "Endpoint version. Dated release tags are added to the API as they ship.",
            }),
            "variation": ("INT", {
                "default": 0, "min": 0, "max": 0x7FFFFFFF, "control_after_generate": True,
                "tooltip": "Not sent: flux-3-image has no seed. Changing it forces a fresh "
                           "generation; keeping it lets ComfyUI and the cache reuse the last one.",
            }),
        })
        required.update(key_widgets())
        return {"required": required, "optional": image_socket()}

    def execute(self, **kwargs):
        api_key = resolve_api_key(kwargs.pop("api_key", ""))
        variation = int(kwargs.pop("variation", 0))
        payload = self.build_payload(kwargs)
        return generate_with_prompt(self.ENDPOINT, payload, api_key, salt=variation)

    def build_payload(self, kwargs):
        payload = self.base_payload(kwargs)
        payload["resolution"] = kwargs.pop("resolution", "1k")
        self.finish_payload(payload, kwargs)
        payload["grounding"] = bool(kwargs.pop("grounding", True))
        payload["version"] = kwargs.pop("version", "latest")
        return payload


class Flux3ImageKlein(Flux3Base):
    ENDPOINT = "flux-3-image-klein"
    DESCRIPTION = ("FLUX 3 Image Klein via the Black Forest Labs API: the fast, editing-first "
                   "model. Adds seed, steps and output_format over FLUX 3 Image.")

    @classmethod
    def INPUT_TYPES(cls):
        required = common_widgets(
            "Edit instruction or text-to-image prompt. The API runs Klein's prompt upsampler "
            "for you, so a short instruction is fine.")
        required.update({
            "resolution": (["default"] + TIERS, {
                "default": "default",
                "tooltip": "default omits it: 1k for text-to-image, image 1's own size for "
                           "edits (its nearest tier when aspect_ratio is set). Ignored when "
                           "width and height are set.",
            }),
            "width": dimension(64),
            "height": dimension(64),
            "seed": ("INT", {
                "default": -1, "min": -1, "max": SEED_MAX, "control_after_generate": True,
                "tooltip": "-1 draws a fresh random seed for each run.",
            }),
            "steps": ("INT", {"default": 16, "min": 1, "max": 16, "tooltip": "Denoising steps."}),
        })
        required.update(tail_widgets())
        required["output_format"] = (["jpeg", "png"], {"default": "jpeg"})
        required.update(key_widgets())
        return {"required": required, "optional": image_socket()}

    def execute(self, **kwargs):
        api_key = resolve_api_key(kwargs.pop("api_key", ""))
        payload = self.build_payload(kwargs)
        return generate_with_prompt(self.ENDPOINT, payload, api_key)

    def build_payload(self, kwargs):
        payload = self.base_payload(kwargs)
        resolution = kwargs.pop("resolution", "default")
        if resolution != "default":
            payload["resolution"] = resolution
        payload["seed"] = resolve_seed(kwargs.pop("seed", -1))
        payload["steps"] = int(kwargs.pop("steps", 16))
        self.finish_payload(payload, kwargs)
        payload["output_format"] = kwargs.pop("output_format", "jpeg")
        return payload


def grid(label):
    return ("INT", {"default": 0, "min": 0, "max": 1000, "step": 1, "tooltip": label})


def check_box(element_id, box):
    top, left, bottom, right = box
    if top >= bottom or left >= right:
        raise BFLApiError("Box for {} is empty: [top, left, bottom, right] = {} needs top < "
                          "bottom and left < right.".format(element_id, box))
    return box


class Flux3BoxElement:
    CATEGORY = CATEGORY
    RETURN_TYPES = ("FLUX3_BOXES",)
    RETURN_NAMES = ("rows",)
    FUNCTION = "build"
    DESCRIPTION = ("One row of a FLUX 3 element table. Chain these, then feed FLUX 3 Layout "
                   "Prompt. Boxes are [top, left, bottom, right] on a 0-1000 grid that "
                   "stretches with the frame. No API call.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "element_id": ("STRING", {
                    "default": "subject_1",
                    "tooltip": "Lowercase name plus a number; mention it as <subject_1> in the caption.",
                }),
                "kind": (BOX_KINDS, {
                    "default": "layout",
                    "tooltip": "layout: place it in a new image. Edits — keep: anchor it in place; "
                               "move: from the box to the to_ box; new: generate desc in the box "
                               "(add, replace, recolor); remove: erase what is in the box.",
                }),
                "top": grid("Box top. For move this is where it is now."),
                "left": grid("Box left."),
                "bottom": grid("Box bottom."),
                "right": grid("Box right."),
                "desc": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "What the element looks like — after the edit, for edits. Quote "
                               "exact text: text reading \"Sauna\".",
                }),
                "ref_image": ("INT", {
                    "default": 0, "min": 0, "max": MAX_IMAGES - 1,
                    "tooltip": "keep/move/remove: which reference it comes from (0 = image 1).",
                }),
                "to_top": grid("move only: target box top."),
                "to_left": grid("move only: target box left."),
                "to_bottom": grid("move only: target box bottom."),
                "to_right": grid("move only: target box right."),
            },
            "optional": {
                "rows": ("FLUX3_BOXES", {"tooltip": "Rows from earlier Box Element nodes."}),
            },
        }

    def build(self, element_id, kind, top, left, bottom, right, desc, ref_image,
              to_top, to_left, to_bottom, to_right, rows=None):
        element_id = element_id.strip()
        if not BOX_ID_RE.match(element_id):
            raise BFLApiError("element_id {!r} should be letters, digits and underscores, "
                              "e.g. subject_1.".format(element_id))
        box = check_box(element_id, [top, left, bottom, right])
        source = "ref_image_{}".format(ref_image)
        if kind == "layout":
            row = {"id": element_id, "bbox": box, "desc": desc}
        elif kind == "keep":
            row = {"id": element_id, "from": source, "src_bbox": box, "tgt_bbox": list(box), "desc": desc}
        elif kind == "move":
            target = check_box(element_id, [to_top, to_left, to_bottom, to_right])
            row = {"id": element_id, "from": source, "src_bbox": box, "tgt_bbox": target, "desc": desc}
        elif kind == "new":
            row = {"id": element_id, "from": None, "src_bbox": None, "tgt_bbox": box, "desc": desc}
        else:
            row = {"id": element_id, "from": source, "src_bbox": box, "tgt_bbox": None, "desc": desc}
        return (list(rows or []) + [row],)


class Flux3LayoutPrompt:
    CATEGORY = CATEGORY
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "join"
    DESCRIPTION = ("Joins a caption (or edit instruction) and its element rows into the single "
                   "prompt string FLUX 3 reads boxes from. No API call.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "caption": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "One paragraph naming each element as <id>. For edits, the "
                               "instruction: \"In <ref_image_0>, move <knight_1>… keep <cliff_1> unchanged.\"",
                }),
                "rows_json": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Optional JSON array of rows, appended after any wired rows — "
                               "paste one from a docs example or a previous expanded_prompt.",
                }),
            },
            "optional": {
                "rows": ("FLUX3_BOXES",),
            },
        }

    def join(self, caption, rows_json, rows=None):
        table = list(rows or [])
        if rows_json.strip():
            try:
                pasted = json.loads(rows_json)
            except ValueError as exc:
                raise BFLApiError("rows_json is not valid JSON: {}".format(exc)) from exc
            if not isinstance(pasted, list):
                raise BFLApiError("rows_json must be a JSON array of rows.")
            table.extend(pasted)
        caption = caption.strip()
        if not table:
            return (caption,)
        missing = [row.get("id") for row in table
                   if isinstance(row, dict) and "<{}>".format(row.get("id")) not in caption]
        if missing:
            print("[strange-pets] FLUX 3 layout: caption never mentions {}. The docs advise "
                  "naming each element in the caption as well as the table.".format(
                      ", ".join("<{}>".format(m) for m in missing)))
        return ("{} {}".format(caption, json.dumps(table)).strip(),)


NODE_CLASS_MAPPINGS = {
    "BFLFlux3Image": Flux3Image,
    "BFLFlux3ImageKlein": Flux3ImageKlein,
    "BFLFlux3BoxElement": Flux3BoxElement,
    "BFLFlux3LayoutPrompt": Flux3LayoutPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BFLFlux3Image": "FLUX 3 Image (BFL API)",
    "BFLFlux3ImageKlein": "FLUX 3 Image Klein (BFL API)",
    "BFLFlux3BoxElement": "FLUX 3 Box Element",
    "BFLFlux3LayoutPrompt": "FLUX 3 Layout Prompt",
}
