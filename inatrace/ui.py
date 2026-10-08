"""Console output and the step runner."""

import subprocess
import sys
from collections.abc import Callable


class StepError(Exception):
    """A step failed; the message says why."""


def info(message: str) -> None:
    print(f"    {message}", flush=True)


def warn(message: str) -> None:
    print(f"    {message}", file=sys.stderr, flush=True)


def ask(question: str) -> bool:
    """A yes/no question, "no" unless answered otherwise; "no" without a terminal."""
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


class Steps:
    """Runs steps in order. A failing step never stops the next ones: it is
    reported, and listed by `summary()`."""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def run(self, title: str, step: Callable[[], None]) -> None:
        print(f"==> {title}", flush=True)
        try:
            step()
        except (StepError, OSError, subprocess.CalledProcessError) as error:
            warn(f"!! {title}: {error}")
            self.failed.append(title)

    def summary(self) -> None:
        if self.failed:
            print("\nSteps that failed (fix the cause, then re-run):", file=sys.stderr)
            for title in self.failed:
                print(f"  - {title}", file=sys.stderr)
