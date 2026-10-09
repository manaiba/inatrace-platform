"""Files and directories others may write to, under the platform, and removing that.

On some Docker setups (docs/dev-container.md → Troubleshooting) everything `docker
exec` starts, VS Code's server and its terminals among them, runs with umask 0000:
new files come out 666 and directories 777. Git ignores these bits, so nothing
else shows it.
"""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

GROUP_OTHER_WRITE = stat.S_IWGRP | stat.S_IWOTH


def umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def umask_lets_others_write() -> bool:
    return umask() & GROUP_OTHER_WRITE != GROUP_OTHER_WRITE


def _entries(root: Path) -> Iterator[tuple[str, int]]:
    """(path, mode) of root and everything under it; symbolic links are skipped,
    as chmod would follow them."""
    try:
        yield str(root), root.lstat().st_mode
    except OSError:
        return
    pending = [str(root)]
    while pending:
        try:
            listing = os.scandir(pending.pop())
        except OSError:
            continue
        with listing:
            for entry in listing:
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except OSError:
                    continue
                if stat.S_ISLNK(mode):
                    continue
                yield entry.path, mode
                if stat.S_ISDIR(mode):
                    pending.append(entry.path)


def writable(root: Path) -> list[str]:
    """What under `root` others may write to."""
    return [path for path, mode in _entries(root) if mode & GROUP_OTHER_WRITE]


def fix(root: Path) -> tuple[int, list[str]]:
    """Removes group/other write under `root`: how many it fixed, and those it
    could not (not ours)."""
    fixed, failed = 0, []
    for path, mode in _entries(root):
        if not mode & GROUP_OTHER_WRITE:
            continue
        try:
            os.chmod(path, stat.S_IMODE(mode) & ~GROUP_OTHER_WRITE)
            fixed += 1
        except OSError:
            failed.append(path)
    return fixed, failed
