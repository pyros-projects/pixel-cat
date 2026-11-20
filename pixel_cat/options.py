"""Check conversion settings once, for the command line and for JSON recipes alike."""

import re

MODES = ("recommended", "grid", "faithful", "artist")
SAMPLING = ("exact", "ink", "crisp", "area")
OPTIONS = {
    "recommended": {"contrast"},
    "grid": {"grid", "sampling", "colors", "palette", "symmetry"},
    "faithful": {"width", "pixel", "colors", "palette", "symmetry"},
    "artist": {"width", "pixel", "colors", "palette", "symmetry", "strength"},
}


def keys(obj, permitted, label):
    if not isinstance(obj, dict):
        raise TypeError(f"{label} must be a JSON object")
    extra = set(obj) - set(permitted)
    if extra:
        raise ValueError(f"Unknown {label} settings: {', '.join(sorted(extra))}")


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def conversion(raw):
    """Fill in defaults and reject anything a mode does not use."""
    mode = raw.get("mode", "recommended")
    if mode not in MODES:
        raise ValueError(f"Choose a mode from {', '.join(MODES)}")
    keys(raw, {"mode"} | OPTIONS[mode], mode)
    values = {
        "mode": mode, "grid": 160, "sampling": "exact", "width": 800, "pixel": 8, "strength": 2, "contrast": False,
        "colors": 12 if mode == "grid" else 16,
        "palette": "source" if mode in ("grid", "recommended") else "gameboy",
        "symmetry": "off",
    } | raw
    for name, low, high in (("width", 32, 2400), ("pixel", 2, 40), ("strength", 1, 3), ("colors", 2, 256)):
        integer(values[name], low, high, name)
    if values["grid"] != "native":
        integer(values["grid"], 1, 2048, "grid")
    if values["sampling"] not in SAMPLING or values["symmetry"] not in ("off", "left", "right"):
        raise ValueError("Unknown sampling or symmetry setting")
    if type(values["contrast"]) is not bool:
        raise ValueError("contrast must be true or false")
    palette = values["palette"]
    if isinstance(palette, list):
        if not 2 <= len(palette) <= 256 or not all(isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in palette):
            raise ValueError("A custom palette needs 2 to 256 colours written as #RRGGBB")
    elif not isinstance(palette, str):
        raise TypeError("palette must be a palette name, source, or a list of #RRGGBB colours")
    return values
