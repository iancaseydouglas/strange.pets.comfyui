"""The style cascade: deck-level colour rules, a base style lock, scoped layers over any
subset of the deck, and the resolver that turns the stack into one prompt and one reference
plate per card."""

import copy
import glob
import json
import math
import os

import torch
from PIL import Image

from .common import (
    IMAGE_EXTS,
    NodeError,
    load_image_file,
    pil_to_tensor,
    resolve_path,
    tensor_to_pil,
)
from .tarot import (
    DECK_CATEGORY,
    PROOF_SELECTOR,
    palette_swatch,
    parse_hex_codes,
    select_cards,
    slugify,
)

PACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1.0 holds the seed card's version of an aspect; 0.0 lets it change completely.
AXES = ("structure", "style", "colour", "lighting", "iconography")
INHERIT = -1.0

_PHRASE_CACHE = {}


def load_phrases(path=""):
    """The wording for each axis band. Shipped as phrases.json; override with a path.

    Kept as data rather than code because the exact phrasing is the part worth tuning,
    and tuning it should not mean editing Python.
    """
    resolved = resolve_path(path, "input") if (path or "").strip() else \
        os.path.join(PACK_DIR, "phrases.json")
    try:
        stamp = os.path.getmtime(resolved)
    except OSError as exc:
        raise NodeError("No phrase table at {!r}: {}".format(resolved, exc)) from exc
    if _PHRASE_CACHE.get("path") == (resolved, stamp):
        return _PHRASE_CACHE["phrases"]

    try:
        with open(resolved) as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise NodeError("{} is not valid JSON: {}".format(resolved, exc)) from exc

    phrases = {}
    for axis in AXES:
        bands = data.get(axis)
        if not isinstance(bands, list) or not bands:
            raise NodeError("{}: axis {!r} is missing or empty.".format(resolved, axis))
        rows = []
        for band in bands:
            if not isinstance(band, dict) or "text" not in band:
                raise NodeError("{}: every {} band needs 'upto' and 'text'.".format(resolved, axis))
            rows.append((float(band.get("upto", 1.0)), str(band["text"])))
        phrases[axis] = sorted(rows)
    _PHRASE_CACHE["path"] = (resolved, stamp)
    _PHRASE_CACHE["phrases"] = phrases
    return phrases


def phrase_for(phrases, axis, value):
    for upto, text in phrases[axis]:
        if value <= upto:
            return text
    return phrases[axis][-1][1]


def parse_rules(text, label):
    """`selector : payload` per line, ignoring blanks and # comments."""
    rules = []
    for number, line in enumerate(( text or "").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise NodeError(
                "{} line {}: {!r} needs a ':' between the selector and the text — "
                "'the-tower : dominant red, #B3121B'.".format(label, number, line))
        selector, _, payload = line.partition(":")
        selector, payload = selector.strip(), payload.strip()
        if not selector or not payload:
            raise NodeError("{} line {}: {!r} needs both a selector and text.".format(
                label, number, line))
        rules.append({"selector": selector, "text": payload,
                      "hex": parse_hex_codes(payload)})
    return rules


def anchor_paths(directory):
    resolved = resolve_path(directory, "input")
    if not resolved:
        return []
    if not os.path.isdir(resolved):
        raise NodeError("Anchor directory {!r} does not exist.".format(resolved))
    found = sorted(path for path in glob.glob(os.path.join(glob.escape(resolved), "*"))
                   if os.path.isfile(path) and path.lower().endswith(IMAGE_EXTS))
    if not found:
        raise NodeError("No images in the anchor directory {!r}.".format(resolved))
    return found


def build_plate(paths, hexes, width, height, max_tiles):
    """Compose the anchors (and any palette) into one reference image.

    One plate rather than one slot per anchor: it costs a single reference slot, and a
    model shown several cards at once is likelier to read the style they share than to
    copy the one picture it was given.
    """
    tiles = [Image.open(path).convert("RGB") for path in paths[:max_tiles]]
    if hexes:
        tiles.append(palette_swatch(hexes, 512, 512, "grid"))
    if not tiles:
        return None
    columns = min(len(tiles), int(math.ceil(math.sqrt(len(tiles)))) or 1)
    rows = int(math.ceil(len(tiles) / columns))
    cell_w, cell_h = width // columns, height // rows
    plate = Image.new("RGB", (width, height), "#000000")
    for position, tile in enumerate(tiles):
        row, column = divmod(position, columns)
        fitted = tile.copy()
        fitted.thumbnail((cell_w, cell_h), Image.LANCZOS)
        plate.paste(fitted, (column * cell_w + (cell_w - fitted.width) // 2,
                             row * cell_h + (cell_h - fitted.height) // 2))
    return plate


def new_style(text, anchors, axes, max_tiles):
    return {"layers": [{"selector": "all", "name": "deck", "text": text,
                        "hex": parse_hex_codes(text), "anchors": anchors, "axes": axes}],
            "max_tiles": max_tiles}


def match_sets(entries, deck, pip_count):
    """Resolve each selector once, to a set of card indices, rather than per card."""
    resolved = []
    for entry in entries:
        hit = select_cards(entry["selector"], deck, pip_count)
        resolved.append((entry, {card["index"] for card in hit}))
    return resolved


def axis_widgets(inherit):
    """The fidelity vector. On a scope layer, -1 means inherit from the layer before."""
    default = INHERIT if inherit else 1.0
    low = INHERIT if inherit else 0.0
    tips = {
        "structure": "composition, placement, proportion, gesture",
        "style": "line quality, mark-making, surface",
        "colour": "palette, hue, value",
        "lighting": "direction, key, contrast",
        "iconography": "which symbols the card depicts",
    }
    return {
        "hold_" + axis: ("FLOAT", {
            "default": default, "min": low, "max": 1.0, "step": 0.05,
            "tooltip": "{} — 1.0 holds the seed card's, 0.0 lets it change entirely.{}".format(
                tips[axis], " -1 inherits." if inherit else ""),
        }) for axis in AXES
    }


def collect_axes(kwargs):
    return {axis: float(kwargs["hold_" + axis]) for axis in AXES}


class TarotConstraints:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("TAROT_RULES", "STRING")
    RETURN_NAMES = ("constraints", "summary")
    FUNCTION = "build"
    DESCRIPTION = (
        "Invariants that belong to the deck rather than to any one style — the cards that "
        "must always read a certain colour. They travel through every derivation, so no "
        "change of style can drop them."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "rules": ("STRING", {
                    "multiline": True,
                    "default": ("the-tower : dominant red, burning — #B3121B, #7A0E14\n"
                                "the-moon  : cold silver-blue, no warm tones\n"
                                "wands     : warm — amber, ember, brass\n"),
                    "tooltip": "One 'selector : text' per line. Hex codes anywhere in the "
                               "text are read out and rendered into the reference plate. "
                               "# starts a comment.",
                }),
            },
            "optional": {
                "inherit": ("TAROT_RULES", {"tooltip": "Rules from another node, extended here."}),
            },
        }

    def build(self, rules, inherit=None):
        parsed = list(inherit or []) + parse_rules(rules, "Constraints")
        lines = ["{} rule(s):".format(len(parsed))]
        for rule in parsed:
            lines.append("  {:<22} {}{}".format(
                rule["selector"], rule["text"][:60],
                "  [{}]".format(" ".join(rule["hex"])) if rule["hex"] else ""))
        return (parsed, "\n".join(lines))


class TarotStyleLock:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("TAROT_STYLE", "IMAGE", "STRING")
    RETURN_NAMES = ("style", "plate", "summary")
    FUNCTION = "build"
    DESCRIPTION = (
        "The deck's base look, locked as one object: prose, a set of approved anchor cards, "
        "and the fidelity vector. Saves to and loads from disk, so a deck derived a year "
        "from now can be derived in the same style."
    )

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "style_text": ("STRING", {
                "multiline": True,
                "default": ("Gold ink on deep indigo, engraved linework, candlelit, "
                            "symbolist, hand-inked texture."),
                "tooltip": "The one block retuned between whole-deck runs. Hex codes here "
                           "are read out and rendered into the plate.",
            }),
            "anchor_dir": ("STRING", {
                "default": "",
                "tooltip": "A folder of approved cards. Several deliberately unalike ones — "
                           "a major, a pip, a court — beat a single anchor, whose composition "
                           "the whole deck will otherwise start to inherit.",
            }),
            "max_plate_tiles": ("INT", {"default": 4, "min": 1, "max": 12}),
            "plate_width": ("INT", {"default": 1024, "min": 128, "max": 4096}),
            "plate_height": ("INT", {"default": 1024, "min": 128, "max": 4096}),
        }
        inputs.update(axis_widgets(inherit=False))
        inputs.update({
            "save_to": ("STRING", {"default": "", "tooltip": "Write this lock to a JSON file."}),
            "load_from": ("STRING", {
                "default": "",
                "tooltip": "Read a saved lock. Everything above is ignored when set.",
            }),
        })
        return {"required": inputs}

    def build(self, style_text, anchor_dir, max_plate_tiles, plate_width, plate_height,
              save_to, load_from, **axes):
        if load_from.strip():
            path = resolve_path(load_from, "input")
            if not os.path.isfile(path):
                raise NodeError("No style lock at {!r}.".format(path))
            with open(path) as handle:
                style = json.load(handle)
            if "layers" not in style:
                raise NodeError("{} is not a style lock (no 'layers').".format(path))
        else:
            style = new_style(style_text, anchor_dir.strip(), collect_axes(axes),
                              int(max_plate_tiles))
            if save_to.strip():
                target = resolve_path(save_to, "input")
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                with open(target, "w") as handle:
                    json.dump(style, handle, indent=2)
                print("[strange-pets] wrote style lock to {}".format(target))

        base = style["layers"][0]
        paths = anchor_paths(base["anchors"]) if base["anchors"] else []
        plate = build_plate(paths, base["hex"], int(plate_width), int(plate_height),
                            style.get("max_tiles", 4))
        summary = "base style · {} anchor(s) · {}".format(
            len(paths), " ".join("{} {:.2f}".format(a, base["axes"][a]) for a in AXES))
        if base["hex"]:
            summary += "\npalette: " + " ".join(base["hex"])
        blank = torch.zeros((1, 8, 8, 3))
        return (style, pil_to_tensor(plate) if plate else blank, summary)


class TarotStyleScope:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("TAROT_STYLE", "IMAGE", "STRING")
    RETURN_NAMES = ("style", "plate", "summary")
    FUNCTION = "build"
    DESCRIPTION = (
        "One scoped layer on the style cascade — a subset of the deck with its own prose, "
        "its own anchors and its own fidelity. Chain several; the later node wins, so the "
        "cascade is the order you can see on the canvas."
    )

    @classmethod
    def INPUT_TYPES(cls):
        inputs = {
            "style": ("TAROT_STYLE",),
            "selector": ("STRING", {
                "default": "courts suit1",
                "tooltip": "Which cards this layer covers. 'courts wands', 'pips 7', "
                           "'courts !wands', 'majors, aces', 'the-tower'.",
            }),
            "text": ("STRING", {
                "multiline": True,
                "default": "",
                "tooltip": "Added after the layers before it — this refines, it does not "
                           "replace. Hex codes here join this scope's plate.",
            }),
            "anchor_dir": ("STRING", {
                "default": "",
                "tooltip": "Anchors for this subset. They lead the plate and the deck's own "
                           "anchors fill the remaining tiles, so a suit's courts read like "
                           "each other first and like the deck second.",
            }),
        }
        inputs.update(axis_widgets(inherit=True))
        return {"required": inputs}

    def build(self, style, selector, text, anchor_dir, **axes):
        layer = {
            "selector": selector.strip(), "name": selector.strip(),
            "text": text.strip(), "hex": parse_hex_codes(text),
            "anchors": anchor_dir.strip(),
            "axes": {axis: value for axis, value in collect_axes(axes).items()
                     if value >= 0},
        }
        if not layer["selector"]:
            raise NodeError("A style scope needs a selector.")
        if not (layer["text"] or layer["anchors"] or layer["axes"]):
            raise NodeError(
                "Scope {!r} changes nothing — give it text, anchors, or a fidelity "
                "override.".format(layer["selector"]))

        extended = copy.deepcopy(style)
        extended["layers"].append(layer)
        paths = anchor_paths(layer["anchors"]) if layer["anchors"] else []
        plate = build_plate(paths, layer["hex"], 1024, 1024, extended.get("max_tiles", 4))
        overrides = " ".join("{} {:.2f}".format(a, v) for a, v in layer["axes"].items())
        summary = "scope {!r} · {} anchor(s){}".format(
            layer["selector"], len(paths), " · " + overrides if overrides else "")
        blank = torch.zeros((1, 8, 8, 3))
        return (extended, pil_to_tensor(plate) if plate else blank, summary)


def backend_numbers(axes):
    """Map the vector onto what each API actually exposes.

    Stability has real parameters for this. FLUX.2 has only guidance, so the vector
    reaches it through the prompt instead and these numbers are a convenience for
    whichever node you wire them into.
    """
    structure = axes["structure"]
    travel = 1.0 - (structure * 0.5 + axes["colour"] * 0.25 + axes["style"] * 0.25)
    return {
        "guidance": round(3.0 + 5.0 * structure, 2),
        "control_strength": round(structure, 3),
        "composition_fidelity": round(structure, 3),
        "style_strength": round(1.0 - axes["style"], 3),
        "change_strength": round(min(1.0, max(0.1, travel)), 3),
    }


class TarotStyleResolve:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("STRING", "IMAGE", "STRING", "FLOAT", "FLOAT", "FLOAT", "FLOAT", "FLOAT")
    RETURN_NAMES = ("prompts", "plates", "report", "guidance", "control_strength",
                    "style_strength", "composition_fidelity", "change_strength")
    OUTPUT_IS_LIST = (True, True, False, True, True, True, True, True)
    FUNCTION = "resolve"
    DESCRIPTION = (
        "Collapses the cascade to one prompt and one reference plate per card, and reports "
        "which layers each card ended up under. Everything downstream runs once per card."
    )

    TOKENS = "{title} {numeral} {arcana} {suit} {rank} {element} {slug} {index} {style} {constraints}"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "deck": ("TAROT_DECK",),
                "style": ("TAROT_STYLE",),
                "template": ("STRING", {
                    "multiline": True,
                    "default": ("Tarot card '{title}' ({numeral}), {arcana} arcana. {style} "
                                "{constraints}"),
                    "tooltip": "Tokens: " + cls.TOKENS + ". {style} is the resolved cascade "
                               "and {constraints} the deck's invariants, which land last.",
                }),
                "phrases_path": ("STRING", {
                    "default": "",
                    "tooltip": "Override the shipped phrases.json — the wording each "
                               "fidelity band turns into.",
                }),
                "plate_width": ("INT", {"default": 1024, "min": 128, "max": 4096}),
                "plate_height": ("INT", {"default": 1024, "min": 128, "max": 4096}),
                "include_palette": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Render resolved hex codes as a swatch tile on the plate. A "
                               "model follows a swatch far more reliably than hex in prose.",
                }),
                "warn_unscoped": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Flag cards that only ever matched the base layer.",
                }),
            },
            "optional": {
                "constraints": ("TAROT_RULES",),
            },
        }

    def resolve(self, deck, style, template, phrases_path, plate_width, plate_height,
                include_palette, warn_unscoped, constraints=None):
        if not deck:
            raise NodeError("The deck is empty.")
        phrases = load_phrases(phrases_path)
        pip_count = max((c["number"] for c in deck if c["arcana"] == "minor"), default=10)
        pip_count = 10 if pip_count > 10 else pip_count

        layers = match_sets(style["layers"], deck, pip_count)
        rules = match_sets(constraints or [], deck, pip_count)
        max_tiles = style.get("max_tiles", 4)
        plate_cache = {}

        prompts, plates, lines, numbers = [], [], [], {k: [] for k in
                                                       ("guidance", "control_strength",
                                                        "style_strength",
                                                        "composition_fidelity",
                                                        "change_strength")}
        unscoped = []

        for card in deck:
            hits = [layer for layer, indices in layers if card["index"] in indices]
            if not hits:
                raise NodeError(
                    "{} matches no layer at all, not even the base.".format(card["title"]))
            if warn_unscoped and len(hits) == 1:
                unscoped.append(card["title"])

            # Text accumulates in chain order; axes and anchors are overridden by the last
            # layer that names them, so the rightmost node on the canvas is the specific one.
            axes = dict(hits[0]["axes"])
            text_parts, hexes, anchors = [], [], hits[0]["anchors"]
            for layer in hits:
                if layer["text"]:
                    text_parts.append(layer["text"])
                for code in layer["hex"]:
                    if code not in hexes:
                        hexes.append(code)
                if layer["anchors"]:
                    anchors = layer["anchors"]
                axes.update(layer["axes"])

            for axis in AXES:
                text_parts.append(phrase_for(phrases, axis, axes[axis]))

            hit_rules = [rule for rule, indices in rules if card["index"] in indices]
            constraint_text = " ".join(rule["text"] for rule in hit_rules)
            for rule in hit_rules:
                for code in rule["hex"]:
                    if code not in hexes:
                        hexes.append(code)

            values = dict(card)
            values["style"] = " ".join(part.strip() for part in text_parts if part.strip())
            values["constraints"] = (
                "Regardless of style: {}".format(constraint_text) if constraint_text else "")
            try:
                prompts.append(" ".join(template.format(**values).split()))
            except KeyError as exc:
                raise NodeError("Unknown token {} in the template. Available: {}".format(
                    exc, self.TOKENS)) from exc

            key = (anchors, tuple(hexes) if include_palette else ())
            if key not in plate_cache:
                paths = anchor_paths(anchors) if anchors else []
                base_anchors = hits[0]["anchors"]
                if base_anchors and base_anchors != anchors:
                    # Scope anchors lead, the deck's own fill the rest of the plate.
                    for path in anchor_paths(base_anchors):
                        if path not in paths:
                            paths.append(path)
                pil = build_plate(paths, hexes if include_palette else [],
                                  int(plate_width), int(plate_height), max_tiles)
                plate_cache[key] = pil_to_tensor(pil) if pil else torch.zeros((1, 8, 8, 3))
            plates.append(plate_cache[key])

            for name, value in backend_numbers(axes).items():
                numbers[name].append(value)

            lines.append("{:<24} <- {}{}".format(
                card["title"], " | ".join(layer["name"] for layer in hits),
                "  [{}]".format(" ".join(rule["selector"] for rule in hit_rules))
                if hit_rules else ""))

        report = "\n".join(
            ["{} card(s), {} layer(s), {} constraint(s), {} distinct plate(s)".format(
                len(deck), len(style["layers"]), len(rules), len(plate_cache)), ""] + lines)
        if unscoped:
            report += "\n\nonly the base layer ({}): {}".format(
                len(unscoped), ", ".join(unscoped))
        print("[strange-pets]\n" + report)
        return (prompts, plates, report, numbers["guidance"], numbers["control_strength"],
                numbers["style_strength"], numbers["composition_fidelity"],
                numbers["change_strength"])


def card_stats(pil):
    """Mean lightness, saturation and dominant hue, on 0-1 scales."""
    import numpy as np

    small = pil.convert("RGB").copy()
    small.thumbnail((96, 96), Image.LANCZOS)
    rgb = np.asarray(small).astype(np.float32) / 255.0
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    lightness = float((high + low).mean() / 2.0)
    span = high - low
    saturation = float(np.where(high > 0, span / np.maximum(high, 1e-6), 0).mean())
    return lightness, saturation, rgb.reshape(-1, 3).mean(axis=0)


def median_absolute_deviation(values, middle):
    spread = sorted(abs(value - middle) for value in values)
    return spread[len(spread) // 2] if spread else 0.0


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


class TarotDeckReport:
    CATEGORY = DECK_CATEGORY
    INPUT_IS_LIST = True
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("report", "outliers")
    OUTPUT_NODE = True
    FUNCTION = "measure"
    DESCRIPTION = (
        "Measure whether a finished deck actually holds together. Flags cards whose "
        "lightness or saturation sits far from the deck's own median, and checks any "
        "colour constraint that named hex codes — the drift you cannot see by eye at 78 "
        "cards, and the cheapest thing to run because it costs no API calls."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "sensitivity": ("FLOAT", {
                    "default": 2.5, "min": 1.0, "max": 6.0, "step": 0.1,
                    "tooltip": "Deviations from the median before a card is called an "
                               "outlier. Lower flags more.",
                }),
            },
            "optional": {
                "titles": ("STRING",),
                "deck": ("TAROT_DECK",),
                "constraints": ("TAROT_RULES",),
            },
        }

    def measure(self, images, sensitivity, titles=None, deck=None, constraints=None):
        import numpy as np

        threshold = float(sensitivity[0] if isinstance(sensitivity, list) else sensitivity)
        deck = deck[0] if isinstance(deck, list) and deck else deck
        constraints = (constraints[0] if isinstance(constraints, list) and constraints
                       else constraints)
        frames = []
        for item in (images if isinstance(images, list) else [images]):
            batch = item if item.dim() == 4 else item.unsqueeze(0)
            for index in range(batch.shape[0]):
                frames.append(tensor_to_pil(batch[index]))
        if not frames:
            raise NodeError("The deck report received no images.")
        names = [str(t) for t in (titles or [])] or             ["card {}".format(i) for i in range(len(frames))]

        stats = [card_stats(pil) for pil in frames]
        lights = [row[0] for row in stats]
        sats = [row[1] for row in stats]
        mid_light, mid_sat = median(lights), median(sats)
        # A deck of near-identical cards has a deviation of zero, which would make every
        # ratio astronomic. The floor is a hundredth of the 0-1 scale — below that a card
        # is not meaningfully off, however small the spread around it.
        dev_light = max(median_absolute_deviation(lights, mid_light), 0.01)
        dev_sat = max(median_absolute_deviation(sats, mid_sat), 0.01)

        lines = ["{} cards — median lightness {:.3f}, median saturation {:.3f}".format(
            len(frames), mid_light, mid_sat), ""]
        outliers = []
        for index, (light, sat, _mean) in enumerate(stats):
            name = names[index] if index < len(names) else "card {}".format(index)
            off_light = (light - mid_light) / dev_light
            off_sat = (sat - mid_sat) / dev_sat
            flags = []
            if abs(off_light) > threshold:
                flags.append("{} ({:.2f} vs {:.2f}, {:+.1f} dev)".format(
                    "lighter" if off_light > 0 else "darker", light, mid_light, off_light))
            if abs(off_sat) > threshold:
                flags.append("{} ({:.2f} vs {:.2f}, {:+.1f} dev)".format(
                    "more saturated" if off_sat > 0 else "flatter", sat, mid_sat, off_sat))
            if flags:
                outliers.append(name)
                lines.append("  ! {:<24} {}".format(name, "; ".join(flags)))

        if deck and constraints:
            pip_count = max((c["number"] for c in deck if c["arcana"] == "minor"), default=10)
            pip_count = 10 if pip_count > 10 else pip_count
            by_title = {card["title"]: position for position, card in enumerate(deck)}
            checked, missed = 0, []
            for rule, indices in match_sets(constraints, deck, pip_count):
                if not rule["hex"]:
                    continue
                wanted = np.array([[int(code[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
                                   for code in rule["hex"]])
                for card in deck:
                    if card["index"] not in indices:
                        continue
                    position = by_title.get(card["title"])
                    if position is None or position >= len(stats):
                        continue
                    checked += 1
                    distance = float(np.linalg.norm(wanted - stats[position][2], axis=1).min())
                    if distance > 0.45:
                        missed.append("{} ({}, off by {:.2f})".format(
                            card["title"], " ".join(rule["hex"]), distance))
            lines += ["", "colour constraints: {} card(s) checked, {} adrift".format(
                checked, len(missed))]
            lines += ["  ! " + row for row in missed]

        if not outliers:
            lines.append("  no lightness or saturation outliers at {:.1f} deviations".format(
                threshold))
        report = "\n".join(lines)
        print("[strange-pets]\n" + report)
        return (report, ", ".join(outliers))


NODE_CLASS_MAPPINGS = {
    "SPTarotConstraints": TarotConstraints,
    "SPTarotDeckReport": TarotDeckReport,
    "SPTarotStyleLock": TarotStyleLock,
    "SPTarotStyleScope": TarotStyleScope,
    "SPTarotStyleResolve": TarotStyleResolve,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SPTarotConstraints": "Tarot Deck Constraints",
    "SPTarotDeckReport": "Tarot Deck Cohesion Report",
    "SPTarotStyleLock": "Tarot Style Lock",
    "SPTarotStyleScope": "Tarot Style Scope",
    "SPTarotStyleResolve": "Tarot Style Resolve",
}
