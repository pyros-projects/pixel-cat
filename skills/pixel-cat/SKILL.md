---
name: pixel-cat
description: Turn an image, GIF, video or sprite sheet into pixel art, or make a short pixel-art film with sound. Use when the user asks to pixelate a picture, make a sprite, a sprite sheet or a pixel-art GIF, reduce an image to a palette, or make a pixel-art cutscene or short film. On first use it installs pixel-cat (a git checkout with its own uv environment).
---

# pixel-cat

pixel-cat runs from its own checkout, the **home**. This skill installs the
home on first use, remembers where it is and runs the tool from there.

`<skill>` is the folder that contains this file. `<home>` is the folder that
`status` reports. The helper needs uv:

```bash
uv run --no-project <skill>/scripts/pixel_cat_home.py status
```

## 1. Find or install the home

- **`status: not installed`**: Tell the user in one sentence what the install
  does, then run `install`. It clones github.com/pyros-projects/pixel-cat
  into the default home that `status` shows and creates a uv environment with
  numpy and Pillow. Pass `--home <dir>` if the user wants the checkout
  somewhere else or already has a clone.
- **`status: home missing`**: Ask where the checkout went. `install --home
  <dir>` adopts an existing checkout. Without a checkout, it installs into the
  folder if the folder is empty.
- **`status: ok`**: Continue with step 2. FFmpeg is only needed for video
  input, for MP4 and WebM exports and for films. Install it with
  `brew install ffmpeg`, the Linux package manager or `winget install ffmpeg`.

The install needs git and uv. If one is missing, the helper says so. Tell the
user how to install it and stop.

## 2. Convert

Run the CLI against the home. The user's files stay where they are. Give
inputs and outputs as absolute paths:

```bash
uv run --project <home> pixel-cat catalog
uv run --project <home> pixel-cat convert /abs/photo.png -o /abs/photo-pixel.png
uv run --project <home> pixel-cat convert /abs/clip.gif -o /abs/clip-pixel.gif --scale 3
uv run --project <home> pixel-cat run /abs/recipe.json
```

A successful command prints one JSON object with the grid size, the number of
colors, the frame timings and the files it wrote. Errors go to stderr as JSON
with a nonzero exit.

- Start without settings. The recommended mode reads the picture and reports
  what it chose under `recommendation`. Look at the result, then adjust:
  `--mode grid` with `--grid`, `--sampling` and `--colors` gives full control.
- `catalog` lists the modes, samplers and palettes.
- The `<home>/README.md` section "Modes" explains what each mode and sampler
  does. `<home>/docs/recipes.md` is the reference for JSON recipes. Read it
  before you write one.
- For a sprite, add `--cutout --trim --outline "#101018"`: transparent
  background, tight crop, a rim. More finishing steps are in the recipe guide
  under `edits`.
- A still can become a loop (`--loop clouds,water`, or the `loop` key of a
  recipe). The guessed areas are rough. Look at the converted picture, then
  give each region its `area` as a box or a polygon in fractions of the
  picture, the way `<home>/examples/recipes/lake-loop.json` does.
- A recipe can live next to the user's files. Paths inside it resolve against
  the recipe's own folder.
- Look at what you made before you hand it over. A PNG at `--scale 1` has one
  pixel per cell; the default scale is easier to judge by eye.

## 3. Make a film

Read `<home>/.agents/skills/pixel-cat-film/SKILL.md` and follow it, with two
changes:

- Run its commands from the home (`cd <home>`), because `prepare.py` calls
  `uv run pixel-cat`.
- Put the new film's folder with its plates, `story.json` and renders in the
  user's project or in a folder they name. Do not put it in the home. Pass
  absolute paths. The films in `<home>/examples/films/` are templates to copy.

## Maintenance (only when the user asks)

| The user says | Run (`pixel_cat_home.py …`) |
|---|---|
| "update pixel-cat" | `update`: fetches the latest version and refreshes the environment |
| "where is pixel-cat", "which version" | `status` |
| "use my clone in X" | `install --home X` |

`npx skills update pixel-cat` updates this skill itself. The home's location
is stored in `~/.config/pixel-cat/config.json`, so an update keeps it.
`PIXEL_CAT_HOME` sets a different default home before the first install.
