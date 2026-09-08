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

CATEGORY = "strange-pets/StabAI/Control"


def _control_inputs(image_tooltip):
    return {
        "required": {
            "image": ("IMAGE", {"tooltip": image_tooltip}),
            "prompt": prompt_widget(),
            "control_strength": float_widget(
                0.7, 0.0, 1.0,
                tooltip="How closely the result follows the control image."),
            "negative_prompt": negative_prompt_widget(),
            "seed": seed_widget(),
            "output_format": output_format_widget(),
            "style_preset": style_preset_widget(),
            "api_key": api_key_widget(),
        }
    }


class StabilityControlSketch(StabilityNode):
    ENDPOINT = "control/sketch"
    CATEGORY = CATEGORY
    DESCRIPTION = "Turn a sketch or line drawing into a finished image."

    @classmethod
    def INPUT_TYPES(cls):
        return _control_inputs("The sketch or line art to follow.")


class StabilityControlStructure(StabilityNode):
    ENDPOINT = "control/structure"
    CATEGORY = CATEGORY
    DESCRIPTION = "Generate an image that follows the structure of a reference image."

    @classmethod
    def INPUT_TYPES(cls):
        return _control_inputs("The structural reference to follow.")


class StabilityControlStyle(StabilityNode):
    ENDPOINT = "control/style"
    CATEGORY = CATEGORY
    DESCRIPTION = "Generate a new image in the style of a reference image."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "The style reference."}),
                "prompt": prompt_widget(),
                "negative_prompt": negative_prompt_widget(),
                "aspect_ratio": aspect_ratio_widget(),
                "fidelity": float_widget(
                    0.5, 0.0, 1.0, tooltip="How closely the output matches the reference style."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityControlStyleTransfer(StabilityNode):
    ENDPOINT = "control/style-transfer"
    IMAGE_FIELDS = ("init_image", "style_image")
    CATEGORY = CATEGORY
    DESCRIPTION = "Apply the style of one image to the composition of another."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": prompt_widget(tooltip="Optional. Leave empty to let the images speak."),
                "negative_prompt": negative_prompt_widget(),
                "style_strength": float_widget(1.0, 0.0, 1.0),
                "composition_fidelity": float_widget(0.9, 0.0, 1.0),
                "change_strength": float_widget(0.9, 0.1, 1.0),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            },
            "optional": {
                "init_image": ("IMAGE", {"tooltip": "Required. The composition to preserve."}),
                "style_image": ("IMAGE", {"tooltip": "Required. The style to apply."}),
            },
        }

    def validate(self, kwargs):
        missing = [name for name in ("init_image", "style_image") if kwargs.get(name) is None]
        if missing:
            raise StabilityApiError(
                "control/style-transfer requires both init_image and style_image; "
                "not connected: {}.".format(", ".join(missing))
            )
