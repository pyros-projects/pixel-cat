# Studio

The studio is a page on your own machine for drawing and animating a project, alone or together with an agent.

```sh
uv run pixel-cat studio sprite.pixelcat --size 32x32 --open     # creates the project if it is not there
uv run pixel-cat studio sprite.pixelcat --agent                  # from an agent's session: the chat is wired to it
```

The command prints the address (`http://127.0.0.1:4178` unless you pass `--port`) and keeps running. The server listens on this machine only. Every change is saved to the project file at once.

## The page

- **Canvas** in the middle. Scroll to zoom, hold Space and drag to pan, `0` fits the picture to the window.
- **Tools** on the left: pencil `B`, eraser `E`, fill `G`, colour picker `I` (or hold Alt), line `L`, rectangle `R` (Shift fills it), select `M`, move `V`, pan `H`. Below them the brush size (`[` and `]`), mirror `X`, and fit.
- **Timeline** at the bottom: one column per frame, one row per layer, each cel as a small picture. A click picks the frame and the layer at once. Space plays, Left and Right step through the frames, Up and Down through the layers. Onion skin shows the neighbouring frames faintly.
- **Colour, layers and chat** on the right. Layers can be hidden, locked, renamed (double-click), faded, reordered and merged. The palette comes from the art with one click.
- **Selections:** drag with the select tool, move with the move tool, Enter sets the moved piece down, Delete clears, Ctrl+C and Ctrl+V copy and paste, Escape lets go.
- **Import:** drop a picture, a GIF or a video on the canvas. It is converted first: to fit the canvas, the way pixel-cat recommends, or not at all. It lands on a new layer, after the last frame, or in place of the project.
- **Export:** PNG of the frame, GIF, MP4, WebM, or the project file.
- **Undo** (Ctrl+Z) and Redo cover everything, also what the agent did. The top bar names the last change and who made it.

## Working with an agent

With `--agent`, what you write in the chat goes to the agent that started the studio. It sees what you have open, the frame, the layer, your selection and your colour, and it can look at the picture, change the project and answer. Its changes appear as they happen, and a line in the chat offers to take them back in one go.

The agent's side is four commands that talk to the running studio:

| Command | What it does |
| --- | --- |
| `pixel-cat wait` | blocks until you write, then prints the message and what you have open, and exits |
| `pixel-cat look -o frame.png` | prints the project's shape and what you have open; `-o` saves the frame you are on, enlarged (`--frame`, `--layer`, `--scale`) |
| `pixel-cat op '{"op": ...}'` | changes the project with one operation |
| `pixel-cat say "text"` | answers in the chat (`-` reads the text from stdin) |
| `pixel-cat undo` | takes back the last change |

A message arrives as one line:

```
YOU [sprite.pixelcat, frame 2, layer 1 "Ink", selection x 4 y 6, 12 by 8, colour #f2a65a]: make the outline darker here
```

The loop for an agent: start the studio in the background, give the person the address, run `wait` in the background, and end the turn. When `wait` exits, read its output, `look`, change, `say`, and start `wait` again before ending the turn. While `wait` runs, the page shows the agent as listening.

## Operations

Frames and layers are counted from 0; layer 0 is the bottom one. An operation that cannot be carried out changes nothing and says why.

| Operation | Settings |
| --- | --- |
| `draw` | `layer`, `frame`, `color` (`"#rrggbb"`, or `null` to erase), and any of `points` `[[x, y], ...]`, `line` `[x0, y0, x1, y1]`, `rect` `[x, y, width, height]` (with `outline: true` only its border), `fill` `[x, y]` |
| `clear` | `layer`, `frame` |
| `add-layer` | `name`, `at` |
| `remove-layer`, `merge-down` | `layer` |
| `move-layer` | `layer`, `to` |
| `set-layer` | `layer`, and any of `name`, `visible`, `locked`, `opacity` |
| `add-frame` | `at`, and `copy` to duplicate a frame |
| `remove-frame` | `frame` |
| `move-frame` | `frame`, `to` |
| `set-frame` | `frame` (a number or `"all"`), `duration` in milliseconds |
| `palette` | `colors` |
| `palette-from-art` | |
| `edits` | `layer`, `frames` (`"all"` or a list), `steps`: the [finishing steps](recipes.md#edits) that keep the size |
| `place` | `frames` (PNG images as base64), `name`, `frame`, `x`, `y`, `durations`: converted art as a new layer |
| `resize` | `width`, `height`, and `x`, `y` to shift the art |

```sh
pixel-cat op '{"op": "add-layer", "name": "Ink"}'
pixel-cat op '{"op": "draw", "layer": 1, "frame": 0, "color": "#101018", "line": [4, 20, 27, 20]}'
pixel-cat op '{"op": "edits", "layer": 0, "steps": [{"op": "replace", "from": "#f2a65a", "to": "#5ec4c8"}]}'
```

## Project files

A project is one JSON file ending in `.pixelcat`:

```json
{"format": "pixel-cat project", "version": 1, "width": 32, "height": 32,
 "palette": ["#101018", "#f2a65a"],
 "frames": [{"duration": 120}, {"duration": 120}],
 "layers": [{"name": "Colour", "visible": true, "locked": false, "opacity": 1,
             "cels": ["<PNG, base64>", null]}]}
```

Layers are listed from the bottom up. Every layer has one cel per frame; an empty cel is `null`, a filled one is a PNG of the project's size. A project has up to 256 frames, 32 layers and 1024 cells per side.

The converter reads and writes projects like any other file:

```sh
pixel-cat convert walk.gif -o walk.pixelcat --mode grid --grid 48 --colors 12   # every frame becomes a cel
pixel-cat convert walk.pixelcat -o walk.gif --scale 4                           # the visible layers, flattened
```

A project as input is taken as it is: it accepts `edits`, a `loop` and exports, and no conversion settings.
