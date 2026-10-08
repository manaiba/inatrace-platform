"""Running external commands."""

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path


def run(cmd: Sequence[str | Path], *, quiet: bool = False, check: bool = True,
        input: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Runs a command. `quiet` hides its output; `check` raises when it fails."""
    out = subprocess.DEVNULL if quiet else None
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
