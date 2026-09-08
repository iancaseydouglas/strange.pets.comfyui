import os
from concurrent.futures import ThreadPoolExecutor

from .base import (
    BFLApiError,
    apply_dimensions,
    dimension_widget,
    generate,
    list_input_images,
    load_image_file,
    resolve_api_key,
    resolve_path,
    resolve_seed,
    save_tensor_to_dir,
    tensor_to_base64_png,
)
from .flux2 import Flux2Flex, Flux2Klein4B, Flux2Klein9B, Flux2Max, Flux2Pro

MODELS = {
    cls.ENDPOINT: cls for cls in (Flux2Pro, Flux2Max, Flux2Flex, Flux2Klein9B, Flux2Klein4B)
}
SWEEP_PARAMS = ["none", "seed", "steps", "guidance", "safety_tolerance", "width", "height"]
FLOAT_PARAMS = {"guidance"}
MAX_BATCH = 100


def progress_bar(total):
    try:
        from comfy.utils import ProgressBar

        return ProgressBar(total)
    except Exception:
        return None


def parse_sweep(param, spec):
    """Parse a sweep spec into a list of values.

    Forms: "3,5,7" (explicit list), "1.5..8" or "1.5..8:0.5" (inclusive range
    with optional step), or a single "5". guidance parses as float, all others int.
    """
    spec = (spec or "").strip()
    if param == "none" or not spec:
        return []
    cast = float if param in FLOAT_PARAMS else int

    def parse_one(text):
        try:
            return cast(text.strip())
        except ValueError as exc:
            raise BFLApiError("Could not parse {!r} in sweep_spec as a {}.".format(text, cast.__name__)) from exc

    if "," in spec:
        return [parse_one(part) for part in spec.split(",") if part.strip()]
    if ".." in spec:
        span, _, step_text = spec.partition(":")
        lo_text, _, hi_text = span.partition("..")
        lo, hi = parse_one(lo_text), parse_one(hi_text)
        step = parse_one(step_text) if step_text.strip() else (0.5 if cast is float else 1)
        if step <= 0:
            raise BFLApiError("sweep_spec step must be positive, got {}.".format(step))
        if hi < lo:
            lo, hi = hi, lo
        count = int(round((hi - lo) / step))
        values = []
        for i in range(count + 1):
            value = lo + step * i
            values.append(round(value, 6) if cast is float else int(round(value)))
        if values and values[-1] != hi and abs(hi - values[-1]) > (step / 2 if cast is float else 0):
            values.append(hi)
        return values
    return [parse_one(spec)]


class Flux2LoadImageFromPath:
    CATEGORY = "strange-pets/BFL/FLUX.2"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "load"
    DESCRIPTION = "Load an image straight from a local file path (or the first match of a directory/glob), bypassing ComfyUI's input-folder dropdown."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "path": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Absolute path, or relative to the ComfyUI input directory. "
                        "A directory or glob resolves to its first match.",
                    },
                ),
            }
        }

    @classmethod
    def IS_CHANGED(cls, path):
        resolved = resolve_path(path, "input")
        try:
            return os.path.getmtime(resolved)
        except OSError:
            return path

    def load(self, path):
        files = list_input_images(path)
        return (load_image_file(files[0]),)


class Flux2BatchVariations:
    CATEGORY = "strange-pets/BFL/FLUX.2"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "execute"
    DESCRIPTION = (
        "Batch-generate variations of a local image (or a whole folder of them), optionally sweeping "
        "one parameter across a range. Outputs a list of images and can write each to an output directory."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (list(MODELS), {"default": "flux-2-pro"}),
                "input_path": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Base image: a file, a directory, or a glob (each file becomes its "
                        "own base). Relative paths resolve under the ComfyUI input directory. Leave "
                        "empty for text-to-image. Ignored when the input_image socket is connected.",
                    },
                ),
                "output_dir": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Where to write each result (created if missing). Relative paths "
                        "resolve under the ComfyUI output directory. Leave empty to only pass images "
                        "downstream without writing files.",
                    },
                ),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "sweep_param": (
                    SWEEP_PARAMS,
                    {
                        "default": "none",
                        "tooltip": "Parameter to vary across the batch. steps/guidance require flux-2-flex.",
                    },
                ),
                "sweep_spec": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "Values for the swept parameter: a list '3,5,7', an inclusive range "
                        "'1.5..8' or '1.5..8:0.5' (with step). Ignored when sweep_param is none.",
                    },
                ),
                "parallel": (
                    "INT",
                    {
                        "default": 1, "min": 1, "max": 8,
                        "tooltip": "How many API calls to keep in flight at once. The calls are "
                                   "network-bound, so 3-5 cuts wall-clock time roughly that much. "
                                   "Results keep their order regardless.",
                    },
                ),
                "variations": (
                    "INT",
                    {
                        "default": 4,
                        "min": 1,
                        "max": MAX_BATCH,
                        "tooltip": "Images per swept value (or total, when sweep_param is none). With "
                        "seed = -1 each gets a fresh random seed.",
                    },
                ),
                "seed": (
                    "INT",
                    {
                        "default": -1,
                        "min": -1,
                        "max": 0x7FFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "-1 draws a fresh random seed per image. Fix it to hold the seed "
                        "constant while sweeping another parameter.",
                    },
                ),
                "width": dimension_widget("width"),
                "height": dimension_widget("height"),
                "safety_tolerance": (
                    "INT",
                    {"default": 2, "min": 0, "max": 5, "display": "slider"},
                ),
                "output_format": (["jpeg", "png", "webp"], {"default": "png"}),
                "disable_pup": (
                    "BOOLEAN",
                    {"default": False, "tooltip": "pro/max only: use the prompt exactly as written."},
                ),
                "prompt_upsampling": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "flex only: let the API rewrite the prompt."},
                ),
                "steps": ("INT", {"default": 50, "min": 1, "max": 50, "tooltip": "flex only."}),
                "guidance": (
                    "FLOAT",
                    {"default": 5.0, "min": 1.5, "max": 10.0, "step": 0.1, "tooltip": "flex only."},
                ),
                "api_key": (
                    "STRING",
                    {"default": "", "multiline": False, "password": True},
                ),
            },
            "optional": {
                "input_image": (
                    "IMAGE",
                    {"tooltip": "Optional upstream base image. Overrides input_path when connected."},
                ),
            },
        }

    def execute(self, model, input_path, output_dir, prompt, sweep_param, sweep_spec, parallel,
                variations, seed, width, height, safety_tolerance, output_format, disable_pup,
                prompt_upsampling, steps, guidance, api_key, input_image=None):
        api_key = resolve_api_key(api_key)
        cls = MODELS[model]
        endpoint, extra_fields = cls.ENDPOINT, cls.EXTRA_FIELDS

        sweepable = {"seed", "width", "height", "safety_tolerance"} | (
            {"steps", "guidance"} & set(extra_fields)
        )
        if sweep_param != "none" and sweep_param not in sweepable:
            raise BFLApiError(
                "{} has no {!r} parameter to sweep. steps and guidance are flux-2-flex only; "
                "this model can sweep {}.".format(model, sweep_param, ", ".join(sorted(sweepable)))
            )

        values = parse_sweep(sweep_param, sweep_spec)
        if sweep_param != "none" and not values:
            raise BFLApiError("sweep_param is {!r} but sweep_spec is empty.".format(sweep_param))
        if sweep_param == "none":
            values = [None]

        variations = max(1, int(variations))

        if input_image is not None:
            bases = [("input", input_image)]
        elif input_path.strip():
            bases = [
                (os.path.splitext(os.path.basename(path))[0], load_image_file(path))
                for path in list_input_images(input_path)
            ]
        else:
            bases = [("txt2img", None)]

        total = len(bases) * len(values) * variations
        if total > MAX_BATCH:
            raise BFLApiError(
                "This batch would make {} API calls (>{}). Narrow the sweep, lower variations, or "
                "reduce the number of input images. Each call costs credits.".format(total, MAX_BATCH)
            )

        out_dir = resolve_path(output_dir, "output") if output_dir.strip() else ""
        bar = progress_bar(total)
        jobs = []

        for base_name, base_tensor in bases:
            base_b64 = tensor_to_base64_png(base_tensor) if base_tensor is not None else None
            for value in values:
                current = {
                    "seed": seed, "width": width, "height": height,
                    "safety_tolerance": safety_tolerance, "steps": steps, "guidance": guidance,
                }
                if sweep_param != "none":
                    current[sweep_param] = value
                for _ in range(variations):
                    payload = {
                        "prompt": prompt,
                        "seed": resolve_seed(current["seed"]),
                        "safety_tolerance": int(current["safety_tolerance"]),
                        "output_format": output_format,
                    }
                    apply_dimensions(payload, current["width"], current["height"])
                    for field in extra_fields:
                        if field == "disable_pup":
                            payload[field] = bool(disable_pup)
                        elif field == "prompt_upsampling":
                            payload[field] = bool(prompt_upsampling)
                        elif field == "steps":
                            payload[field] = int(current["steps"])
                        elif field == "guidance":
                            payload[field] = float(current["guidance"])
                    if base_b64 is not None:
                        payload["input_image"] = base_b64

                    parts = [model]
                    if len(bases) > 1:
                        parts.append(base_name)
                    if sweep_param != "none":
                        parts.append("{}-{}".format(sweep_param, value))
                    parts.append("seed{}".format(payload["seed"]))
                    jobs.append(("_".join(str(part) for part in parts), payload))

        lanes = max(1, min(int(parallel), len(jobs)))
        print("[strange-pets] batch of {} on {}, {} at a time".format(total, endpoint, lanes))

        def run_job(job):
            stem, payload = job
            return generate(endpoint, payload, api_key)

        if lanes == 1:
            images = []
            for index, job in enumerate(jobs):
                print("[BFL batch] {}/{}  {}".format(index + 1, total, job[0]))
                images.append(run_job(job))
                if bar is not None:
                    bar.update(1)
        else:
            # Calls are I/O-bound, so threads overlap the waiting. executor.map keeps
            # results in submission order, so the grid stays aligned with the sweep.
            with ThreadPoolExecutor(max_workers=lanes) as pool:
                futures = [pool.submit(run_job, job) for job in jobs]
                images = []
                for index, future in enumerate(futures):
                    images.append(future.result())
                    print("[BFL batch] {}/{}  {}".format(index + 1, total, jobs[index][0]))
                    if bar is not None:
                        bar.update(1)

        results, manifest = [], []
        for index, (image, (stem, _payload)) in enumerate(zip(images, jobs)):
            results.append(image)
            if out_dir:
                manifest.append(save_tensor_to_dir(
                    image, out_dir, "{}_{:03d}".format(stem, index), output_format))

        if out_dir and manifest:
            with open(os.path.join(out_dir, "manifest.txt"), "w") as handle:
                handle.write("\n".join(manifest) + "\n")

        return (results,)
