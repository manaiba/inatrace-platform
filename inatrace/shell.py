"""Running external commands."""

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import ui


def run(cmd: Sequence[str | Path], *, quiet: bool = False, check: bool = True,
        input: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Runs a command. `quiet` hides its output; `check` raises when it fails. With
    --json what it prints goes to stderr, stdout being the events'."""
    out = subprocess.DEVNULL if quiet else sys.stderr if ui.json_mode() else None
    return subprocess.run([str(c) for c in cmd], check=check, text=True, input=input,
                          stdout=out, stderr=out, cwd=cwd)


def ok(cmd: Sequence[str | Path], **kwargs) -> bool:
    """True when the command exists and exits with 0. Output is hidden."""
    try:
        return run(cmd, quiet=True, check=False, **kwargs).returncode == 0
    except OSError:
        return False


def output(cmd: Sequence[str | Path], *, check: bool = True, cwd: Path | None = None) -> str:
    """The command's stdout, stripped; stderr is hidden."""
    result = subprocess.run([str(c) for c in cmd], check=check, text=True, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.stdout.strip()


def have(program: str) -> bool:
    return shutil.which(program) is not None


def lines(cmd: Sequence[str | Path], on_line: Callable[[str], None], *,
          env: dict[str, str] | None = None) -> int:
    """Runs a command, handing each line it prints (stdout and stderr) to `on_line`."""
    process = subprocess.Popen([str(c) for c in cmd], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               errors="replace")
    try:
        for line in process.stdout:
            on_line(line.rstrip("\n"))
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def compose_log(line: str) -> tuple[str | None, str]:
    """(service, text) of a `docker compose logs` line: "backend-1  | text"."""
    container, bar, said = line.partition(" | ")
    if not bar:
        return None, line
    return container.strip().rsplit("-", 1)[0] if container.strip()[-1:].isdigit() else container.strip(), said
