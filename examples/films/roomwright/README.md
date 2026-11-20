# Roomwright's intro

This is the scene file for the 30-second intro of [Roomwright](https://github.com/pyros-projects/roomwright),
a browser game. It has eight landscape shots on a 400 × 225 grid at scale 4.

```sh
uv run python .agents/skills/pixel-cat-film/scripts/render.py \
  examples/films/roomwright/story.json output/roomwright-intro.mp4
```

This renders the film without its captions. Roomwright's build burns them in after rendering, in the
game's own font, so camera moves leave the lettering alone.

Roomwright's [`intro/`](https://github.com/pyros-projects/roomwright/tree/main/intro) folder holds the
rest: the prompts for the eight paintings, the model sheet they were steered by, `make_story.py`, which
writes this scene file, and `build.sh`.
