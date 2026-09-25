"""Deck-shaped nodes: a canonical 78-card manifest, a slot loader that maps a folder of
art onto it, per-card prompt assembly, and the card frame/lettering compositing that turns
finished art into a printable card."""

import glob
import json
import math
import os
import re

import torch
from PIL import Image, ImageDraw, ImageFont

from .common import (
    IMAGE_EXTS,
    NodeError,
    load_image_file,
    pil_to_tensor,
    resolve_path,
    tensor_to_pil,
)

DECK_CATEGORY = "strange-pets/Tarot"

MAJOR_NAMES = {
    "rws": [
        "The Fool", "The Magician", "The High Priestess", "The Empress", "The Emperor",
        "The Hierophant", "The Lovers", "The Chariot", "Strength", "The Hermit",
        "Wheel of Fortune", "Justice", "The Hanged Man", "Death", "Temperance",
        "The Devil", "The Tower", "The Star", "The Moon", "The Sun", "Judgement",
        "The World",
    ],
    "marseille": [
        "Le Mat", "Le Bateleur", "La Papesse", "L'Imperatrice", "L'Empereur",
        "Le Pape", "L'Amoureux", "Le Chariot", "La Justice", "L'Hermite",
        "La Roue de Fortune", "La Force", "Le Pendu", "L'Arcane sans Nom", "Temperance",
        "Le Diable", "La Maison Dieu", "L'Etoile", "La Lune", "Le Soleil", "Le Jugement",
        "Le Monde",
    ],
    "thoth": [
        "The Fool", "The Magus", "The Priestess", "The Empress", "The Emperor",
        "The Hierophant", "The Lovers", "The Chariot", "Adjustment", "The Hermit",
        "Fortune", "Lust", "The Hanged Man", "Death", "Art", "The Devil", "The Tower",
        "The Star", "The Moon", "The Sun", "The Aeon", "The Universe",
    ],
    # Book T titles vary between recensions — The Foolish Man, The Blasted Tower and The
    # Last Judgement are the forms that differ most from Waite's. Edit and re-save if your
    # source reads otherwise; that is what definition files are for.
    "golden-dawn": [
        "The Foolish Man", "The Magician", "The High Priestess", "The Empress",
        "The Emperor", "The Hierophant", "The Lovers", "The Chariot", "Strength",
        "The Hermit", "Wheel of Fortune", "Justice", "The Hanged Man", "Death",
        "Temperance", "The Devil", "The Blasted Tower", "The Star", "The Moon",
        "The Sun", "The Last Judgement", "The Universe",
    ],
}

# Marseille and Thoth both seat Justice/Adjustment at VIII and Strength/Lust at XI; the
# name lists above are already written in that order, so nothing is swapped at runtime.
SUIT_NAMES = {
    "rws": ["Wands", "Cups", "Swords", "Pentacles"],
    "marseille": ["Batons", "Cups", "Swords", "Coins"],
    "thoth": ["Wands", "Cups", "Swords", "Disks"],
    "golden-dawn": ["Wands", "Cups", "Swords", "Pentacles"],
}

# Listed lowest rank first. The Golden Dawn seats the Knight at the top in place of a King,
# and Crowley carried that into the Thoth.
COURT_NAMES = {
    "rws": ["Page", "Knight", "Queen", "King"],
    "marseille": ["Valet", "Knight", "Queen", "King"],
    "thoth": ["Princess", "Prince", "Queen", "Knight"],
    "golden-dawn": ["Princess", "Prince", "Queen", "Knight"],
}

PIP_NAMES = ["Ace", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
SUIT_ELEMENTS = ["Fire", "Water", "Air", "Earth"]
TRADITIONS = list(MAJOR_NAMES)


def builtin_definition(tradition):
    """A tradition expanded into the full deck definition it stands for.

    Position in `majors` *is* the card's number, so the ordering that separates the
    Golden Dawn line (Strength VIII, Justice XI — what Waite carried into the RWS) from
    the Marseille one (Justice VIII, Strength XI) is the order of this list and nothing
    else. Reorder it and the deck is renumbered.
    """
    return {
        "name": tradition,
        "majors": list(MAJOR_NAMES[tradition]),
        "suits": [{"name": name, "element": element}
                  for name, element in zip(SUIT_NAMES[tradition], SUIT_ELEMENTS)],
        "pips": list(PIP_NAMES),
        "courts": list(COURT_NAMES[tradition]),
        "major_element": "Spirit",
        "renames": {},
        "aliases": {},
    }


def load_definition(path):
    """Read a deck definition from JSON, checking the shape rather than trusting it."""
    resolved = resolve_path(path, "input")
    if not os.path.isfile(resolved):
        raise NodeError("No deck definition at {!r}.".format(resolved))
    try:
        with open(resolved) as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise NodeError("{} is not valid JSON: {}".format(resolved, exc)) from exc
    return validate_definition(data, resolved)


def validate_definition(data, where="definition"):
    if not isinstance(data, dict):
        raise NodeError("{}: expected a JSON object.".format(where))
    definition = {
        "name": str(data.get("name") or "custom"),
        "majors": data.get("majors") or [],
        "suits": data.get("suits") or [],
        "pips": data.get("pips") or list(PIP_NAMES),
        "courts": data.get("courts") or list(COURT_NAMES["rws"]),
        "major_element": str(data.get("major_element") or "Spirit"),
        # Minors are generated from suit x rank, so a one-off minor title has nowhere else
        # to live. Keyed by code (wands-14) or by slug.
        "renames": data.get("renames") or {},
        # code -> the slug the card had before it was renamed. Without this a definition
        # saved after a rename forgets the card ever had another name, and every selector
        # or colour rule written against the old one starts failing.
        "aliases": data.get("aliases") or {},
    }
    for key in ("renames", "aliases"):
        if not isinstance(definition[key], dict):
            raise NodeError("{}: {!r} must be an object keyed by card.".format(where, key))
    for key in ("majors", "pips", "courts"):
        if not isinstance(definition[key], list) or not all(
                isinstance(item, str) for item in definition[key]):
            raise NodeError("{}: {!r} must be a list of names.".format(where, key))
    suits = []
    for index, suit in enumerate(definition["suits"]):
        if isinstance(suit, str):
            suit = {"name": suit}
        if not isinstance(suit, dict) or not suit.get("name"):
            raise NodeError(
                "{}: suits[{}] must be a name, or an object with a name and an element."
                .format(where, index))
        suits.append({
            "name": str(suit["name"]),
            "element": str(suit.get("element") or (
                SUIT_ELEMENTS[index] if index < len(SUIT_ELEMENTS) else "")),
        })
    definition["suits"] = suits
    if not definition["majors"] and not suits:
        raise NodeError("{}: a deck needs majors, suits, or both.".format(where))
    return definition


def definition_for(tradition, definition_path=""):
    path = (definition_path or "").strip()
    return load_definition(path) if path else builtin_definition(tradition)

ROMAN_PAIRS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def roman(number):
    if number <= 0:
        return "0"
    text = ""
    for value, glyph in ROMAN_PAIRS:
        while number >= value:
            text += glyph
            number -= value
    return text


def slugify(text):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").lower())).strip("-")


def normalise(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def split_list(text, expected, fallback):
    """Parse a comma-separated override into exactly `expected` names, or use the default."""
    parts = [part.strip() for part in (text or "").split(",") if part.strip()]
    if not parts:
        return list(fallback)
    if len(parts) != expected:
        raise NodeError(
            "Expected {} comma-separated names, got {}: {!r}".format(expected, len(parts), text))
    return parts


def apply_renames(cards, text):
    """Rename individual cards by slug, index, code or current title.

    `slug` follows the new name so filenames and lettering read correctly, while
    `base_slug` keeps the definition's original — so a selector or a colour rule written
    against `temperance` still finds the card after you rename it to Art. A rename that
    silently orphaned every rule mentioning that card would be a bad trade for convenience.
    """
    by_key = {}
    for card in cards:
        for key in (card["slug"], card["base_slug"], card["code"],
                    str(card["index"]), slugify(card["title"])):
            by_key.setdefault(key, card)

    renamed = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise NodeError(
                "Rename line {!r} needs an '='. Write 'temperance = Art', or "
                "'14 = Art' by position.".format(line))
        target, _, title = line.partition("=")
        target, title = target.strip(), title.strip()
        card = by_key.get(target) or by_key.get(slugify(target))
        if card is None:
            raise NodeError(
                "Rename target {!r} matches no card. Use a slug (temperance), an index (14), "
                "a code (major-14), or the card's current title.".format(target))
        if not title:
            raise NodeError("Rename line {!r} has no new title.".format(line))
        card["title"] = title
        card["slug"] = slugify(title)
        renamed.append((target, title))
    return renamed


def build_deck(definition, numerals, suit_override="", court_override=""):
    """Every card the definition describes, majors in order then each suit low to high.

    The count follows the definition rather than being fixed at 78, so a deck with extra
    majors or a fifth suit builds without anything here needing to know about it.
    """
    majors = list(definition["majors"])
    suit_defs = definition["suits"]
    suits = split_list(suit_override, len(suit_defs), [s["name"] for s in suit_defs])
    elements = [s["element"] for s in suit_defs]
    pips = list(definition["pips"])
    courts = split_list(court_override, len(definition["courts"]), definition["courts"])

    cards = []

    def numeral_for(number):
        if numerals == "none" or number is None:
            return ""
        return roman(number) if numerals == "roman" else str(number)

    for number, name in enumerate(majors):
        cards.append({
            "index": len(cards), "arcana": "major", "number": number, "title": name,
            "numeral": "0" if (number == 0 and numerals == "roman") else numeral_for(number),
            "suit": "", "rank": "", "element": definition["major_element"],
            "code": "major-{:02d}".format(number),
        })

    for suit_index, suit in enumerate(suits):
        for rank_index, rank in enumerate(pips + courts, start=1):
            court = rank_index > len(pips)
            cards.append({
                "index": len(cards), "arcana": "minor", "number": rank_index,
                "title": "{} of {}".format(rank, suit),
                "numeral": "" if court else numeral_for(rank_index),
                "suit": suit, "rank": rank, "element": elements[suit_index],
                "code": "{}-{:02d}".format(slugify(suit), rank_index),
            })

    # Definition renames are part of the deck's identity, so they land before base_slug
    # is taken; a rename typed on the node afterwards moves slug but not base_slug.
    lookup = {}
    for card in cards:
        lookup.setdefault(card["code"], card)
        lookup.setdefault(slugify(card["title"]), card)
    for target, title in (definition.get("renames") or {}).items():
        card = lookup.get(target) or lookup.get(slugify(target))
        if card is None:
            raise NodeError("Definition renames {!r}, which is not a card in this deck.".format(target))
        card["title"] = str(title)
    aliases = definition.get("aliases") or {}
    for card in cards:
        card["slug"] = slugify(card["title"])
        card["base_slug"] = slugify(aliases.get(card["code"]) or card["slug"])
    return cards


SCOPES = {
    "full deck": lambda card, pips: True,
    "major arcana": lambda card, pips: card["arcana"] == "major",
    "minor arcana": lambda card, pips: card["arcana"] == "minor",
    "pips only": lambda card, pips: card["arcana"] == "minor" and card["number"] <= pips,
    "courts only": lambda card, pips: card["arcana"] == "minor" and card["number"] > pips,
}

# Suit names change with the deck definition, so the suits are selected by position.
SUIT_SCOPES = ["first suit", "second suit", "third suit", "fourth suit"]
SCOPE_CHOICES = list(SCOPES) + SUIT_SCOPES + ["proof set", "selector"]


# --------------------------------------------------------------------------- selectors

GROUPS = {
    "all": lambda card, pips: True,
    "majors": lambda card, pips: card["arcana"] == "major",
    "minors": lambda card, pips: card["arcana"] == "minor",
    "courts": lambda card, pips: card["arcana"] == "minor" and card["number"] > pips,
    "pips": lambda card, pips: card["arcana"] == "minor" and card["number"] <= pips,
    "aces": lambda card, pips: card["arcana"] == "minor" and card["number"] == 1,
}


def number_span(text):
    """'7' or '2-5' becomes a membership test over a card's number or index."""
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        value = int(text)
        return lambda number: number == value
    found = re.fullmatch(r"(\d+)\s*-\s*(\d+)", text)
    if not found:
        raise NodeError("{!r} is not a number or a range like 2-5.".format(text))
    low, high = int(found.group(1)), int(found.group(2))
    return lambda number: low <= number <= high


def term_test(term, deck, pip_count):
    """One selector term becomes a predicate. Unknown terms raise rather than match nothing.

    A silent no-match is the expensive failure here: a mistyped suit means that suit
    quietly keeps the deck style and you find out after paying for the run.
    """
    negate = term.startswith("!")
    term = term.lstrip("!")
    suits = list(dict.fromkeys(slugify(c["suit"]) for c in deck if c["suit"]))
    ranks = {slugify(c["rank"]) for c in deck if c["rank"]}
    elements = {c["element"].lower() for c in deck if c["element"]}
    slugs = {c["slug"] for c in deck} | {c.get("base_slug", c["slug"]) for c in deck}

    if term in GROUPS:
        test = lambda card: GROUPS[term](card, pip_count)
    elif term in suits:
        test = lambda card: slugify(card["suit"]) == term
    elif re.fullmatch(r"suit\d+", term):
        position = int(term[4:]) - 1
        if not 0 <= position < len(suits):
            raise NodeError("This deck has {} suit(s), so {!r} selects nothing.".format(
                len(suits), term))
        test = lambda card: slugify(card["suit"]) == suits[position]
    elif term in ranks:
        test = lambda card: slugify(card["rank"]) == term
    elif term in elements:
        test = lambda card: card["element"].lower() == term
    elif term in slugs:
        test = lambda card: term in (card["slug"], card.get("base_slug"))
    elif term.startswith("index="):
        inside = number_span(term[len("index="):])
        test = lambda card: inside(card["index"])
    elif term.startswith("number="):
        inside = number_span(term[len("number="):])
        test = lambda card: inside(card["number"])
    elif re.fullmatch(r"\d+(\s*-\s*\d+)?", term):
        inside = number_span(term)
        test = lambda card: inside(card["number"])
    else:
        raise NodeError(
            "{!r} is not a group ({}), a suit ({}), a rank, an element, a card slug, or a "
            "number. Use index=N or number=N-M for spans.".format(
                term, ", ".join(GROUPS), ", ".join(suits) or "none"))
    return (lambda card: not test(card)) if negate else test


def select_cards(selector, deck, pip_count=10):
    """Resolve a selector against a deck.

    Space between terms is AND, a comma between alternatives is OR, and a leading `!`
    negates one term — so `courts wands` is four cards, `courts !wands` is twelve, and
    `majors, aces` is twenty-six.
    """
    selector = (selector or "").strip()
    if not selector:
        return []
    alternatives = [alt.split() for alt in selector.split(",")]
    tests = [[term_test(term, deck, pip_count) for term in terms]
             for terms in alternatives if terms]
    if not tests:
        return []
    return [card for card in deck
            if any(all(test(card) for test in group) for group in tests)]


# The smallest set that exercises every dimension the style cascade can scope on. Written
# positionally rather than by name, so it resolves the same against any deck definition.
PROOF_SELECTOR = "index=0, index=16, index=18, courts suit1, pips 7, suit2 13"


def apply_scope(cards, scope, suits, pip_count=10, selector=""):
    if scope == "proof set":
        return select_cards(PROOF_SELECTOR, cards, pip_count)
    if scope == "selector":
        if not (selector or "").strip():
            raise NodeError("scope is 'selector' but the selector field is empty.")
        return select_cards(selector, cards, pip_count)
    if scope in SUIT_SCOPES:
        position = SUIT_SCOPES.index(scope)
        if position >= len(suits):
            raise NodeError("This deck has {} suit(s); {!r} does not exist.".format(
                len(suits), scope))
        return [card for card in cards if card["suit"] == suits[position]]
    keep = SCOPES.get(scope, lambda card, pips: True)
    return [card for card in cards if keep(card, pip_count)]


ARTICLES = ("the", "le", "la", "les", "l")


def strip_article(text):
    """'thehighpriestess' -> 'highpriestess', so HighPriestess_final.png still lands."""
    for article in ARTICLES:
        if text.startswith(article) and len(text) > len(article) + 2:
            return text[len(article):]
    return text


def card_aliases(card):
    """Filename spellings that should resolve to this card, longest first.

    Purely numeric aliases are exact-match only — a bare "07" would otherwise claim any
    file with a 7 in its name — while a short *word* like "art" or "sun" is safe enough
    to match inside a longer filename, which short card names depend on.
    """
    aliases = {normalise(card["slug"]), normalise(card["title"]),
               normalise(card.get("base_slug", ""))}
    number = card["number"]
    if card["arcana"] == "major":
        bare = strip_article(normalise(card["slug"]))
        aliases |= {
            bare,
            strip_article(normalise(card.get("base_slug", ""))),
            "{:02d}{}".format(number, normalise(card["slug"])),
            "{:02d}{}".format(number, bare),
            "major{:02d}".format(number),
            normalise(roman(number)) if number else "0",
            "{:02d}".format(number),
            str(number),
        }
    else:
        suit, rank = normalise(card["suit"]), normalise(card["rank"])
        aliases |= {
            suit + rank, rank + suit, rank + "of" + suit,
            "{}{:02d}".format(suit, number), "{}{}".format(suit, number),
            "{:02d}{}".format(number, suit), "{}{}".format(number, suit),
            normalise(card["code"]),
        }
    aliases.discard("")
    return sorted(aliases, key=len, reverse=True)


def stem_tokens(stem):
    """'SevenOfCups_v2' -> {seven, of, cups, v2}. Splits on punctuation and camel case."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    return {part.lower() for part in re.split(r"[^A-Za-z0-9]+", spaced) if part}


def match_files_to_cards(cards, files, match_by):
    """Return {card index: path} plus a per-file account of how it was decided."""
    stems = {path: normalise(os.path.splitext(os.path.basename(path))[0]) for path in files}
    raw_stems = {path: os.path.splitext(os.path.basename(path))[0] for path in files}
    tokens = {path: stem_tokens(raw_stems[path]) for path in files}
    assigned = {}
    notes = []
    claimed = set()

    def claim(card, path, how):
        if card["index"] in assigned:
            notes.append("  {} also matches {} ({}), already taken by {}".format(
                os.path.basename(path), card["title"], how,
                os.path.basename(assigned[card["index"]])))
            return
        assigned[card["index"]] = path
        claimed.add(path)
        notes.append("  {} -> {} ({})".format(os.path.basename(path), card["title"], how))

    if match_by in ("auto", "slug"):
        # Longest alias wins, so "seven-of-wands" beats the bare "wands" of a suit name.
        scored = []
        for card in cards:
            for alias in card_aliases(card):
                # Three-letter names ("art", "sun") must land on a whole word, or
                # sunset_moodboard.png would claim The Sun. Longer names may sit inside
                # a longer stem, which is what makes SevenOfCups_v2.png work.
                word_only = len(alias) == 3
                loose = len(alias) >= 4 and not alias.isdigit()
                for path, stem in stems.items():
                    if (stem == alias or (loose and alias in stem)
                            or (word_only and alias in tokens[path])):
                        scored.append((len(alias), stem == alias, card, path, alias))
        for _, _, card, path, alias in sorted(scored, key=lambda row: (row[0], row[1]), reverse=True):
            if path in claimed or card["index"] in assigned:
                continue
            claim(card, path, "matched {!r}".format(alias))

    if match_by in ("auto", "index prefix"):
        by_position = {card["index"]: card for card in cards}
        for path in files:
            if path in claimed:
                continue
            found = re.match(r"^\s*(\d+)", raw_stems[path])
            if not found:
                continue
            value = int(found.group(1))
            # Accept both 0-based deck index and 1-based card position.
            card = by_position.get(value) or by_position.get(value - 1)
            if card:
                claim(card, path, "index prefix {}".format(value))

    if match_by == "exact filename":
        for card in cards:
            for path, stem in stems.items():
                if path not in claimed and stem == normalise(card["slug"]):
                    claim(card, path, "exact filename")

    unclaimed = [os.path.basename(path) for path in files if path not in claimed]
    return assigned, notes, unclaimed


# --------------------------------------------------------------------------- drawing

CARD_SIZES = {
    "tarot 2.75x4.75in": (2.75, 4.75),
    "poker 2.5x3.5in": (2.5, 3.5),
    "bridge 2.25x3.5in": (2.25, 3.5),
    "large 3.5x5.75in": (3.5, 5.75),
    "square 3.5x3.5in": (3.5, 3.5),
    "custom": None,
}

BORDER_STYLES = ["none", "keyline", "solid band", "double rule", "inset panel"]
_FONT_CACHE = {}


def load_font(path, size):
    """A truetype font from `path`, or Pillow's built-in when the path is blank."""
    size = max(6, int(size))
    key = (path or "", size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if path:
        resolved = os.path.expanduser(path.strip())
        if not os.path.isfile(resolved):
            raise NodeError("No font file at {!r}.".format(resolved))
        try:
            font = ImageFont.truetype(resolved, size)
        except OSError as exc:
            raise NodeError("Could not load font {!r}: {}".format(resolved, exc)) from exc
    else:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def text_width(draw, text, font, tracking):
    if not text:
        return 0
    total = sum(draw.textlength(char, font=font) for char in text)
    return int(round(total + tracking * (len(text) - 1)))


def draw_tracked(draw, xy, text, font, fill, tracking, anchor_y="top",
                 stroke_width=0, stroke_fill=None):
    """Draw `text` a glyph at a time so letter-spacing can be dialled in.

    Display lettering on a card is usually widely tracked, and Pillow has no tracking
    parameter, so the run is laid out here rather than handed to `draw.text` whole.
    """
    x, y = xy
    if anchor_y == "baseline":
        y -= font.getbbox("H")[3] if hasattr(font, "getbbox") else 0
    for char in text:
        draw.text((x, y), char, font=font, fill=fill,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += draw.textlength(char, font=font) + tracking
    return x


def fit_into(pil, box_w, box_h, mode):
    """Resize `pil` to fill (cover), sit inside (contain) or match (stretch) the box."""
    if box_w < 1 or box_h < 1:
        raise NodeError("The art box collapsed to nothing; lower margin_pct or raise the card size.")
    if mode == "stretch":
        return pil.resize((box_w, box_h), Image.LANCZOS)
    scale = max(box_w / pil.width, box_h / pil.height) if mode == "cover" else \
        min(box_w / pil.width, box_h / pil.height)
    sized = pil.resize((max(1, int(round(pil.width * scale))),
                        max(1, int(round(pil.height * scale)))), Image.LANCZOS)
    if mode == "contain":
        return sized
    left = (sized.width - box_w) // 2
    top = (sized.height - box_h) // 2
    return sized.crop((left, top, left + box_w, top + box_h))


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                           radius=max(0, radius), fill=255)
    return mask


def outline(draw, box, colour, width, radius=0):
    if width <= 0:
        return
    # Pillow strokes outward from the path, so the path is nudged inward by half the width.
    offset = width / 2.0
    draw.rounded_rectangle(
        [box[0] - offset, box[1] - offset, box[2] + offset, box[3] + offset],
        radius=max(0, radius), outline=colour, width=int(width))


# --------------------------------------------------------------------------- palettes

HEX_RE = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


def parse_hex_codes(text):
    """Pull #rgb / #rrggbb out of free text, normalised and de-duplicated in order.

    Palettes are written inline in constraint and style lines, so a colour rule reads as
    one sentence — "dominant red, burning — #B3121B, #7A0E14" — rather than being split
    across a prose field and a colour field.
    """
    found = []
    for match in HEX_RE.finditer(text or ""):
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        code = "#" + digits.upper()
        if code not in found:
            found.append(code)
    return found


def extract_palette(pil, count):
    """The `count` most-used colours in an image, as hex."""
    small = pil.convert("RGB").copy()
    small.thumbnail((256, 256), Image.LANCZOS)
    reduced = small.quantize(colors=max(1, min(64, count)), method=Image.MEDIANCUT)
    palette = reduced.getpalette() or []
    ranked = sorted(reduced.getcolors() or [], reverse=True)
    codes = []
    for _weight, index in ranked[:count]:
        red, green, blue = palette[index * 3:index * 3 + 3]
        code = "#{:02X}{:02X}{:02X}".format(red, green, blue)
        if code not in codes:
            codes.append(code)
    return codes


def palette_swatch(codes, width=1024, height=256, layout="strip", labels=False):
    """Render a palette as an image, so it can go in a reference slot rather than a prompt.

    A model follows a swatch far more reliably than it follows six hex codes in prose,
    and the same image doubles as the thing you check the result against.
    """
    if not codes:
        raise NodeError("No colours to render — write hex codes like #B3121B.")
    if layout == "grid":
        columns = int(math.ceil(math.sqrt(len(codes))))
    else:
        columns = len(codes)
    rows = int(math.ceil(len(codes) / columns))
    swatch = Image.new("RGB", (max(1, width), max(1, height)), "#000000")
    draw = ImageDraw.Draw(swatch)
    cell_w, cell_h = width / columns, height / rows
    font = load_font("", max(10, int(cell_h * 0.16))) if labels else None
    for position, code in enumerate(codes):
        row, column = divmod(position, columns)
        box = [int(column * cell_w), int(row * cell_h),
               int((column + 1) * cell_w) - 1, int((row + 1) * cell_h) - 1]
        draw.rectangle(box, fill=code)
        if labels:
            red, green, blue = swatch.getpixel((box[0] + 2, box[1] + 2))
            ink = "#000000" if (red * 299 + green * 587 + blue * 114) / 1000 > 140 else "#FFFFFF"
            draw.text((box[0] + 6, box[3] - int(cell_h * 0.22)), code, fill=ink, font=font)
    return swatch


def placeholder_card(width, height, title, colour="#191622", text_colour="#6f6786"):
    card = Image.new("RGB", (max(1, width), max(1, height)), colour)
    draw = ImageDraw.Draw(card)
    inset = max(4, min(width, height) // 24)
    draw.rectangle([inset, inset, width - inset - 1, height - inset - 1],
                   outline=text_colour, width=max(1, inset // 6))
    font = load_font("", max(10, height // 20))
    label = title or "empty slot"
    span = text_width(draw, label, font, 0)
    draw.text(((width - span) // 2, height // 2), label, font=font, fill=text_colour)
    return card


# --------------------------------------------------------------------------- nodes

class TarotDeck:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("TAROT_DECK", "STRING", "STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = ("deck", "titles", "slugs", "numerals", "indices", "count", "summary")
    OUTPUT_IS_LIST = (False, True, True, True, True, False, False)
    FUNCTION = "build"
    DESCRIPTION = (
        "The card list every other deck node works against: 78 slots in order, with each "
        "card's title, numeral, suit, rank and element. Pick a tradition, or override the "
        "names outright for a deck of your own."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "tradition": (TRADITIONS, {
                    "default": "rws",
                    "tooltip": "Starting point for the names and the order. Marseille seats "
                               "Justice at VIII and Strength at XI; the Golden Dawn line that "
                               "Waite carried into the RWS swaps them, which is why 'rws' and "
                               "'marseille' differ in order as well as in language. Ignored "
                               "when definition_path is set.",
                }),
                "definition_path": ("STRING", {
                    "default": "",
                    "tooltip": "A deck definition JSON — majors in order, suits with their "
                               "elements, pip and court names. Overrides tradition entirely, "
                               "and its list lengths set the deck's size and numbering.",
                }),
                "scope": (SCOPE_CHOICES, {
                    "default": "full deck",
                    "tooltip": "Work on part of the deck — the majors while they are in polish, "
                               "one suit at a time, or the whole thing. 'proof set' is the "
                               "12-card spread that exercises every scoping dimension; "
                               "'selector' uses the field below.",
                }),
                "selector": ("STRING", {
                    "default": PROOF_SELECTOR,
                    "tooltip": "Used when scope is 'selector'. Space is AND, comma is OR, "
                               "! negates: 'courts wands', 'pips 7', 'courts !wands', "
                               "'majors, aces', 'index=0-5', 'number=2-5'.",
                }),
                "numerals": (["roman", "arabic", "none"], {"default": "roman"}),
                "letter_case": (["as written", "UPPERCASE", "lowercase", "Title Case"], {
                    "default": "as written"}),
                "suit_names": ("STRING", {
                    "default": "",
                    "tooltip": "Four comma-separated names to replace the tradition's suits, "
                               "e.g. Keys, Wells, Blades, Stones. Blank keeps the default.",
                }),
                "court_names": ("STRING", {
                    "default": "",
                    "tooltip": "Four comma-separated court names, lowest rank first.",
                }),
                "rename": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "One 'target = new title' per line. The target is a slug "
                               "(temperance), an index (14), a code (major-14) or the card's "
                               "current title — so renaming one card costs one line. Selectors "
                               "and colour rules written against the old slug keep working.",
                }),
                "save_definition_to": ("STRING", {
                    "default": "",
                    "tooltip": "Write the resolved definition — tradition plus every override "
                               "above — to this path as JSON. Start from a tradition, adjust, "
                               "save, then load it back through definition_path as your deck's "
                               "own nomenclature.",
                }),
            }
        }

    def build(self, tradition, definition_path, scope, selector, numerals, letter_case,
              suit_names, court_names, rename, save_definition_to):
        definition = definition_for(tradition, definition_path)
        suits = split_list(suit_names, len(definition["suits"]),
                           [s["name"] for s in definition["suits"]])
        cards = build_deck(definition, numerals, suit_names, court_names)
        renamed = apply_renames(cards, rename)
        pip_count = len(definition["pips"])
        scoped = apply_scope(cards, scope, suits, pip_count, selector)
        if not scoped:
            raise NodeError("Scope {!r} selected no cards.".format(scope))

        casing = {
            "UPPERCASE": str.upper, "lowercase": str.lower, "Title Case": str.title,
        }.get(letter_case)
        if casing:
            for card in scoped:
                card["title"] = casing(card["title"])

        # Saved after the overrides are folded in, so what comes back is what ran.
        resolved = {
            "name": definition["name"],
            "majors": [card["title"] for card in cards if card["arcana"] == "major"],
            "suits": [{"name": name, "element": definition["suits"][i]["element"]}
                      for i, name in enumerate(suits)],
            "pips": list(definition["pips"]),
            "courts": split_list(court_names, len(definition["courts"]), definition["courts"]),
            "major_element": definition["major_element"],
        }
        generated = "{} of {}".format
        resolved["renames"] = {
            card["code"]: card["title"] for card in cards
            if card["arcana"] == "minor"
            and card["title"] != generated(card["rank"], card["suit"])
        }
        # Carry each renamed card's original slug forward, so a rule written against the
        # name it used to have keeps resolving in whatever graph loads this file next.
        resolved["aliases"] = {
            card["code"]: card["base_slug"] for card in cards
            if card["base_slug"] != card["slug"]
        }
        if save_definition_to.strip():
            target = resolve_path(save_definition_to, "input")
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w") as handle:
                json.dump(resolved, handle, indent=2)
            print("[strange-pets] wrote deck definition to {}".format(target))

        titles = [card["title"] for card in scoped]
        summary = "{} — {}, {} of {} cards: {} … {}{}".format(
            scope, resolved["name"], len(scoped), len(cards), titles[0], titles[-1],
            "" if not renamed else "\nrenamed: " + ", ".join(
                "{} -> {}".format(target, title) for target, title in renamed))
        return (scoped, titles, [card["slug"] for card in scoped],
                [card["numeral"] for card in scoped],
                [card["index"] for card in scoped], len(scoped), summary)


class TarotDeckLoad:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "INT", "TAROT_DECK", "STRING")
    RETURN_NAMES = ("images", "titles", "numerals", "slugs", "indices", "deck", "report")
    OUTPUT_IS_LIST = (True, True, True, True, True, False, False)
    FUNCTION = "load"
    DESCRIPTION = (
        "Map a folder of art onto the deck's slots, so every downstream node runs once per "
        "card with that card's own title beside it. Files are matched by name, and slots "
        "with nothing to fill them are reported rather than silently skipped."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "deck": ("TAROT_DECK",),
                "directory": ("STRING", {
                    "default": "",
                    "tooltip": "Absolute path, or relative to the ComfyUI input directory.",
                }),
                "pattern": ("STRING", {"default": "*"}),
                "match_by": (["auto", "slug", "index prefix", "exact filename"], {
                    "default": "auto",
                    "tooltip": "auto tries name matching first (the-fool.png, 07_wands.png, "
                               "SevenOfWands_v3.png) then falls back to a leading number.",
                }),
                "on_missing": (["blank slot", "skip", "error"], {
                    "default": "blank slot",
                    "tooltip": "blank slot keeps the deck's shape while it is unfinished — every "
                               "position stays in the run, holding a labelled placeholder.",
                }),
                "blank_width": ("INT", {"default": 1024, "min": 64, "max": 8192}),
                "blank_height": ("INT", {"default": 1792, "min": 64, "max": 8192}),
                "overrides": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "One 'slug = filename' per line for anything the matcher gets "
                               "wrong, e.g. the-tower = tower_final_v9.png",
                }),
            }
        }

    @classmethod
    def IS_CHANGED(cls, deck, directory, pattern, **kwargs):
        resolved = resolve_path(directory, "input")
        try:
            names = sorted(os.listdir(resolved))
            return json.dumps([[(n, os.path.getmtime(os.path.join(resolved, n))) for n in names],
                               pattern, sorted(kwargs.items(), key=str)], default=str)
        except OSError:
            return "{}|missing".format(directory)

    def load(self, deck, directory, pattern, match_by, on_missing, blank_width, blank_height,
             overrides):
        resolved = resolve_path(directory, "input")
        if not resolved or not os.path.isdir(resolved):
            raise NodeError("{!r} is not a directory.".format(resolved or directory))

        files = sorted(
            path for path in glob.glob(os.path.join(glob.escape(resolved), pattern or "*"))
            if os.path.isfile(path) and path.lower().endswith(IMAGE_EXTS)
        )
        assigned, notes, unclaimed = match_files_to_cards(deck, files, match_by)

        by_slug = {card["slug"]: card for card in deck}
        for line in (overrides or "").splitlines():
            if not line.strip() or "=" not in line:
                continue
            slug, _, name = line.partition("=")
            card = by_slug.get(slugify(slug))
            if card is None:
                raise NodeError("Override {!r} names no card in this deck.".format(slug.strip()))
            path = os.path.expanduser(name.strip())
            if not os.path.isabs(path):
                path = os.path.join(resolved, path)
            if not os.path.isfile(path):
                raise NodeError("Override for {}: no file at {!r}.".format(card["title"], path))
            assigned[card["index"]] = path
            notes.append("  {} -> {} (override)".format(os.path.basename(path), card["title"]))

        images, titles, numerals, slugs, indices, filled = [], [], [], [], [], []
        missing = []
        for card in deck:
            path = assigned.get(card["index"])
            if path:
                images.append(load_image_file(path))
            elif on_missing == "error":
                missing.append(card["title"])
                continue
            elif on_missing == "skip":
                missing.append(card["title"])
                continue
            else:
                images.append(pil_to_tensor(
                    placeholder_card(blank_width, blank_height, card["title"])))
                missing.append(card["title"])
            titles.append(card["title"])
            numerals.append(card["numeral"])
            slugs.append(card["slug"])
            indices.append(card["index"])
            filled.append(card)

        if on_missing == "error" and missing:
            raise NodeError("{} of {} slots have no art: {}".format(
                len(missing), len(deck), ", ".join(missing[:12]) +
                (" …" if len(missing) > 12 else "")))
        if not images:
            raise NodeError("Nothing matched in {!r}; {} files were considered.".format(
                resolved, len(files)))

        report = "\n".join(
            ["{} of {} slots filled from {}".format(len(deck) - len(missing), len(deck), resolved),
             ""] + notes +
            ([""] + ["unmatched files: " + ", ".join(unclaimed)] if unclaimed else []) +
            ([""] + ["empty slots ({}): {}".format(len(missing), ", ".join(missing))]
             if missing else []))
        print("[strange-pets]\n" + report)
        return (images, titles, numerals, slugs, indices, filled, report)


class TarotPrompt:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("prompts", "preview")
    OUTPUT_IS_LIST = (True, False)
    FUNCTION = "build"
    DESCRIPTION = (
        "One prompt per card, from a template plus a shared style block — so a whole deck "
        "run says 'The Hermit' on The Hermit's call and 'Seven of Swords' on the next."
    )

    TOKENS = "{title} {numeral} {arcana} {suit} {rank} {element} {slug} {index} {style}"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "deck": ("TAROT_DECK",),
                "template": ("STRING", {
                    "multiline": True,
                    "default": "Tarot card '{title}' ({numeral}), {arcana} arcana. {style}",
                    "tooltip": "Tokens: " + cls.TOKENS,
                }),
                "style": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "The one block you retune between deck runs; it lands on {style}.",
                }),
            }
        }

    def build(self, deck, template, style):
        prompts = []
        for card in deck:
            values = dict(card)
            values["style"] = style
            try:
                prompts.append(re.sub(r"\s+", " ", template.format(**values)).strip())
            except KeyError as exc:
                raise NodeError(
                    "Unknown token {} in the template. Available: {}".format(exc, self.TOKENS)
                ) from exc
        preview = "\n".join("{:>2}. {}".format(card["index"], text)
                            for card, text in list(zip(deck, prompts))[:6])
        if len(prompts) > 6:
            preview += "\n… {} more".format(len(prompts) - 6)
        return (prompts, preview)


class TarotCardFrame:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("IMAGE", "INT", "INT", "STRING")
    RETURN_NAMES = ("card", "width", "height", "summary")
    FUNCTION = "compose"
    DESCRIPTION = (
        "Seat finished art inside a printable card: trim size at a real DPI, optional bleed, "
        "a margin the art sits within, and a border drawn around it. Accepts a frame PNG with "
        "alpha on top, so a border designed elsewhere can ride over the same geometry."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "card_size": (list(CARD_SIZES), {
                    "default": "tarot 2.75x4.75in",
                    "tooltip": "Trim size before bleed. 'tarot' is the standard 70x120mm stock.",
                }),
                "dpi": ("INT", {"default": 300, "min": 72, "max": 1200,
                                "tooltip": "300 is the usual print minimum."}),
                "custom_width_px": ("INT", {"default": 0, "min": 0, "max": 12000}),
                "custom_height_px": ("INT", {"default": 0, "min": 0, "max": 12000}),
                "bleed_mm": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 10.0, "step": 0.5,
                                       "tooltip": "Printers usually ask for 3mm. 0 gives a trim-size card."}),
                "art_fit": (["cover", "contain", "stretch"], {"default": "cover"}),
                "border": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Off gives full-bleed art at the same trim size: the margins "
                               "collapse to nothing and no rule or overlay is drawn. Trim, dpi, "
                               "bleed and the card's corner radius still apply.",
                }),
                "margin_pct": ("FLOAT", {
                    "default": 6.0, "min": 0.0, "max": 40.0, "step": 0.5,
                    "tooltip": "Border band width, as a percentage of the card's width.",
                }),
                "top_margin_extra_pct": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 40.0, "step": 0.5,
                    "tooltip": "Extra room above the art for a numeral, as a percentage of height.",
                }),
                "bottom_margin_extra_pct": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 40.0, "step": 0.5,
                    "tooltip": "Extra room under the art for a title, as a percentage of height.",
                }),
                "background": ("STRING", {"default": "#0d0b14"}),
                "border_style": (BORDER_STYLES, {"default": "keyline"}),
                "border_colour": ("STRING", {"default": "#c8a94b"}),
                "border_width_px": ("INT", {"default": 6, "min": 0, "max": 200}),
                "rule_colour": ("STRING", {"default": "#c8a94b"}),
                "rule_width_px": ("INT", {"default": 2, "min": 0, "max": 200}),
                "rule_gap_px": ("INT", {"default": 14, "min": 0, "max": 400}),
                "art_corner_radius_px": ("INT", {"default": 0, "min": 0, "max": 400}),
                "card_corner_radius_px": ("INT", {"default": 0, "min": 0, "max": 400}),
            },
            "optional": {
                "overlay_path": ("STRING", {
                    "default": "",
                    "tooltip": "A frame PNG with alpha, laid over the finished card at card size.",
                }),
                "frame_overlay": ("IMAGE", {"tooltip": "Same idea, from upstream in the graph."}),
                "frame_alpha": ("MASK", {"tooltip": "Alpha for frame_overlay; white is opaque."}),
            },
        }

    def geometry(self, card_size, dpi, custom_width_px, custom_height_px, bleed_mm):
        inches = CARD_SIZES[card_size]
        if inches is None:
            if custom_width_px < 64 or custom_height_px < 64:
                raise NodeError(
                    "card_size 'custom' needs custom_width_px and custom_height_px of at least 64.")
            trim = (int(custom_width_px), int(custom_height_px))
        else:
            trim = (int(round(inches[0] * dpi)), int(round(inches[1] * dpi)))
        bleed = int(round(bleed_mm / 25.4 * dpi))
        return trim, bleed

    def overlay_layer(self, size, overlay_path, frame_overlay, frame_alpha):
        if overlay_path and overlay_path.strip():
            path = resolve_path(overlay_path, "input")
            if not os.path.isfile(path):
                raise NodeError("No overlay image at {!r}.".format(path))
            layer = Image.open(path).convert("RGBA")
        elif frame_overlay is not None:
            layer = tensor_to_pil(frame_overlay).convert("RGBA")
            if frame_alpha is not None:
                alpha = frame_alpha
                while alpha.dim() > 2:
                    alpha = alpha[0]
                mask = Image.fromarray(
                    (alpha.detach().cpu().numpy().clip(0, 1) * 255).astype("uint8"), mode="L")
                layer.putalpha(mask.resize(layer.size, Image.LANCZOS))
        else:
            return None
        return layer.resize(size, Image.LANCZOS)

    def compose(self, image, card_size, dpi, custom_width_px, custom_height_px, bleed_mm,
                art_fit, border, margin_pct, top_margin_extra_pct, bottom_margin_extra_pct,
                background, border_style,
                border_colour, border_width_px, rule_colour, rule_width_px, rule_gap_px,
                art_corner_radius_px, card_corner_radius_px, overlay_path="",
                frame_overlay=None, frame_alpha=None):
        # One switch, because "no border" is four widgets otherwise and the frame is the
        # cheap half of a card — the same art wants seeing both ways.
        if not border:
            margin_pct = top_margin_extra_pct = bottom_margin_extra_pct = 0.0
            border_style = "none"
            overlay_path, frame_overlay = "", None
            # A rounded art box only reads as intentional inside a border; at full bleed it
            # reads as a printing fault. The card's own die-cut radius still applies.
            art_corner_radius_px = 0
        trim, bleed = self.geometry(card_size, dpi, custom_width_px, custom_height_px, bleed_mm)
        card_w, card_h = trim[0] + bleed * 2, trim[1] + bleed * 2
        margin = int(round(margin_pct / 100.0 * trim[0]))
        head = int(round(top_margin_extra_pct / 100.0 * trim[1]))
        foot = int(round(bottom_margin_extra_pct / 100.0 * trim[1]))
        art_box = (bleed + margin, bleed + margin + head,
                   card_w - bleed - margin, card_h - bleed - margin - foot)
        art_w, art_h = art_box[2] - art_box[0], art_box[3] - art_box[1]
        overlay = self.overlay_layer((card_w, card_h), overlay_path, frame_overlay, frame_alpha)

        batch = image if image.dim() == 4 else image.unsqueeze(0)
        frames = []
        for offset in range(batch.shape[0]):
            card = Image.new("RGB", (card_w, card_h), background)
            draw = ImageDraw.Draw(card)

            if border_style == "solid band":
                draw.rounded_rectangle([bleed, bleed, card_w - bleed - 1, card_h - bleed - 1],
                                       radius=card_corner_radius_px, fill=border_colour)

            art = fit_into(tensor_to_pil(batch[offset]).convert("RGB"), art_w, art_h, art_fit)
            position = (art_box[0] + (art_w - art.width) // 2,
                        art_box[1] + (art_h - art.height) // 2)
            card.paste(art, position,
                       rounded_mask(art.size, art_corner_radius_px) if art_corner_radius_px else None)

            drawn = (position[0], position[1],
                     position[0] + art.width - 1, position[1] + art.height - 1)
            if border_style in ("keyline", "solid band", "double rule", "inset panel"):
                outline(draw, drawn, border_colour, border_width_px, art_corner_radius_px)
            if border_style == "double rule":
                gap = border_width_px + rule_gap_px
                outline(draw, (drawn[0] - gap, drawn[1] - gap, drawn[2] + gap, drawn[3] + gap),
                        rule_colour, rule_width_px, art_corner_radius_px)
            if border_style == "inset panel":
                inset = bleed + rule_gap_px
                outline(draw, (inset, inset, card_w - inset - 1, card_h - inset - 1),
                        rule_colour, rule_width_px, card_corner_radius_px)

            if overlay is not None:
                card = Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB")

            if card_corner_radius_px:
                rounded = Image.new("RGB", (card_w, card_h), background)
                rounded.paste(card, (0, 0), rounded_mask((card_w, card_h), card_corner_radius_px))
                card = rounded

            frames.append(pil_to_tensor(card))

        summary = ("{} at {}dpi -> {}x{}px{}, art box {}x{}, {}".format(
            card_size, dpi, card_w, card_h,
            " (incl. {}px bleed)".format(bleed) if bleed else "", art_w, art_h,
            "inset {}px".format(margin) if border else "border off, full bleed"))
        return (torch.cat(frames, dim=0), card_w, card_h, summary)


class TarotCardTitle:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("card", "summary")
    FUNCTION = "letter"
    DESCRIPTION = (
        "Set a card's name and numeral onto it, with real letter-spacing — wire a deck's "
        "titles in and each card is lettered with its own."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "title": ("STRING", {"default": "", "tooltip": "Wire a deck's titles in here."}),
                "numeral": ("STRING", {"default": ""}),
                "font_path": ("STRING", {
                    "default": "",
                    "tooltip": "Path to a .ttf or .otf. Blank falls back to Pillow's built-in "
                               "face, which is fine for placing the block but not for print.",
                }),
                "title_position": (["bottom", "top", "none"], {"default": "bottom"}),
                "numeral_position": (["top", "bottom", "none"], {"default": "top"}),
                "title_size_pct": ("FLOAT", {"default": 3.6, "min": 0.5, "max": 20.0, "step": 0.1,
                                             "tooltip": "Cap height as a percentage of card height."}),
                "numeral_size_pct": ("FLOAT", {"default": 3.0, "min": 0.5, "max": 20.0, "step": 0.1}),
                "colour": ("STRING", {"default": "#e8dcc0"}),
                "tracking_px": ("INT", {"default": 6, "min": -50, "max": 200,
                                        "tooltip": "Letter-spacing. Display lettering wants some."}),
                "letter_case": (["UPPERCASE", "as written", "lowercase", "Title Case"], {
                    "default": "UPPERCASE"}),
                "margin_pct": ("FLOAT", {"default": 3.0, "min": 0.0, "max": 40.0, "step": 0.1,
                                         "tooltip": "Distance from the card edge, as a percentage of height."}),
                "line_gap_px": ("INT", {"default": 10, "min": 0, "max": 400}),
                "stroke_width_px": ("INT", {"default": 0, "min": 0, "max": 30}),
                "stroke_colour": ("STRING", {"default": "#000000"}),
                "rule": (["none", "above title", "below title", "both"], {"default": "none"}),
                "rule_width_px": ("INT", {"default": 2, "min": 1, "max": 40}),
                "rule_length_pct": ("FLOAT", {"default": 40.0, "min": 5.0, "max": 100.0, "step": 1.0}),
                "rule_gap_px": ("INT", {"default": 12, "min": 0, "max": 200}),
            }
        }

    def letter(self, image, title, numeral, font_path, title_position, numeral_position,
               title_size_pct, numeral_size_pct, colour, tracking_px, letter_case, margin_pct,
               line_gap_px, stroke_width_px, stroke_colour, rule, rule_width_px,
               rule_length_pct, rule_gap_px):
        casing = {"UPPERCASE": str.upper, "lowercase": str.lower,
                  "Title Case": str.title}.get(letter_case, lambda text: text)
        batch = image if image.dim() == 4 else image.unsqueeze(0)
        frames = []
        placed = []

        for offset in range(batch.shape[0]):
            card = tensor_to_pil(batch[offset]).convert("RGB")
            draw = ImageDraw.Draw(card)
            margin = int(round(margin_pct / 100.0 * card.height))

            items = []
            if numeral and numeral_position != "none":
                items.append(("numeral", casing(numeral), numeral_position,
                              load_font(font_path, numeral_size_pct / 100.0 * card.height)))
            if title and title_position != "none":
                items.append(("title", casing(title), title_position,
                              load_font(font_path, title_size_pct / 100.0 * card.height)))

            for edge in ("top", "bottom"):
                block = [item for item in items if item[2] == edge]
                if not block:
                    continue
                heights = [int(math.ceil(font.getbbox("Hg")[3])) for _, _, _, font in block]
                total = sum(heights) + line_gap_px * (len(block) - 1)
                y = margin if edge == "top" else card.height - margin - total

                for (kind, text, _, font), height in zip(block, heights):
                    span = text_width(draw, text, font, tracking_px)
                    x = (card.width - span) // 2
                    if kind == "title" and rule != "none":
                        length = int(rule_length_pct / 100.0 * card.width)
                        left, right = (card.width - length) // 2, (card.width + length) // 2
                        if rule in ("above title", "both"):
                            top = y - rule_gap_px - rule_width_px
                            draw.rectangle([left, top, right, top + rule_width_px - 1], fill=colour)
                        if rule in ("below title", "both"):
                            bottom = y + height + rule_gap_px
                            draw.rectangle([left, bottom, right, bottom + rule_width_px - 1],
                                           fill=colour)
                    draw_tracked(draw, (x, y), text, font, colour, tracking_px,
                                 stroke_width=stroke_width_px,
                                 stroke_fill=stroke_colour if stroke_width_px else None)
                    y += height + line_gap_px
            placed.append(casing(title) if title else "")
            frames.append(pil_to_tensor(card))

        summary = "lettered {} card(s): {}".format(len(frames), ", ".join(p for p in placed if p))
        return (torch.cat(frames, dim=0), summary)


class TarotPalette:
    CATEGORY = DECK_CATEGORY
    RETURN_TYPES = ("STRING", "IMAGE", "STRING")
    RETURN_NAMES = ("hex_codes", "swatch", "summary")
    FUNCTION = "build"
    DESCRIPTION = (
        "A colour reference as both text and image: write hex codes, or pull them off a card "
        "you have already approved. The swatch goes in a reference slot, where a model follows "
        "it far more reliably than it follows hex codes in a prompt."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "hex_codes": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Hex codes in any surrounding text — #B3121B, #7A0E14, #E8C547. "
                               "Both #rgb and #rrggbb are read; anything else is ignored.",
                }),
                "extract_count": ("INT", {
                    "default": 0, "min": 0, "max": 24,
                    "tooltip": "With an image connected, pull this many dominant colours off "
                               "it. They are appended after any hex codes written above.",
                }),
                "layout": (["strip", "grid"], {"default": "strip"}),
                "width": ("INT", {"default": 1024, "min": 32, "max": 4096}),
                "height": ("INT", {"default": 256, "min": 32, "max": 4096}),
                "labels": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Print each code on its block. Useful to look at, noise to a "
                               "model — leave it off for a swatch you are going to send.",
                }),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip": "Source for extract_count."}),
            },
        }

    def build(self, hex_codes, extract_count, layout, width, height, labels, image=None):
        codes = parse_hex_codes(hex_codes)
        if image is not None and extract_count:
            batch = image if image.dim() == 4 else image.unsqueeze(0)
            for code in extract_palette(tensor_to_pil(batch[0]), int(extract_count)):
                if code not in codes:
                    codes.append(code)
        if not codes:
            raise NodeError(
                "No colours. Write hex codes, or connect an image and raise extract_count.")
        swatch = palette_swatch(codes, int(width), int(height), layout, labels)
        summary = "{} colour(s): {}".format(len(codes), " ".join(codes))
        return (", ".join(codes), pil_to_tensor(swatch), summary)


NODE_CLASS_MAPPINGS = {
    "SPTarotDeck": TarotDeck,
    "SPTarotPalette": TarotPalette,
    "SPTarotDeckLoad": TarotDeckLoad,
    "SPTarotPrompt": TarotPrompt,
    "SPTarotCardFrame": TarotCardFrame,
    "SPTarotCardTitle": TarotCardTitle,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SPTarotDeck": "Tarot Deck Manifest",
    "SPTarotPalette": "Tarot Palette",
    "SPTarotDeckLoad": "Tarot Deck Slot Loader",
    "SPTarotPrompt": "Tarot Prompt Builder",
    "SPTarotCardFrame": "Tarot Card Frame",
    "SPTarotCardTitle": "Tarot Card Lettering",
}
