"""Makes a video from videos.py. Recording runs the commands for real, once: VHS types them,
asciinema keeps each terminal clip's session (a .cast), Playwright records the browser. Making
the video replays the casts in VHS (fast-forwarded where videos.py says) and joins the clips
with ffmpeg, on the background, with fades and the captions burned in (an .ass subtitle file).
Everything lands in out/<video>/ (git-ignored); out/<video>.mp4 is the video.

    uv run python docs/video/make.py tutorial                         # the video, from what is recorded
    uv run python docs/video/make.py tutorial --record                # record everything, from nothing
    uv run python docs/video/make.py tutorial --record --only status  # record these clips again
    uv run python docs/video/make.py tutorial --record --from up      # record from this clip on

Each step keeps its result until what it comes from changes: a new caption costs the final
encode; a new speed or hold, replaying that clip; a new look (tapes/settings.tape), replaying all.
Needs vhs, ttyd, asciinema, ffmpeg and the fonts JetBrains Mono and Inter (README.md).
"""

import argparse
import glob
import hashlib
import json
import os
import subprocess
from pathlib import Path

import browser
from videos import VIDEOS

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
SIZE = (1920, 1080)
BAND = 110  # the captions' band, on top; the clips go below it
CLIP = (1920, 970)  # the terminal clips' size (tapes/settings.tape: Width, Height)
WINDOW = (20, BAND + 20, 1880, 930)  # x, y, width, height of the window in the frame (Margin 20)
BAR = 44  # the browser's address bar
RADIUS = 12
GREEN, INK = "5fd787", "0a0c0a"  # the accent (the terminal's green) and the windows' black
FPS = 30
FADE = 0.4
FONT = "Inter"
LOOK = 2  # bump when the segments' look changes, to redo them all


def run(*command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, timeout=kwargs.pop("timeout", 1800), **kwargs)


def duration(path: Path) -> float:
    probe = run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path),
                capture_output=True, text=True)
    return float(json.loads(probe.stdout)["format"]["duration"])


def ssh_agent() -> None:
    """The recordings use ssh keys from the agent; an IDE's socket goes stale when it reconnects,
    so take one that answers (the dev container also mounts the host's in /tmp/host-ssh-agent)."""
    for socket in [os.environ.get("SSH_AUTH_SOCK", ""), *sorted(glob.glob("/tmp/host-ssh-agent/*")),
                   *sorted(glob.glob("/tmp/vscode-ssh-auth-*.sock"), key=os.path.getmtime, reverse=True)]:
        if socket and subprocess.run(["ssh-add", "-l"], check=False, env={**os.environ, "SSH_AUTH_SOCK": socket},
                                     capture_output=True, timeout=10).returncode == 0:
            os.environ["SSH_AUTH_SOCK"] = socket
            return
    raise SystemExit("no ssh agent with a key: the VM is reached with one (ssh-add -l)")


def kind(clip: dict) -> str:
    return "card" if "card" in clip else "browser" if "browser" in clip else "tape"


# --- the pieces that do not change between takes ---

def backgrounds() -> None:
    w, h = SIZE
    if not (OUT / "bg.png").exists():
        run("ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            f"gradients=s={w}x{h}:c0=0x0b1a10:c1=0x020302:x0=0:y0=0:x1={w}:y1={h}:nb_colors=2,format=rgb24",
            "-frames:v", "1", str(OUT / "bg.png"))
    run("ffmpeg", "-loglevel", "error", "-y", "-i", str(OUT / "bg.png"), "-vf",
        f"crop={CLIP[0]}:{CLIP[1]}:0:{BAND}", str(OUT / "bg-clip.png"))
    # the browser's window, rounded like the terminal's (white = shown)
    _, _, ww, wh = WINDOW
    r = RADIUS
    inside = (f"if(lt(X,{r})*lt(Y,{r}), lte(hypot({r}-X,{r}-Y),{r}),"
              f" if(gt(X,{ww - r})*lt(Y,{r}), lte(hypot(X-{ww - r},{r}-Y),{r}),"
              f" if(lt(X,{r})*gt(Y,{wh - r}), lte(hypot({r}-X,Y-{wh - r}),{r}),"
              f" if(gt(X,{ww - r})*gt(Y,{wh - r}), lte(hypot(X-{ww - r},Y-{wh - r}),{r}), 1))))*255")
    run("ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={ww}x{wh}", "-vf",
        f"format=gray,geq=lum='{inside}'", "-frames:v", "1", str(OUT / "window-mask.png"))


# --- recording ---

EPILOGUE = """
# off camera: the recorded shell ends, and asciinema with it
Hide
Type "exit" Enter
Sleep 1s
"""


def record(video: str, clip: dict, folder: Path) -> None:
    """Runs the clip for real: a tape's session into casts/<name>.cast (and the take as VHS saw
    it, takes/<name>.mp4), a browser flow into clips/<name>.webm."""
    name = clip["name"]
    if clip.get("cast", name) != name:
        return  # it replays another clip's session
    for command in clip.get("setup", []):
        run("bash", "-c", command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if kind(clip) == "tape":
        print(f"recording {name} (vhs, asciinema)", flush=True)
        for sub in ("casts", "takes", "record"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        tape = folder / "record" / f"{name}.tape"
        tape.write_text((HERE / "tapes" / video / f"{name}.tape").read_text() + EPILOGUE)
        cast = folder / "casts" / f"{name}.cast"
        cast.unlink(missing_ok=True)
        run("vhs", str(tape), "-o", str(folder / "takes" / f"{name}.mp4"), cwd=HERE, stdout=subprocess.DEVNULL,
            env={**os.environ, "CAST": str(cast)})
    elif kind(clip) == "browser":
        print(f"recording {name} (browser)", flush=True)
        start = browser.record(clip["browser"], folder / "clips" / f"{name}.webm", **clip["args"])
        (folder / "clips" / f"{name}.json").write_text(json.dumps({"start": start}))


# --- replaying ---

PROMPT = "❯"
LEAD = 0.6  # replaying starts off camera: asciinema's own start, and the first prompt drawn
HOLD = 4  # seconds a clip's last screen stays, unless it says otherwise
IDLE = 1.2  # the longest pause kept in a clip (before typing, between answers)


def edit(cast: Path, clip: dict) -> tuple[list[str], float, tuple[float, float] | None]:
    """The session as the clip shows it: from the start, or from its `since` text (what came
    before already on screen); to its last prompt (the exit after it is not shown), or to its
    `until` text; its last command's output, or from its `fast_from` text, fast-forwarded `speed`
    times; no pause longer than `idle` seconds, and the last screen held `hold` seconds. Returns
    the cast's lines, its length and where the fast part is (both on the video's clock)."""
    speed, hold, idle = clip.get("speed", 1), clip.get("hold", HOLD), clip.get("idle", IDLE)
    lines = cast.read_text().splitlines()
    header, events = lines[0], [json.loads(line) for line in lines[1:]]
    events = [e for e in events if e[1] == "o"]

    def first(text: str, start: int = 0) -> int:
        found = next((i for i in range(start, len(events)) if text in events[i][2]), None)
        if found is None:
            raise SystemExit(f"{cast}: no {text!r} in it")
        return found

    if clip.get("since"):
        begin = first(clip["since"])
        at = events[begin][0]
        for i, e in enumerate(events):
            e[0] = 0.0 if i < begin else e[0] - at + LEAD + 1.2
    if clip.get("until"):
        end = first(clip["until"], first(clip["since"]) if clip.get("since") else 0)
    else:
        prompts = [i for i, e in enumerate(events) if PROMPT in e[2]]
        if not prompts:
            raise SystemExit(f"{cast}: no prompt in it")
        end = prompts[-1]
    events = events[:end + 1]
    fast = None
    if speed != 1:
        if clip.get("fast_from"):
            t0 = events[first(clip["fast_from"])][0]
        else:
            prompts = [i for i, e in enumerate(events) if PROMPT in e[2]]
            enter = next((i for i in range(prompts[-2] + 1, end) if "\n" in events[i][2]), None) \
                if len(prompts) >= 2 else None
            t0 = events[enter][0] + 1.0 if enter is not None else None
        t1 = events[end][0] - 0.3
        if t0 is not None and t1 > t0:
            for e in events:
                if e[0] > t1:
                    e[0] -= (t1 - t0) * (1 - 1 / speed)
                elif e[0] > t0:
                    e[0] = t0 + (e[0] - t0) / speed
            fast = [t0, t0 + (t1 - t0) / speed]
    # long pauses shortened: every later moment (and the fast part's ends) moves back as much
    shift, last = 0.0, events[0][0]
    moved = []
    for e in events:
        gap, last = e[0] - last, e[0]
        if gap > idle:
            shift += gap - idle
        moved.append((e[0], shift))
        e[0] -= shift
    if fast:
        fast = tuple(t - max((sh for at, sh in moved if at <= t), default=0.0) - LEAD for t in fast)
    length = events[-1][0] + hold
    events.append([length, "o", ""])  # asciinema waits until then: the hold
    return [header] + [json.dumps(e, ensure_ascii=False) for e in events], length - LEAD, fast


EDITS = ("speed", "hold", "idle", "since", "until", "fast_from", "cast")


def replay(video: str, clip: dict, folder: Path) -> None:
    """clips/<name>.mp4 from its cast; redone only when the cast, the look, or how the clip
    edits it changed."""
    name = clip["name"]
    source = clip.get("cast", name)
    cast = folder / "casts" / f"{source}.cast"
    out, meta = folder / "clips" / f"{name}.mp4", folder / "clips" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not cast.exists():
        raise SystemExit(f"{name} is not recorded yet: make.py {video} --record --only {source}")
    stat = cast.stat()
    key = hashlib.sha256(json.dumps([LOOK, (HERE / "tapes" / "settings.tape").read_text(), HOLD, IDLE,
                                     {k: clip.get(k) for k in EDITS}, stat.st_size, stat.st_mtime_ns]).encode()
                         ).hexdigest()
    if out.exists() and meta.exists() and json.loads(meta.read_text()).get("key") == key:
        return
    print(f"replaying {name}", flush=True)
    lines, length, fast = edit(cast, clip)
    edited = folder / "replay" / f"{name}.cast"
    edited.parent.mkdir(exist_ok=True)
    edited.write_text("\n".join(lines) + "\n")
    tape = folder / "replay" / f"{name}.tape"
    tape.write_text(f"""Source tapes/settings.tape
Hide
Type "clear; asciinema play {edited}" Enter
Sleep {LEAD}s
Show
Sleep {length:.2f}s
""")
    run("vhs", str(tape), "-o", str(out), cwd=HERE, stdout=subprocess.DEVNULL)
    meta.write_text(json.dumps({"key": key, "fast": fast, "speed": clip.get("speed", 1)}))


# --- joining ---

def source_of(clip: dict, clips: Path) -> Path | None:
    return {"tape": clips / f"{clip['name']}.mp4", "browser": clips / f"{clip['name']}.webm"}.get(kind(clip))


def segment(clip: dict, index: int, clips: Path, segments: Path) -> tuple[Path, float]:
    """The clip on the background, 1920x1080 at 30 fps; redone only when its recording or its
    look changed (captions are not in it)."""
    name = clip["name"]
    out = segments / f"{index:02d}-{name}.mp4"
    source = source_of(clip, clips)
    if source and not source.exists():
        raise SystemExit(f"{name} is not recorded yet: make.py ... --only {name}")
    stat = source.stat() if source else None
    shape = {k: v for k, v in clip.items() if k in ("card", "seconds", "url")}
    key = hashlib.sha256(json.dumps([LOOK, shape, stat and (stat.st_size, stat.st_mtime_ns)]).encode()).hexdigest()
    stamp = out.with_suffix(".key")
    if out.exists() and stamp.exists() and stamp.read_text() == key:
        return out, duration(out)
    for old in segments.glob(f"[0-9][0-9]-{name}.*"):
        old.unlink()
    encode = ["-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
              "-an", str(out)]
    bg = ["-loop", "1", "-framerate", str(FPS), "-i", str(OUT / "bg.png")]
    if kind(clip) == "card":
        run("ffmpeg", "-loglevel", "error", "-y", *bg, "-t", str(clip["seconds"]), *encode)
    elif kind(clip) == "tape":
        run("ffmpeg", "-loglevel", "error", "-y", *bg, "-i", str(source), "-filter_complex",
            f"[0][1]overlay=0:{BAND}:shortest=1", *encode)
    else:
        start = json.loads(source.with_suffix(".json").read_text())["start"]
        x, y, ww, wh = WINDOW
        url = clip["url"].removeprefix("https://").removeprefix("http://").replace(":", r"\:")
        warning = clip["url"].startswith("https://127.") or clip["url"].startswith("https://10.")
        graph = (f"[1]trim=start={start:.2f},setpts=PTS-STARTPTS,fps={FPS}[page];"
                 f"color=0x141814:s={ww}x{wh}:r={FPS}[frame];"
                 f"[frame][page]overlay=0:{BAR}:shortest=1,"
                 + (f"drawtext=font='{FONT}':text='⚠ Not secure':fontcolor=0xff6b6b:fontsize=17:x=22:"
                    f"y=({BAR}-th)/2," if warning else "")
                 + f"drawtext=font='{FONT}':text='{url}':fontcolor=0xb8beb8:fontsize=18:x=(w-tw)/2:y=({BAR}-th)/2,"
                 f"format=rgba[win];[2]format=gray[mask];[win][mask]alphamerge[rounded];"
                 f"[0][rounded]overlay={x}:{y}:shortest=1")
        run("ffmpeg", "-loglevel", "error", "-y", *bg, "-i", str(source), "-loop", "1", "-i",
            str(OUT / "window-mask.png"), "-filter_complex", graph,
            "-t", f"{duration(source) - start:.2f}", *encode)
    stamp.write_text(key)
    return out, duration(out)


def ass_time(seconds: float) -> str:
    seconds = max(seconds, 0)
    return f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}:{seconds % 60:05.2f}"


def bgr(rgb: str) -> str:
    """ASS colors are BGR."""
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def ass_text(text: str) -> str:
    """`code` in the mono font and the accent color."""
    parts = text.split("`")
    return "".join(f"{{\\fnJetBrains Mono\\c&H{bgr(GREEN)}&}}{part}{{\\r}}" if i % 2 else part
                   for i, part in enumerate(parts))


def dialogue(style: str, start: float, end: float, text: str, layer: int = 0) -> str:
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def captions(clips: list[dict], starts: list[float], lengths: list[float], path: Path) -> Path:
    green, ink = bgr(GREEN), bgr(INK)
    middle = BAND // 2 + 4
    lines = []
    for index, (clip, start, length) in enumerate(zip(clips, starts, lengths)):
        end = start + length
        if "card" in clip:
            kicker, title, sub, command = clip["card"]
            fade = r"{\fad(500,450)}"
            title = " ".join(rf"{{\c&H{green}&}}{word}{{\r}}" if word == "INATrace" else word
                             for word in title.split(" "))
            lines += [
                dialogue("Kicker", start, end, rf"{fade}{{\pos(960,380)}}{kicker.upper()}", 1),
                dialogue("Title", start, end, rf"{fade}{{\pos(960,480)}}{title}", 1),
                dialogue("Sub", start, end, rf"{fade}{{\pos(960,580)}}{sub}", 1),
                dialogue("Command", start, end, rf"{fade}{{\pos(960,700)}}{{\c&H{green}&}}❯{{\r}}  {command}", 1),
            ]
        if "caption" in clip and (index == 0 or clips[index - 1].get("caption") != clip["caption"]):
            # one caption for the clips in a row that share it (a command's help, then the command)
            last = index
            while last + 1 < len(clips) and clips[last + 1].get("caption") == clip["caption"]:
                last += 1
            tag, text = clip["caption"]
            lines.append(dialogue("Caption", start + FADE, starts[last] + lengths[last] - FADE / 2,
                                  rf"{{\fad(350,250)}}{{\pos(44,{middle})}}{{\c&H{green}&\b1}}{tag.upper()}{{\r}}"
                                  rf"     {ass_text(text)}"))
        fast = clip.get("_fast")
        if fast:
            lines.append(dialogue("Badge", start + fast[0], start + fast[1],
                                  rf"{{\fad(250,250)}}{{\pos({SIZE[0] - 50},{middle})}}{clip['speed']}× faster"))
    path.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {SIZE[0]}
PlayResY: {SIZE[1]}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{FONT} Medium,36,&H00F2F4F2,&H00F2F4F2,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,40,40,0,1
Style: Badge,{FONT},26,&H00{ink},&H00{ink},&H00{green},&H00000000,1,0,0,0,100,100,0,0,3,10,0,6,40,50,0,1
Style: Kicker,{FONT} SemiBold,28,&H00{green},&H00{green},&H00000000,&H00000000,0,0,0,0,100,100,6,0,1,0,0,5,0,0,0,1
Style: Title,{FONT},112,&H00F2F4F2,&H00F2F4F2,&H00000000,&H00000000,1,0,0,0,100,100,-2,0,1,0,0,5,0,0,0,1
Style: Sub,{FONT},38,&H00B8BEB8,&H00B8BEB8,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Command,JetBrains Mono,32,&H00D7D4D4,&H00D7D4D4,&H00{ink},&H00000000,0,0,0,0,100,100,0,0,3,16,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(lines) + "\n")
    return path


def join(video: str, clips: list[dict]) -> Path:
    folder = OUT / video
    for clip in clips:
        if kind(clip) == "tape":
            replay(video, clip, folder)
            meta = folder / "clips" / f"{clip['name']}.json"
            if meta.exists() and json.loads(meta.read_text()).get("fast"):
                clip["_fast"] = json.loads(meta.read_text())["fast"]
    print("joining", flush=True)
    (folder / "segments").mkdir(parents=True, exist_ok=True)
    segments = [segment(clip, index, folder / "clips", folder / "segments") for index, clip in enumerate(clips)]
    starts, at = [], 0.0
    for _, length in segments:
        starts.append(at)
        at += length - FADE
    total = at + FADE
    inputs = [arg for path, _ in segments for arg in ("-i", str(path))]
    graph, previous = [], "[0]"
    for index in range(1, len(segments)):
        label = f"[x{index}]"
        graph.append(f"{previous}[{index}]xfade=transition=fade:duration={FADE}:offset={starts[index]:.3f}{label}")
        previous = label
    subtitles = captions(clips, starts, [length for _, length in segments], folder / "captions.ass")
    graph.append(f"{previous}subtitles={subtitles.name},fade=t=out:st={total - 0.8:.3f}:d=0.8[v]")
    out = OUT / f"{video}.mp4"
    run("ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out), cwd=folder)
    print(f"{out}  ({duration(out):.1f}s)", flush=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", choices=VIDEOS)
    parser.add_argument("--record", action="store_true",
                        help="Record for real first (the server must be there): every clip, from nothing.")
    parser.add_argument("--only", nargs="+", default=[], metavar="CLIP", help="With --record: only these clips.")
    parser.add_argument("--from", dest="start", metavar="CLIP", help="With --record: from this clip on.")
    options = parser.parse_args()
    spec = VIDEOS[options.video]
    names = [clip["name"] for clip in spec["clips"]]
    for name in [*options.only, *([options.start] if options.start else [])]:
        if name not in names:
            raise SystemExit(f"no clip {name} in {options.video}: {', '.join(names)}")
    if (options.only or options.start) and not options.record:
        raise SystemExit("--only and --from choose what to record: add --record")
    folder = OUT / options.video
    (folder / "clips").mkdir(parents=True, exist_ok=True)
    backgrounds()
    if options.record:
        ssh_agent()
        if not options.only and not options.start:
            for command in spec.get("setup", []):
                print(f"setup: {command}", flush=True)
                run("bash", "-c", command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        first = names.index(options.start) if options.start else 0
        for index, clip in enumerate(spec["clips"]):
            if (options.only and clip["name"] in options.only) or (not options.only and index >= first):
                record(options.video, clip, folder)
    join(options.video, spec["clips"])


if __name__ == "__main__":
    main()
