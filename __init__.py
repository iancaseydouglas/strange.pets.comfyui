from .nodes.flux2 import Flux2Flex, Flux2Klein4B, Flux2Klein9B, Flux2Max, Flux2Pro

NODE_CLASS_MAPPINGS = {
    "BFLFlux2Pro": Flux2Pro,
    "BFLFlux2Max": Flux2Max,
    "BFLFlux2Flex": Flux2Flex,
    "BFLFlux2Klein9B": Flux2Klein9B,
    "BFLFlux2Klein4B": Flux2Klein4B,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BFLFlux2Pro": "FLUX.2 [pro] (BFL API)",
    "BFLFlux2Max": "FLUX.2 [max] (BFL API)",
    "BFLFlux2Flex": "FLUX.2 [flex] (BFL API)",
    "BFLFlux2Klein9B": "FLUX.2 [klein] 9B (BFL API)",
    "BFLFlux2Klein4B": "FLUX.2 [klein] 4B (BFL API)",
}

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
