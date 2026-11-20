"""Named palettes, and the colour-space helpers that choose between colours."""

import json
from pathlib import Path

import numpy as np

PALETTES = json.loads(Path(__file__).with_name("palettes.json").read_text())


def oklab(rgb):
    """sRGB colours (n, 3) in 0..255 to Oklab, a space where distance matches how different colours look.

    Bjorn Ottosson's public-domain transform: https://bottosson.github.io/posts/oklab/
    """
    x = np.asarray(rgb, dtype=np.float64) / 255
    linear = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    lms = np.cbrt(linear @ np.array([[0.4122214708, 0.2119034982, 0.0883024619],
                                     [0.5363325363, 0.6806995451, 0.2817188376],
                                     [0.0514459929, 0.1073969566, 0.6299787005]]))
    return lms @ np.array([[0.2104542553, 1.9779984951, 0.0259040371],
                           [0.7936177850, -2.4285922050, 0.7827717662],
                           [-0.0040720468, 0.4505937099, -0.8086757660]])


def spread(rgb, count):
    """Pick `count` colours of a palette that stay as far apart as possible, in the palette's own order.

    The darkest and the lightest colour come first; each further pick is the colour farthest from those
    already chosen. A short palette cut this way keeps its whole range instead of only its first entries.
    """
    lab = oklab(rgb)
    chosen = [int(lab[:, 0].argmin())]
    if count > 1:
        chosen.append(int(lab[:, 0].argmax()))
    while len(chosen) < min(count, len(rgb)):
        gap = ((lab[:, None, :] - lab[chosen][None, :, :]) ** 2).sum(axis=2).min(axis=1)
        gap[chosen] = -1
        chosen.append(int(gap.argmax()))
    return np.asarray(rgb)[sorted(set(chosen))]
