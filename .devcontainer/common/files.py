"""Idempotent file writes: a second run with the same content changes nothing."""

import json
import os
from pathlib import Path

BEGIN = "# >>> inatrace-platform >>>"
END = "# <<< inatrace-platform <<<"
MANAGED = "(managed by .devcontainer/provision.py; edits inside are overwritten)"


def write_if_changed(path: Path, content: str, mode: int | None = None) -> bool:
    """Writes `content` atomically unless the file already holds it. True if written."""
    try:
        if path.read_text() == content:
            if mode is not None:
                path.chmod(mode)
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content)
    if mode is not None:
        tmp.chmod(mode)
    os.replace(tmp, path)
    return True


def without_block(text: str) -> str:
    """`text` minus the managed block, if any."""
    lines, skipping = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith(BEGIN):
            skipping = True
        if not skipping:
            lines.append(line)
        if line.startswith(END):
            skipping = False
    return "".join(lines)


def with_block(text: str, body: str, first: bool = False) -> str:
    """`text` with the managed block holding `body`, at the end (or first)."""
    block = f"{BEGIN} {MANAGED}\n{body.rstrip()}\n{END}\n"
    rest = without_block(text)
    if first:
        return block + rest
    if rest and not rest.endswith("\n"):
        rest += "\n"
    return rest + block


def json_defaults(path: Path, defaults: dict, mode: int | None = None) -> bool:
    """Adds the keys in `defaults` that `path` lacks (one level deep for dicts),
    keeping every value already there. True if the file changed."""
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    before = json.dumps(data, sort_keys=True)
    for key, value in defaults.items():
        if isinstance(value, dict):
            current = data.setdefault(key, {})
            for k, v in value.items():
                current.setdefault(k, v)
        else:
            data.setdefault(key, value)
    if json.dumps(data, sort_keys=True) == before:
        return False
    return write_if_changed(path, json.dumps(data, indent=2) + "\n", mode)
