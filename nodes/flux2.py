from .base import Flux2ApiNode

DISABLE_PUP = {
    "disable_pup": (
        "BOOLEAN",
        {
            "default": False,
            "tooltip": "FLUX.2 [pro] and [max] upsample the prompt by default; enable this to use the "
            "prompt exactly as written.",
        },
    ),
}

FLEX_EXTRAS = {
    "prompt_upsampling": (
        "BOOLEAN",
        {"default": True, "tooltip": "Let the API rewrite the prompt before generating."},
    ),
    "steps": ("INT", {"default": 50, "min": 1, "max": 50}),
    "guidance": (
        "FLOAT",
        {
            "default": 5.0,
            "min": 1.5,
            "max": 10.0,
            "step": 0.1,
            "tooltip": "High guidance improves prompt adherence at the cost of realism.",
        },
    ),
}


class Flux2Pro(Flux2ApiNode):
    ENDPOINT = "flux-2-pro"
    MAX_IMAGES = 8
    EXTRA_FIELDS = ("disable_pup",)
    DESCRIPTION = "FLUX.2 [pro] via the Black Forest Labs API. Text-to-image and editing with up to 8 reference images."

    @classmethod
    def INPUT_TYPES(cls):
        return cls.build_input_types(DISABLE_PUP)


class Flux2Max(Flux2ApiNode):
    ENDPOINT = "flux-2-max"
    MAX_IMAGES = 8
    EXTRA_FIELDS = ("disable_pup",)
    DESCRIPTION = "FLUX.2 [max] via the Black Forest Labs API. Text-to-image and editing with up to 8 reference images."

    @classmethod
    def INPUT_TYPES(cls):
        return cls.build_input_types(DISABLE_PUP)


class Flux2Flex(Flux2ApiNode):
    ENDPOINT = "flux-2-flex"
    MAX_IMAGES = 8
    EXTRA_FIELDS = ("prompt_upsampling", "steps", "guidance")
    DESCRIPTION = "FLUX.2 [flex] via the Black Forest Labs API. Adds steps and guidance control on top of [pro]."

    @classmethod
    def INPUT_TYPES(cls):
        return cls.build_input_types(FLEX_EXTRAS)


class Flux2Klein9B(Flux2ApiNode):
    ENDPOINT = "flux-2-klein-9b"
    MAX_IMAGES = 4
    DESCRIPTION = "FLUX.2 [klein] 9B via the Black Forest Labs API. Up to 4 reference images."

    @classmethod
    def INPUT_TYPES(cls):
        return cls.build_input_types()


class Flux2Klein4B(Flux2ApiNode):
    ENDPOINT = "flux-2-klein-4b"
    MAX_IMAGES = 4
    DESCRIPTION = "FLUX.2 [klein] 4B via the Black Forest Labs API. Up to 4 reference images."

    @classmethod
    def INPUT_TYPES(cls):
        return cls.build_input_types()
