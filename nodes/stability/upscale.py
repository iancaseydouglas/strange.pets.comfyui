from .base import (
    StabilityNode,
    api_key_widget,
    float_widget,
    negative_prompt_widget,
    output_format_widget,
    prompt_widget,
    seed_widget,
    style_preset_widget,
)

CATEGORY = "strange-pets/StabAI/Upscale"


class StabilityUpscaleFast(StabilityNode):
    ENDPOINT = "upscale/fast"
    CATEGORY = CATEGORY
    DESCRIPTION = "4x upscale in about a second. No prompt, no options beyond the output format."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityUpscaleConservative(StabilityNode):
    ENDPOINT = "upscale/conservative"
    CATEGORY = CATEGORY
    DESCRIPTION = "Upscale to ~4MP while staying close to the original. Synchronous."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": prompt_widget(tooltip="Required. Describe the image you are upscaling."),
                "negative_prompt": negative_prompt_widget(),
                "creativity": float_widget(
                    0.35, 0.2, 0.5, step=0.01,
                    tooltip="Likelihood of inventing detail the input does not strongly imply."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "api_key": api_key_widget(),
            }
        }


class StabilityUpscaleCreative(StabilityNode):
    ENDPOINT = "upscale/creative"
    IS_ASYNC = True
    CATEGORY = CATEGORY
    DESCRIPTION = "Heavily reimagines degraded or low-res input while upscaling. Asynchronous — the node polls until it is ready."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "prompt": prompt_widget(tooltip="Required. Describe the image you are upscaling."),
                "negative_prompt": negative_prompt_widget(),
                "creativity": float_widget(
                    0.3, 0.1, 0.5, step=0.01, tooltip="Higher adds more invented detail."),
                "seed": seed_widget(),
                "output_format": output_format_widget(),
                "style_preset": style_preset_widget(),
                "api_key": api_key_widget(),
            }
        }
