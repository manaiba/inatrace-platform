"""Console output, questions and the step runner, with rich: sections, ✓/!/✗ lines,
spinners for waits. Without a terminal (piped, CI) rich drops colors and spinners.

With `use_json()`, the same calls write JSON Lines instead, one event a line on stdout
(docs/deploy.md → For scripts and agents), and nothing is asked."""

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.spinner import Spinner
from rich.text import Text

out = Console(highlight=False)
err = Console(stderr=True, highlight=False)
_events = None  # where JSON events go, once use_json() is on


def use_json(on: bool = True) -> None:
    """From now on: JSON events on stdout, anything else to stderr, no questions (or,
    `on` false, back to people)."""
    global _events, out
    _events = sys.stdout if on else None
    out = Console(stderr=on, highlight=False)


def json_mode() -> bool:
    return _events is not None


def emit(event: str, **fields) -> None:
    _events.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
    _events.flush()


def plain(markup: str) -> str:
    """The text of a line with rich markup."""
    return Text.from_markup(markup).plain


def show(event: str, data: dict, render: Callable[[], None]) -> None:
    """Something with a shape (a plan, a status, a list): drawn by `render` for people,
    or a JSON event with `data` as its fields."""
    if json_mode():
        emit(event, **data)
    else:
        render()


class StepError(Exception):
    """A step failed; the message says why."""


class NoAnswer(Exception):
    """A question met the end of the input (no terminal, nothing piped)."""


def error(message: str, flag: str | None = None) -> None:
    """A command that failed: ✗ and the message for people, an `error` event (with the flag
    that gives a missing answer) for programs."""
    if json_mode():
        emit("error", message=message, **({"flag": flag} if flag else {}))
    else:
        fail(message)


def blank() -> None:
    """An empty line between parts, for people."""
    if not json_mode():
        out.print()


def section(title: str) -> None:
    if json_mode():
        return emit("section", title=title)
    out.print()
    out.rule(f"[bold cyan]{escape(title)}", align="left", style="cyan")


def info(message: str) -> None:
    if json_mode():
        return emit("info", text=message)
    out.print(f"  [dim]{escape(message)}[/]")


def ok(message: str) -> None:
    if json_mode():
        return emit("ok", text=message)
    out.print(f"  [green]✓[/] {escape(message)}")


def step(message: str) -> None:
    if json_mode():
        return emit("step", text=message)
    out.print(f"  [cyan]→[/] {escape(message)}")


def warn(message: str) -> None:
    if json_mode():
        return emit("warn", text=message)
    err.print(f"  [yellow]![/] {escape(message)}")


def fail(message: str) -> None:
    if json_mode():
        return emit("fail", text=message)
    err.print(f"  [bold red]✗[/] {escape(message)}")


def _elapsed(start: float) -> str:
    seconds = time.monotonic() - start
    return f"{seconds:.1f}s" if seconds < 10 else f"{seconds:.0f}s"


class _Ticking:
    """A spinner, the step's message, and the time since it began, counted live."""

    def __init__(self, message: str = "") -> None:
        self.spinner = Spinner("dots", style="green")
        self.restart(message)

    def restart(self, message: str) -> None:
        self.message, self.started = message, time.monotonic()

    def __rich_console__(self, console, options):
        self.spinner.text = Text.from_markup(f"[cyan]{self.message}[/]  [dim]{_elapsed(self.started)}[/]")
        yield self.spinner


def _live(ticking: _Ticking) -> Live:
    return Live(ticking, console=out, refresh_per_second=10, transient=True)


@contextmanager
def doing(message: str) -> Iterator[None]:
    """A step that takes a while: a spinner meanwhile, then ✓ and how long it took;
    ✗ when it raises. `message` may hold rich markup."""
    start = time.monotonic()
    if json_mode():
        task = plain(message)
        emit("start", task=task)
        try:
            yield
        except BaseException:
            emit("end", task=task, ok=False, seconds=round(time.monotonic() - start, 1))
            raise
        emit("end", task=task, ok=True, seconds=round(time.monotonic() - start, 1))
        return
    try:
        with _live(_Ticking(message)):
            yield
    except BaseException:
        err.print(f"  [bold red]✗[/] {message}  [dim]{_elapsed(start)}[/]")
        raise
    out.print(f"  [green]✓[/] {message}  [dim]{_elapsed(start)}[/]")


class Stages:
    """The stages a running command reports, one per line: a spinner on the current one,
    then ✓ and its time when the next begins (✗ when the command fails). Anything else it
    prints (a sudo prompt, an error) shows as it comes, the spinner paused meanwhile."""

    def __init__(self, indent: str = "    ") -> None:
        self.indent = indent
        self.current: str | None = None
        self.started = 0.0
        self.ticking = _Ticking()
        self.status = _live(self.ticking)
        self.spinning = False

    def __enter__(self) -> "Stages":
        return self

    def __exit__(self, kind, error, trace) -> None:
        self.end(error is None)

    def _spin(self, on: bool) -> None:
        if on and not self.spinning:
            self.status.start()
        elif not on and self.spinning:
            self.status.stop()
        self.spinning = on

    def _finish(self, ok: bool) -> None:
        if self.current is None:
            return
        if json_mode():
            emit("end", task=self.current, ok=ok, seconds=round(time.monotonic() - self.started, 1))
            self.current = None
            return
        mark = "[green]✓[/]" if ok else "[bold red]✗[/]"
        self._spin(False)
        out.print(f"{self.indent}{mark} {escape(self.current)}  [dim]{_elapsed(self.started)}[/]")
        self.current = None

    def begin(self, title: str) -> None:
        self._finish(True)
        self.current, self.started = title, time.monotonic()
        if json_mode():
            return emit("start", task=title)
        self.ticking.restart(escape(title))
        self._spin(True)

    def text(self, chunk: str) -> None:
        if json_mode():
            if chunk.strip():
                emit("output", text=chunk.rstrip("\n"))
            return
        self._spin(False)
        out.file.write(chunk)
        out.file.flush()
        if self.current is not None and chunk.endswith("\n"):
            self._spin(True)

    def end(self, ok: bool) -> None:
        self._finish(ok)
        self._spin(False)


def ask_text(question: str, default: str = "", *, hide: bool = False) -> str:
    """One line of input; Enter keeps the default."""
    if json_mode():
        raise NoAnswer(question)
    shown = f" [dim]({escape(default)})[/]" if default and not hide else ""
    while True:
        try:
            answer = out.input(f"  [bold]{escape(question)}[/]{shown}: ", password=hide).strip()
        except EOFError:
            raise NoAnswer(question) from None
        return answer or default


def confirm(question: str, default: bool) -> bool:
    if json_mode():
        raise NoAnswer(question)
    shown = "[bold]Y[/]/n" if default else "y/[bold]N[/]"
    while True:
        try:
            answer = out.input(f"  [bold]{escape(question)}[/] [dim]\\[[/]{shown}[dim]][/]: ").strip().lower()
        except EOFError:
            raise NoAnswer(question) from None
        if not answer:
            return default
        if answer[0] in "yn":
            return answer[0] == "y"
        warn("y or n")


def ask(question: str) -> bool:
    """A yes/no question, "no" unless answered otherwise; "no" without a terminal."""
    if json_mode() or not sys.stdin.isatty():
        return False
    return confirm(question, False)


class Steps:
    """Runs steps in order. A failing step never stops the next ones: it is
    reported, and listed by `summary()`."""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def run(self, title: str, step_: Callable[[], None]) -> None:
        step(title)
        try:
            step_()
        except (StepError, OSError, subprocess.CalledProcessError) as error:
            fail(f"{title}: {error}")
            self.failed.append(title)

    def summary(self) -> None:
        if self.failed:
            err.print("\n[bold red]Steps that failed[/] (fix the cause, then re-run):")
            for title in self.failed:
                err.print(f"  - {escape(title)}")
