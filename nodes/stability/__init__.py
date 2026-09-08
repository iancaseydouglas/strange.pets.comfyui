from .control import (
    StabilityControlSketch,
    StabilityControlStructure,
    StabilityControlStyle,
    StabilityControlStyleTransfer,
)
from .edit import (
    StabilityEditErase,
    StabilityEditInpaint,
    StabilityEditOutpaint,
    StabilityEditRemoveBackground,
    StabilityEditReplaceBackgroundAndRelight,
    StabilityEditSearchAndRecolor,
    StabilityEditSearchAndReplace,
)
from .generate import StabilityGenerateCore, StabilityGenerateSD3, StabilityGenerateUltra
from .upscale import StabilityUpscaleConservative, StabilityUpscaleCreative, StabilityUpscaleFast

NODE_CLASS_MAPPINGS = {
    "StabAIGenerateUltra": StabilityGenerateUltra,
    "StabAIGenerateCore": StabilityGenerateCore,
    "StabAIGenerateSD3": StabilityGenerateSD3,
    "StabAIEditErase": StabilityEditErase,
    "StabAIEditInpaint": StabilityEditInpaint,
    "StabAIEditOutpaint": StabilityEditOutpaint,
    "StabAIEditSearchAndReplace": StabilityEditSearchAndReplace,
    "StabAIEditSearchAndRecolor": StabilityEditSearchAndRecolor,
    "StabAIEditRemoveBackground": StabilityEditRemoveBackground,
    "StabAIEditReplaceBackgroundAndRelight": StabilityEditReplaceBackgroundAndRelight,
    "StabAIUpscaleFast": StabilityUpscaleFast,
    "StabAIUpscaleConservative": StabilityUpscaleConservative,
    "StabAIUpscaleCreative": StabilityUpscaleCreative,
    "StabAIControlSketch": StabilityControlSketch,
    "StabAIControlStructure": StabilityControlStructure,
    "StabAIControlStyle": StabilityControlStyle,
    "StabAIControlStyleTransfer": StabilityControlStyleTransfer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StabAIGenerateUltra": "Stability Generate Ultra",
    "StabAIGenerateCore": "Stability Generate Core",
    "StabAIGenerateSD3": "Stability Generate SD3.5",
    "StabAIEditErase": "Stability Erase",
    "StabAIEditInpaint": "Stability Inpaint",
    "StabAIEditOutpaint": "Stability Outpaint",
    "StabAIEditSearchAndReplace": "Stability Search & Replace",
    "StabAIEditSearchAndRecolor": "Stability Search & Recolor",
    "StabAIEditRemoveBackground": "Stability Remove Background",
    "StabAIEditReplaceBackgroundAndRelight": "Stability Replace BG & Relight (async)",
    "StabAIUpscaleFast": "Stability Upscale Fast",
    "StabAIUpscaleConservative": "Stability Upscale Conservative",
    "StabAIUpscaleCreative": "Stability Upscale Creative (async)",
    "StabAIControlSketch": "Stability Control Sketch",
    "StabAIControlStructure": "Stability Control Structure",
    "StabAIControlStyle": "Stability Control Style",
    "StabAIControlStyleTransfer": "Stability Style Transfer",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
