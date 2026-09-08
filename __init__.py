from .nodes.batch import Flux2BatchVariations, Flux2LoadImageFromPath
from .nodes.flux2 import Flux2Flex, Flux2Klein4B, Flux2Klein9B, Flux2Max, Flux2Pro
from .nodes.stability import NODE_CLASS_MAPPINGS as STABILITY_CLASSES
from .nodes.stability import NODE_DISPLAY_NAME_MAPPINGS as STABILITY_NAMES
from .nodes.utils import NODE_CLASS_MAPPINGS as UTIL_CLASSES
from .nodes.utils import NODE_DISPLAY_NAME_MAPPINGS as UTIL_NAMES

NODE_CLASS_MAPPINGS = {
    "BFLFlux2Pro": Flux2Pro,
    "BFLFlux2Max": Flux2Max,
    "BFLFlux2Flex": Flux2Flex,
    "BFLFlux2Klein9B": Flux2Klein9B,
    "BFLFlux2Klein4B": Flux2Klein4B,
    "BFLFlux2LoadImageFromPath": Flux2LoadImageFromPath,
    "BFLFlux2BatchVariations": Flux2BatchVariations,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BFLFlux2Pro": "FLUX.2 [pro] (BFL API)",
    "BFLFlux2Max": "FLUX.2 [max] (BFL API)",
    "BFLFlux2Flex": "FLUX.2 [flex] (BFL API)",
    "BFLFlux2Klein9B": "FLUX.2 [klein] 9B (BFL API)",
    "BFLFlux2Klein4B": "FLUX.2 [klein] 4B (BFL API)",
    "BFLFlux2LoadImageFromPath": "Load Image From Path (BFL)",
    "BFLFlux2BatchVariations": "FLUX.2 Batch / Sweep (BFL API)",
}

NODE_CLASS_MAPPINGS.update(STABILITY_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(STABILITY_NAMES)
NODE_CLASS_MAPPINGS.update(UTIL_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(UTIL_NAMES)

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
