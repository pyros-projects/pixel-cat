# Working on pixel-cat

pixel-cat turns pictures, GIFs and videos into pixel art, has a local studio to draw and animate in, and a skill
that makes short films. It is plain Python with numpy and Pillow; FFmpeg is needed for video.

```sh
uv sync
uv run python -m unittest discover -s tests
uv run pixel-cat catalog
```

Always use `uv`. The README says what the tool promises; `docs/recipes.md` and `docs/studio.md` are the references.

## How work is organised

1. **Every change starts as an issue**, from a template: Feature, Task or Bug.
2. **A feature** describes a result. Its title starts with an emoji that fits it, so features stand out in a list.
   The label `status:ready-for-refinement` means it is written down and not yet refined: its technical notes name
   the open questions. Settle those with Pyro before building, then split the feature into **tasks**, attached as
   its sub-issues, and take the label off.
3. **What is learned goes into the feature's comments**: an experiment and its result, a problem and what solved
   it. The comment carries the point itself, so nobody has to dig through tasks to find it.
4. **One branch and one pull request per feature.** The pull request says `Closes #n` for the feature and its
   tasks, and carries its tests and its doc changes. Pyro merges.
5. **Scope:** build what the issue asks. Anything else you notice becomes a new issue or a question.

## House rules

- **Everything here is written for this project.** Do not copy code, data, palettes, fonts or art from other
  tools, sites or projects, and do not describe this project by comparing it with other products. Formats and
  platforms we read or write are named plainly.
- **Tests build their own inputs** and check what a feature promises: sizes, colour limits, which cells change and
  which do not. No test depends on a file from outside the repository.
- **Look before you show.** Open what you made. For anything that moves, put every frame on a sheet and look at
  each one before it goes to a person; a loop that is fine at speed can hide a broken frame. In the studio, press
  every new control with real pointer input, button down and up at its place on the screen. A click sent by
  script reaches the control directly and passes where a hand's click was caught by the canvas beneath it.
- **Try it at working size.** A project of 384 by 216 cells with 80 frames and jointed layers shows what a
  16-cell test cannot: how long a change takes, and what a held drawing costs.
- **A new kind of defect gets a mechanism, not a promise:** a check in the tool, an error that names the place, or
  a test.
- **Plain English** in code, docs, issues, commits and pull requests. Say what a thing does; a reader should not
  need the code to follow the docs.
- No secrets, local paths or personal names in the repository.
