from .base import (
    TRANSPARENT_FORMATS,
    StabilityApiError,
    StabilityNode,
    api_key_widget,
    float_widget,
    int_widget,
    negative_prompt_widget,
    output_format_widget,
    prompt_widget,
    seed_widget,
    style_preset_widget,
)

CATEGORY = "strange-pets/StabAI/Edit"

MASK_TOOLTIP = (
    "Optional. White = act on this pixel, black = leave it alone (ComfyUI's MASK polarity "
    "matches Stability's, so no inversion is needed). If left unconnected, the alpha channel "
    "of image is used instead; a connected mask takes precedence."
)


class StabilityEditErase(StabilityNode):
    ENDPOINT = "edit/erase"
    MASK_FIELDS = ("mask",)
    CATEGORY = CATEGORY
    DESCRIPTION = "Remove an object using a mask, or the image's alpha channel when no mask is connected."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "grow_mask": int_widget(5, 0, 20, "Expands the mask edge outward, blurred."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            },
            "optional": {"mask": ("MASK", {"tooltip": MASK_TOOLTIP})},
        }


class StabilityEditInpaint(StabilityNode):
    ENDPOINT = "edit/inpaint"
    MASK_FIELDS = ("mask",)
    CATEGORY = CATEGORY
    DESCRIPTION = "Regenerate a masked region from a prompt."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": prompt_widget(),
                "negative_prompt": negative_prompt_widget(),
                "grow_mask": int_widget(5, 0, 100, "Expands the mask edge outward, blurred."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            },
            "optional": {"mask": ("MASK", {"tooltip": MASK_TOOLTIP})},
        }


class StabilityEditOutpaint(StabilityNode):
    ENDPOINT = "edit/outpaint"
    CATEGORY = CATEGORY
    DESCRIPTION = "Extend an image outward on any side. At least one side must be non-zero."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "left": int_widget(0, 0, 2000, "Pixels to add on this side."),
                "right": int_widget(0, 0, 2000, "Pixels to add on this side."),
                "up": int_widget(0, 0, 2000, "Pixels to add on this side."),
                "down": int_widget(0, 0, 2000, "Pixels to add on this side."),
                "creativity": float_widget(0.5, 0.0, 1.0),
                "prompt": prompt_widget(tooltip="Optional. Leave empty to let the model infer."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            }
        }

    def validate(self, kwargs):
        if not any(kwargs.get(side) for side in ("left", "right", "up", "down")):
            raise StabilityApiError(
                "edit/outpaint needs at least one of left, right, up or down to be non-zero; "
                "all four are 0, so there is nothing to extend."
            )


class StabilityEditSearchAndReplace(StabilityNode):
    ENDPOINT = "edit/search-and-replace"
    CATEGORY = CATEGORY
    DESCRIPTION = "Find an object by description and replace it. No mask needed."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": prompt_widget(tooltip="What to put in place of the found object."),
                "search_prompt": prompt_widget(
                    multiline=False, tooltip="Short description of what to find and replace."),
                "negative_prompt": negative_prompt_widget(),
                "grow_mask": int_widget(3, 0, 20),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityEditSearchAndRecolor(StabilityNode):
    ENDPOINT = "edit/search-and-recolor"
    CATEGORY = CATEGORY
    DESCRIPTION = "Find an object by description and recolour it."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": prompt_widget(tooltip="The new colour or appearance."),
                "select_prompt": prompt_widget(
                    multiline=False, tooltip="What object to find and recolour."),
                "negative_prompt": negative_prompt_widget(),
                "grow_mask": int_widget(3, 0, 20),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityEditRemoveBackground(StabilityNode):
    ENDPOINT = "edit/remove-background"
    CATEGORY = CATEGORY
    DESCRIPTION = "Cut the subject out and return it on transparency."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "output_format": output_format_widget(TRANSPARENT_FORMATS),
                "api_key": api_key_widget(),
            }
        }


class StabilityEditReplaceBackgroundAndRelight(StabilityNode):
    ENDPOINT = "edit/replace-background-and-relight"
    IS_ASYNC = True
    IMAGE_FIELDS = ("subject_image", "background_reference", "light_reference")
    BOOL_FIELDS = ("keep_original_background",)
    CATEGORY = CATEGORY
    DESCRIPTION = "Replace the background and relight the subject. Asynchronous — the node polls until it is ready."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "background_prompt": prompt_widget(tooltip="Description of the background you want."),
                "foreground_prompt": prompt_widget(
                    tooltip="Description of the subject, to stop the background bleeding into it."),
                "negative_prompt": negative_prompt_widget(),
                "preserve_original_subject": float_widget(
                    0.6, 0.0, 1.0, tooltip="1.0 matches the original subject pixel for pixel."),
                "original_background_depth": float_widget(0.5, 0.0, 1.0),
                "keep_original_background": ("BOOLEAN", {"default": False}),
                "light_source_direction": (
                    ["none", "above", "below", "left", "right"], {"default": "none"}),
                "light_source_strength": float_widget(
                    0.3, 0.0, 1.0,
                    tooltip="Only meaningful with light_reference or light_source_direction set."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            },
            "optional": {
                "subject_image": ("IMAGE", {"tooltip": "Required. The subject to keep."}),
                "background_reference": ("IMAGE", {"tooltip": "Optional style reference for the background."}),
                "light_reference": ("IMAGE", {"tooltip": "Optional. Lighter areas mean brighter output lighting."}),
            },
        }

    def validate(self, kwargs):
        if kwargs.get("subject_image") is None:
            raise StabilityApiError(
                "edit/replace-background-and-relight requires subject_image; connect an image to it."
            )
