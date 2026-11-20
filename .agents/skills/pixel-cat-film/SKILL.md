---
name: pixel-cat-film
description: Create or revise a short pixel-art film in the pixel-cat checkout. Use for animated cutscenes, 15-second portrait shorts, or README demos that need pixel-cat artwork, timed motion, original Foley, an MP4 and a looping preview.
metadata:
  internal: "true"
---

# Make a pixel-cat film

Work from the root of the pixel-cat checkout. Run `uv sync --locked`. Install FFmpeg with the libx264 and AAC encoders.

## Choose the four shots

Write one sentence naming the action and what it sets in motion. In *Platform Zero*, a signalman throws a lever and a train crosses an empty platform. Give the action a sound the viewer can recognize with their eyes shut.

If the user supplies a reference, watch it at normal speed. Scrub through its cuts and listen for the sound that connects them. Take its framing, timing and use of light into the new story. Draw original characters and props for the requested theme.

For a 15-second portrait film, budget about three seconds for the hand or mechanism, three for its immediate effect, two or three for a close detail, and the remaining time for the reveal. Carry one color or shape through the cuts. Leave room around the final subject for its entrance and movement. The audience should be able to follow the action with the sound muted.

## Make the art

1. Draw or generate a portrait sheet with four equal, edge-to-edge panels in reading order. Each panel must have the same aspect ratio as the film. Specify the same wardrobe, lighting and time of day in all four. Keep borders and lettering off the sheet. Leave the reveal subject out of the fourth panel.
2. Make the moving subject as a separate PNG with transparency. Give it the orientation it will need in the shot. Check the alpha channel and the silhouette on a dark background. The six [example prompts](../../../examples/films/ART.md) include directions for the background, subject and camera.
3. Feed both images through the converter. Replace the example filenames below with the new film's inputs. Large unprocessed paintings can live in `output/`.

```sh
skill=.agents/skills/pixel-cat-film
film=examples/films/my-film
uv run python "$skill/scripts/prepare.py" output/plates.png output/subject.png "$film"
```

The command splits the sheet into four 240 × 360 PNGs and trims the sprite. It runs `pixel-cat convert` on all five assets with the crisp sampler, 64 colors and one pixel per cell. Inspect each panel **after** conversion. Locate the hand, hinge, highlight and horizon in native pixel coordinates. Trim paths or repaint edges if an actor crosses a cut line.

## Animate the shot

Copy the closest `story.json` into the new film directory and edit its coordinates against the new plates. Start with [Tide Bell](../../../examples/films/tide-bell/story.json) for a tug and swinging bell, [Platform Zero](../../../examples/films/platform-zero/story.json) for a side-on pass behind a foreground character, or [Emberwing](../../../examples/films/emberwing/story.json) for a growing crack and wingbeats.

- Set `size` to the plate dimensions. `fps` is the drawing rate (6, 8, 12 or 24). `scale` is the integer enlargement. At 12 drawings per second, 180 drawings fill 15 seconds. Use a cut on a whole drawing frame. Each drawing is held until the next one, and the film plays at 24 frames per second.
- A shot names its `plate` and `seconds`. Camera keys use `[seconds, centerX, centerY, zoom]`, where 1 shows the whole frame. Shot times start at zero at each cut. Space keyframes in increasing time order. The renderer eases between keys and holds the endpoints. The camera is smooth: it moves on every one of the 24 frames, rests between cells and zooms by any factor, and cells keep their sharp edges without crawling. For a camera that snaps to whole cells and moves at the drawing rate, set `"lens": "grid"` at the top of the story.
- Move the cause of the action. For a hand pulling a rope, `deform` with a `center`, two-axis `radius` and `move` keys of `[seconds, dx, dy]` pulls nearby painted pixels with the hand. Set `hinge_y` above a swinging bell to hold its attachment steady.
- Use a layer with `image` for a free-moving sprite. Set `width` in native cells. Each pose is `[seconds, x, y, xScale, yScale, degrees, opacity]`. Position is measured from the upper left of the plate to the layer's `pivot`. The default pivot is the center. Set it to `[0.5, 1]` to place wheels on a rail. For a prop painted onto a plate, `cutout` lifts a polygon and paints the ground behind it into the hole. Trace the outline around the whole prop, rim and glow included. The renderer widens an outline that cuts through the rim, and stops with a message when it is still off, because any part of the prop left on the plate stays behind as a ghost when the prop moves. The first pose has to put the pivot exactly where it is in the painting, so that the first drawing equals the plate; the renderer names the position if it differs. Paint such a prop with free space around it: the simpler the ground behind it, the better the hole can be filled.
- For a pulse or wingbeat, add `sway` as `[cyclesPerSecond, dx, dy, xScaleDelta, yScaleDelta, degrees]`. Use `ripple` in cells to bend long tendrils. A shot's `foreground` polygons restore pieces of the original plate over the moving subject. Trace the keeper if the train must pass behind them.
- Time effects to the action. A `light` uses `xy`, `radius`, RGB `color` and `level` keys of `[seconds, strength]`. A `ring` expands between `start` and `end`. A `stroke` reveals a polyline as its level moves from 0 to 1. For `particles`, set the emission `box`, velocity in cells per second, count, life in seconds and shape. Try `bubble`, `rain`, `steam` or `spark`. A `path` of `[seconds, x, y]` moves the light or particle emitter. For a moving emitter, the emission box is relative to that path.
- Place `sound.cues` at **seconds from the start of the film**. A cue needs `at`, `voice`, `hz`, `seconds` and `gain`. Available voices are `knock`, `bell`, `hiss`, `wheel` and `tone`. Set `pan` between -1 and 1 and use `to_hz` for a pitch change. Leave space around the cue that explains the cut. Set the story's `seed` to reproduce the particles and Foley.

## Render, review and watch

```sh
uv run python "$skill/scripts/render.py" "$film/story.json" output/my-film.mp4
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,nb_frames,channels:format=duration -of json output/my-film.mp4
ffmpeg -v error -y -filter_complex_threads 1 -i output/my-film.mp4 \
  -filter_complex 'fps=6,scale=160:240:flags=neighbor,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=none' \
  -loop 0 output/my-film.gif
```

```sh
uv run python "$skill/scripts/review.py" "$film/story.json" output/review
```

Review before you watch. Motion hides mistakes that a single drawing shows at once. The command writes enlarged sheets: every shot drawing by drawing, and a close-up strip for every moving part, with the plate as painted and with the prop lifted beside it. Open every sheet and ask of each: Does anything stay behind where a part used to be, a ghost, a rim, a loose crumb? Does the part stay attached where it should and move the way the real thing would? Is the ground behind a moved part believable? The command also prints findings and exits with 1 when one has to be fixed, such as a prop that does not start where it was painted. To choose between several paintings of the same shot, `closeup.py --box left,top,right,bottom -o sheet.png a.png b.png c.png` puts the same detail of each side by side, enlarged.

Play the MP4 once with sound and once muted. Scrub the first movement, each cut and the last second. Look for exposed pixels where props used to be, feet or wheels floating above the ground, and a final silhouette that merges with the background. For the portrait preset, verify 15.000 seconds, 360 frames, 720 × 1080 H.264 video and stereo AAC. View the GIF at README size. Link the preview to the MP4 so readers can hear the cue. Keep the story and normalized PNGs beside the published film's source examples.
