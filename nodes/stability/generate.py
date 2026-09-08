from .base import (
    StabilityApiError,
    StabilityNode,
    api_key_widget,
    aspect_ratio_widget,
    float_widget,
    negative_prompt_widget,
    output_format_widget,
    prompt_widget,
    seed_widget,
    style_preset_widget,
)

CATEGORY = "strange-pets/StabAI/Generate"


class StabilityGenerateUltra(StabilityNode):
    ENDPOINT = "generate/ultra"
    CATEGORY = CATEGORY
    DESCRIPTION = "Stable Image Ultra. Text-to-image, or image-to-image when the image socket is connected."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": prompt_widget(),
                "negative_prompt": negative_prompt_widget(),
                "aspect_ratio": aspect_ratio_widget(),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "strength": float_widget(
                    0.5, 0.0, 1.0,
                    tooltip="Only sent when image is connected. 0 returns the input unchanged, "
                            "1 ignores it entirely.",
                ),
                "api_key": api_key_widget(),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "Connect for image-to-image; leave empty for text-to-image."}),
            },
        }

    def validate(self, kwargs):
        if kwargs.get("image") is None:
            kwargs.pop("strength", None)


class StabilityGenerateCore(StabilityNode):
    ENDPOINT = "generate/core"
    IMAGE_FIELDS = ()
    CATEGORY = CATEGORY
    DESCRIPTION = "Stable Image Core. Fast, affordable text-to-image; no image input."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": prompt_widget(),
                "negative_prompt": negative_prompt_widget(),
                "aspect_ratio": aspect_ratio_widget(),
                "seed": seed_widget(),
                "style_preset": style_preset_widget(),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityGenerateSD3(StabilityNode):
    ENDPOINT = "generate/sd3"
    CATEGORY = CATEGORY
    DESCRIPTION = "Stable Diffusion 3.5. Text-to-image or image-to-image, selected by the mode widget."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": prompt_widget(),
                "mode": (["text-to-image", "image-to-image"], {"default": "text-to-image"}),
                "model": (
                    ["sd3.5-large", "sd3.5-large-turbo", "sd3.5-medium"],
                    {"default": "sd3.5-large",
                     "tooltip": "Credit cost differs per model; check the current pricing page. "
                                "Turbo ignores cfg_scale's default of 4 and behaves best near 1."},
                ),
                "negative_prompt": negative_prompt_widget(),
                "aspect_ratio": aspect_ratio_widget(),
                "cfg_scale": float_widget(
                    4.0, 1.0, 10.0, step=0.1,
                    tooltip="Prompt adherence. The API documents 4 for large/medium and 1 for turbo.",
                ),
                "strength": float_widget(
                    0.5, 0.0, 1.0,
                    tooltip="Only used in image-to-image mode.",
                ),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "Required when mode is image-to-image."}),
            },
        }

    def validate(self, kwargs):
        if kwargs.get("mode") == "image-to-image":
            if kwargs.get("image") is None:
                raise StabilityApiError(
                    "generate/sd3 is set to image-to-image but no image is connected. Connect an "
                    "image, or switch mode to text-to-image."
                )
            # aspect_ratio is documented as valid only for text-to-image.
            kwargs.pop("aspect_ratio", None)
        else:
            kwargs.pop("image", None)
            kwargs.pop("strength", None)
