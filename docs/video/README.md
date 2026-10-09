# The tutorial video

`make.py` makes the deploy tutorial from scripts, so any part can be redone.
Recording runs everything for real, once: [VHS](https://github.com/charmbracelet/vhs) types the
commands and [asciinema](https://asciinema.org) keeps each terminal clip's session (a `.cast`, the
raw output and its timing), Playwright records the browser. The video replays the casts in VHS,
fast-forwarding the long waits, and ffmpeg puts the clips on a background, fades between them and
burns in the captions. So captions, order, speed and colors change without the server. The
videos come out silent, in 1080p; the music goes on later.

| File | What it holds |
|---|---|
| [`videos.py`](videos.py) | each video's clips in order, with their captions: what to edit |
| [`tapes/<video>/<clip>.tape`](tapes/) | what each terminal clip types and waits for ([VHS's syntax](https://github.com/charmbracelet/vhs#vhs-command-reference)) |
| [`tapes/settings.tape`](tapes/settings.tape) | the terminal's look: font, size, colors |
| [`browser.py`](browser.py) | the browser clips: what they open, click and type |
| [`make.py`](make.py) | recording and joining; the layout and the captions' style |

Everything it makes lands in `out/` (git-ignored); the video is `out/tutorial.mp4`.

## What it needs

In the dev container (or anywhere with the platform's `uv`):

* `ffmpeg` and `asciinema` (`sudo apt-get install ffmpeg asciinema`)
* [VHS](https://github.com/charmbracelet/vhs/releases) and [ttyd](https://github.com/tsl0922/ttyd/releases)
  on the `PATH` (VHS's `.deb`; ttyd's static binary)
* the fonts [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono/releases) and
  [Inter](https://github.com/rsms/inter/releases) in `~/.local/share/fonts` (then `fc-cache -f`)

The **tutorial** starts from nothing, as a newcomer would: it clones the platform (this checkout,
with its changes) into `~/inatrace-platform`, makes the VM `demo` with `inatrace vm create demo`
(after removing an earlier one, and its downloaded image, so the video shows it all), and
deploys the instance `demo` there.

## Making it

```
uv run python docs/video/make.py tutorial                         # the video, from what is recorded
uv run python docs/video/make.py tutorial --record                # record everything, from nothing (~25 min)
uv run python docs/video/make.py tutorial --record --only status  # record these clips again
uv run python docs/video/make.py tutorial --record --from up      # record from this clip on
```

What is recorded stays in `out/<video>/`: `casts/` (the sessions), `takes/` (each terminal clip as
it ran, for a look), `clips/` (the browser's, and the terminal's replayed). Each step keeps its
result until what it comes from changes: a caption costs the final encode (a minute or two); a
clip's `speed` or `hold`, replaying that clip; `tapes/settings.tape`, replaying all of them. A
change in what a clip types or shows needs it recorded again; so does one in the terminal's size
(font size, padding), since the CLI laid its output out for the width it was recorded at.

A clip that depends on the server's state names it in `setup` (in `videos.py`), commands run off
camera first: `up` stops the instance before, so it has something to start. Where the state
cannot be set up again cheaply (the VM before Docker, for `init`), record `--from` there.
