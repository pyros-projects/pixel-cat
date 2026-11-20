# Recipes

A recipe is one conversion job as JSON. `pixel-cat run recipe.json` executes it; `pixel-cat run -` reads it from stdin. Paths inside a recipe resolve against the recipe's own folder, so a recipe can sit next to the files it uses.

```json
{
  "input": "bob.gif",
  "convert": {"mode": "grid", "grid": 96, "sampling": "crisp", "colors": 16},
  "media": {"frames": "all", "speed": 100, "stability": 24},
  "export": [
    {"path": "out/bob.gif", "scale": 4},
    {"path": "out/frame-2.png", "frame": 2},
    "out/bob.mp4"
  ]
}
```

## `input`

A still image (PNG, JPEG, WebP and whatever else Pillow reads), a GIF, or a video. Videos need FFmpeg on the PATH. The word `clipboard` in place of a file takes the picture that is on the system clipboard.

## `convert`

`mode` is `recommended` (the default), `grid`, `faithful` or `artist`. Each mode takes only its own settings and rejects the rest.

| Setting | Modes | Values | Default |
| --- | --- | --- | --- |
| `contrast` | recommended | `true` reduces the picture to its few big shapes | `false` |
| `grid` | grid | longest side in cells, 1 to 2048, or `"native"` | 160 |
| `sampling` | grid | `exact`, `area`, `crisp`, `ink` | `exact` |
| `width` | faithful, artist | output width in pixels, 32 to 2400 | 800 |
| `pixel` | faithful, artist | size of one cell, 2 to 40 | 8 |
| `strength` | artist | 1 to 3 | 2 |
| `colors` | grid, faithful, artist | 2 to 256 | 12 in grid, otherwise 16 |
| `palette` | grid, faithful, artist | `"source"`, a palette name, or a list of `"#RRGGBB"` colors | `source` in grid, otherwise `gameboy` |
| `symmetry` | grid, faithful, artist | `off`, `left`, `right` | `off` |
| `alpha` | grid, faithful, artist | `hard`: every cell solid or empty; `soft`: cells keep their transparency, for shadows and glows | `hard` |

With `palette: "source"` the picture keeps its own colors, reduced to `colors`. With a palette name in faithful or artist mode, `colors` picks that many entries of the palette, spread over its whole range. `pixel-cat catalog` lists the palette names. They run from 4 colors (`gameboy`) to the large ones with full shading ramps: `lantern32`, `painter48`, `meadow48`, `harbor48` and `spectrum64`.

## `media`

For GIFs, videos and sprite sheets.

| Setting | Values | Default |
| --- | --- | --- |
| `frames` | how many frames to take, 1 to 256, or `"all"` | all frames of a GIF, 24 of a video |
| `speed` | playback speed in percent, 25 to 300 | 100 |
| `stability` | how much a cell may change over the loop and still count as standing still, 0 to 255; 0 switches the steadying off | 24 |

Steadying applies in grid and recommended mode.

## `sheet`

Treats a still image as a sprite sheet: `columns` and `rows` (1 to 16 each) cut it into frames in reading order, `delay` is the frame duration in milliseconds (default 120).

## `edits`

Finishing steps on the converted art, applied in order to every frame.

```json
"edits": [
  {"op": "cutout"},
  {"op": "trim", "margin": 1},
  {"op": "outline", "color": "#101018"}
]
```

| Step | What it does | Settings |
| --- | --- | --- |
| `cutout` | makes the background transparent: everything that connects to the picture's edge through cells close to a corner color | `tolerance` (0 to 255, default 24), `color` to name the background, `fringe` (default true) also removes a thin rim tinted by the background |
| `trim` | crops to the drawing; an animation is cropped to the box that holds every frame | `margin` in cells |
| `outline` | draws a one-cell outline around everything opaque; the picture grows by one cell on each side | `color`, `corners` (also close diagonal gaps), `grow` |
| `pad` | adds cells around the picture | `cells`: one number or `[left, top, right, bottom]`; `color` |
| `flip` | mirrors | `axis`: `x` or `y` |
| `shift` | moves the picture by whole cells and wraps around, for checking how a tile repeats | `x`, `y` |
| `replace` | turns one color or a list of colors into another | `from`, `to`, `tolerance` |
| `clean` | removes specks: small enclosed groups of cells join the color around them when the two look alike | `strength`: `gentle`, `balanced` or `strong` |
| `thin` | thins drawn lines to one cell and closes small breaks. Lines are dark cells, or cells near `color`. Broad dark areas and small shapes such as an eye stay as they are. | `color`, `tolerance` (0 to 180, default 28), `width`: the thickest line in cells (2 to 8, default 4), `gap`: close breaks of up to 1, 2 or 3 cells (default 0) |
| `halo` | removes the thin rim of in-between color that smooth scaling leaves beside dark lines. Run it before `thin`. | `strength`: `gentle` or `stronger` |
| `fringe` | removes loose bits at the edge of a cut-out drawing: small groups of one color with mostly empty cells around them | `max`: the largest group in cells (default 4), `contact`: the share of empty surroundings from which a group goes (default 0.5) |
| `hold` | makes an area of an animation stand still: its cells are copied from one frame into the others, and everything outside keeps moving | `from` (the frame to copy, default 0), `frames` (`"all"` or a list), and the area: `rect` `[x, y, width, height]`, `points` (a polygon) or `cells` (a list of `[x, y]`), all in cells |

`replace`, `clean`, `thin`, `halo` and `fringe` also take an area (`rect`, `points` or `cells`, in cells) and then change nothing outside it. With `"protect": true` it is the other way round: nothing inside the area changes.

A step with a wrong setting stops the job and names itself: `Edit 2 (outline): color is a colour written as #RRGGBB`. In a recipe with both `edits` and `loop`, the edits come first.

## `loop`

Turns a still picture into a seamless loop. The picture is converted first, then the regions move.

```json
"loop": {
  "seconds": 4,
  "fps": 8,
  "regions": [
    {"kind": "clouds", "amount": 1, "area": [[0, 0], [1, 0], [1, 0.3], [0, 0.25]]},
    {"kind": "water", "area": [0, 0.58, 1, 1]}
  ]
}
```

| Setting | Values | Default |
| --- | --- | --- |
| `seconds` | length of the loop, 1 to 20 | 4 |
| `fps` | 4, 6, 8, 10 or 12 | 8 |
| `seed` | changes which lights twinkle when | 1 |
| `regions` | a list; each region has a `kind`, an `area` and an `amount`. `fireflies`, `leaves` and `birds` also take a `color` or a list of `colors`; `leaves` take `wind`: `left`, `right` or `none`. | |

| Kind | Motion |
| --- | --- |
| `clouds` | drift to the right at 4 cells per second times `amount` |
| `rise` | drift upwards, for smoke and steam |
| `water` | rows slide sideways in waves, more towards the front |
| `sway` | the top of the area moves, its foot stays, for leaves and grass |
| `twinkle` | small bright cells go dark for a moment, for stars and lamps |
| `fireflies` | small lights wander around their spot and blink; about one per 600 cells of the area, times `amount` |
| `leaves` | fall through the area, tumble and drift with the `wind`; about one per 900 cells, times `amount` |
| `birds` | cross the area from left to right and beat their wings; two, times `amount` |

`area` is a box `[left, top, right, bottom]`, a polygon of `[x, y]` points, or `"auto"`. Numbers are fractions of the picture, so an area holds for every grid size. A cell belongs to an area when its center lies inside. `"auto"` guesses: the sky is each column from the top down to the first clearly darker cell, the water is everything below the strongest horizontal border. Look at the picture and draw the areas when the guess is off. `amount` scales the motion (up to 8).

An area painted in the studio is written as `{"cells": [[x, y], ...]}`, in cells of the picture itself.

Cells outside the regions never change. Inside, cells only move, so the colors stay the picture's own. Fireflies, leaves and birds are the exception: they draw a few cells in colors of their own, on top of whatever moves beneath them, and each runs a whole number of rounds per loop. Motions that come back on their own run a whole number of cycles. Clouds and smoke keep going, so over the last third of the loop the take that began one loop earlier fades in.

## `export`

One path, one object, or a list of them. The file extension chooses the format: `.png`, `.gif`, `.mp4`, `.webm`.

| Setting | Meaning |
| --- | --- |
| `path` | where to write |
| `scale` | enlarge by a whole number, 1 to 64, or `"display"` for a size that is easy to look at. PNG and video default to `display`, GIF to 1. |
| `frame` | PNG only: the zero-based frame of an animation |
| `format` | overrides the extension |

Transparent cells stay transparent in PNG and GIF. In MP4 and WebM they are black. By default a converted cell is either solid or empty: cells that are less than half covered become empty, the rest solid. With `alpha: "soft"` cells keep their transparency in PNG; GIF knows only solid and empty.

## The result

```json
{
  "input": "/abs/bob.gif",
  "stabilized": 8110,
  "width": 96, "height": 96, "frames": 8,
  "durations": [90, 90, 90, 90, 90, 90, 90, 90],
  "colors": 13, "displayScale": 6,
  "outputs": [{"path": "/abs/out/bob.gif", "format": "gif", "bytes": 49123, "width": 384, "height": 384, "frames": 8}]
}
```

`width` and `height` are in cells. `colors` counts the distinct opaque colors over all frames. `stabilized` is the number of cells that were held steady. In recommended mode, `recommendation` holds the settings that were chosen: `width`, `height`, `sampling`, `colors`, `cleanup` and `contrast`.

Outputs are written to temporary files first and moved into place only when every export of the job succeeded.

## Checking an animation

```sh
pixel-cat check walk.gif -o marked.gif
```

`check` reads a GIF, a video, a project or a picture as it is and looks for four things:

| Finding | What it is |
| --- | --- |
| `flicker` | a cell that differs from the frame before and the frame after, while those two agree. The animation is read as a loop. |
| `fragments` | groups of at most `--fragment` touching cells of one color (default 3) |
| `holes` | an empty cell with color on all four sides |
| `palette` | a color outside the palette: a project's own palette, or `--palette` |

The result lists the counts per frame and the areas where findings gather, largest first, each as a box `[x, y, width, height]`. A flickering area's box goes straight into a `hold` step. `-o` writes a GIF, or a PNG of the worst frame, with the findings tinted: flicker magenta, fragments yellow, holes cyan, off-palette red.

Every finding is a hint for a person to look at. A star may be a one-cell fragment, and a lamp may be meant to blink.
