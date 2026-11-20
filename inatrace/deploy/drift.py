"""Who deployed last, and whether the server's settings moved since this machine saw them.

`up` leaves a record on the server (~/inatrace/.deployed: who, when, from which platform commit,
and a fingerprint of the instance's files there), and the same record here
(deploy/instances/<name>/.deployed), the server's settings as this machine last saw them. Before
`up` sends anything, the server's settings are compared with both: when they differ from what is
here and also from what this machine last saw, someone else changed them (another `up`, or by
hand), and `up` stops, as it does when the server was deployed from a platform commit this
checkout does not have. `pull` brings the server's settings here."""

import datetime
import getpass
import hashlib
import json
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from .. import paths, ui
from .common import DeployError, age
from .instance import LOCAL_PROPERTIES, LOCAL_RECORD, Instance, instance_files, load, server_files, shown
from .remote import REMOTE_DIR, Remote, deployed

RECORD = LOCAL_RECORD  # the same name there and here

MOVED = "the settings on the server differ from these"

# A key in .env or a .properties file: `KEY=…`, `key.name = …`; not comments.
_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*[=:]")


def settings_names(instance: Instance, record: dict | None = None) -> list[str]:
    """The instance's files `up` puts in ~/inatrace (.env, backend.local.properties, any other of
    its own), and those the server's record says came from the instance."""
    names = {".env", LOCAL_PROPERTIES, *instance_files(instance)}
    names.update((record or {}).get("files", []))
    return sorted(names)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_hashes(instance: Instance, names: list[str]) -> dict[str, str]:
    """sha256 of what `up` would send under these names (the default backend.local.properties
    when the instance has none)."""
    files = server_files(instance)
    return {name: _sha(files[name]) for name in names if name in files}


def server_hashes(remote: Remote, names: list[str]) -> dict[str, str]:
    """sha256 of these files in ~/inatrace, those that are there."""
    quoted = " ".join(shlex.quote(name) for name in names)
    found = remote.output(f"cd ~/{REMOTE_DIR} 2>/dev/null && sha256sum -- {quoted} 2>/dev/null; true") or ""
    hashes = {}
    for line in found.splitlines():
        digest, _, name = line.partition("  ")
        if name:
            hashes[name] = digest
    return hashes


def fingerprint(hashes: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{n}:{h}\n" for n, h in sorted(hashes.items())).encode()).hexdigest()


def _keys(text: str) -> dict[str, str]:
    keys = {}
    for line in text.splitlines():
        match = _KEY.match(line)
        if match:
            keys[match.group(1)] = line.split("=", 1)[-1].strip() if "=" in line else line
    return keys


def differences(here: str | None, there: str | None) -> list[str]:
    """The keys that differ between two versions of a settings file, by name only: the values
    are secrets."""
    mine, theirs = _keys(here or ""), _keys(there or "")
    words = []
    for key in sorted(set(mine) | set(theirs)):
        if key not in theirs:
            words.append(f"{key} (only here)")
        elif key not in mine:
            words.append(f"{key} (only there)")
        elif mine[key] != theirs[key]:
            words.append(key)
    return words or ["comments or spacing only"]


def _server_text(remote: Remote, name: str) -> str | None:
    return remote.output(f"cat ~/{REMOTE_DIR}/{shlex.quote(name)}")


def changed_files(remote: Remote, instance: Instance, names: list[str], here: dict[str, str],
                  there: dict[str, str]) -> list[tuple[str, str, list[str]]]:
    """Each file that differs: (name, where it is: "here", "there" or "both", the keys that
    differ when both)."""
    files = server_files(instance)
    found = []
    for name in names:
        if here.get(name) == there.get(name):
            continue
        if name not in there:
            found.append((name, "here", []))
        elif name not in here:
            found.append((name, "there", []))
        else:
            found.append((name, "both", differences(files[name].read_text(), _server_text(remote, name))))
    return found


def in_words(changes: list[tuple[str, str, list[str]]]) -> str:
    """`.env: KEY_A, KEY_B (only there); compose.override.yaml (only here)`."""
    return "; ".join(f"{name}: {', '.join(keys)}" if where == "both" else f"{name} (only {where})"
                     for name, where, keys in changes)


def read_server(remote: Remote) -> dict | None:
    return _parse(remote.output(f"cat ~/{REMOTE_DIR}/{RECORD} 2>/dev/null"))


def read_local(instance: Instance) -> dict | None:
    path = instance.dir / RECORD
    return _parse(path.read_text()) if path.is_file() else None


def _parse(text: str | None) -> dict | None:
    try:
        found = json.loads(text) if text else None
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(paths.ROOT), *args], capture_output=True, text=True)


def platform() -> tuple[str | None, bool]:
    """The platform checkout's commit, and whether it has uncommitted changes (git-ignored files,
    such as the instances, aside)."""
    try:
        head = _git("rev-parse", "HEAD")
    except FileNotFoundError:
        return None, False
    if head.returncode:
        return None, False
    return head.stdout.strip(), bool(_git("status", "--porcelain", "--untracked-files=no").stdout.strip())


def _who() -> str:
    """The person, as git knows them here: `Ana <ana@example.org>`; else user@host."""
    try:
        name, email = (_git("config", key).stdout.strip() for key in ("user.name", "user.email"))
    except FileNotFoundError:
        name = email = ""
    if name or email:
        return f"{name} <{email}>".strip() if email else name
    return f"{getpass.getuser()}@{socket.gethostname()}"


def described(record: dict, now: datetime.datetime | None = None) -> str:
    """`Ana on laptop, 2 h ago, from platform 1a2b3c4d`."""
    words = [record.get("by") or "someone"]
    try:
        when = datetime.datetime.fromisoformat(record["at"])
        words.append(age(when, now or datetime.datetime.now(datetime.timezone.utc)))
    except (KeyError, TypeError, ValueError):
        pass
    if record.get("platform"):
        words.append(f"from platform {record['platform'][:8]}" + (" with local changes" if record.get("dirty") else ""))
    return ", ".join(words)


def platform_problem(record: dict | None) -> str | None:
    """Why this checkout should not deploy over that record's: it came from a platform commit
    this checkout does not have (newer, or another branch), whose server files `up` would undo."""
    commit = (record or {}).get("platform")
    here, _ = platform()
    if not commit or not here or commit == here:
        return None
    if _git("cat-file", "-e", f"{commit}^{{commit}}").returncode:
        return (f"the server was deployed from platform commit {commit[:8]}, which this checkout does not have: "
                "update it first (git pull)")
    if _git("merge-base", "--is-ancestor", commit, "HEAD").returncode:
        return (f"the server was deployed from platform commit {commit[:8]}, which is not in this checkout's "
                "history (newer, or another branch): `up` would put older or other files there; update "
                "this checkout first")
    return None


def check(remote: Remote, instance: Instance) -> list[str]:
    """What stops `up` here: the server's settings changed since this machine last saw them, or
    it came from a platform this checkout lacks. Empty on a server `up` has not reached yet."""
    record = read_server(remote)
    if record:
        ui.info(f"last deployed by {described(record)}")
    names = settings_names(instance, record)
    there = server_hashes(remote, names)
    if ".env" not in there:
        return []
    problems = []
    here = local_hashes(instance, names)
    seen = (read_local(instance) or {}).get("settings")
    if here != there and fingerprint(there) != seen:
        since = "since this machine last deployed or pulled them" if seen else \
            "and this machine has no record of them (it never deployed or pulled them)"
        problems.append(f"{MOVED} {since}: "
                        + in_words(changed_files(remote, instance, names, here, there)))
    problem = platform_problem(record)
    if problem:
        problems.append(problem)
    return problems


def stop(problems: list[str], name: str, dry_run: bool, overwrite: bool) -> None:
    """Stops `up` on these problems, unless `overwrite`; a dry run says it would."""
    if not problems:
        return
    advice = "--overwrite deploys these anyway"
    if any(problem.startswith(MOVED) for problem in problems):
        advice = f"inatrace deploy pull {name} brings the server's settings here; {advice}"
    if overwrite:
        for problem in problems:
            ui.warn(f"{problem} (--overwrite: going on)")
        return
    if dry_run:
        for problem in problems:
            ui.warn(problem)
        ui.warn(f"up would stop here: {advice}")
        return
    raise DeployError("; ".join(problems) + f": {advice}", flag="--overwrite")


def record(remote: Remote, instance: Instance, names: list[str]) -> None:
    """Notes, there and here, who put these settings on the server, and what they are."""
    commit, dirty = platform()
    settings = fingerprint(server_hashes(remote, names))
    data = {"by": _who(), "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "platform": commit, "dirty": dirty, "files": names, "settings": settings}
    result = remote.quiet(f"cat > {RECORD}", input=json.dumps(data, indent=2) + "\n")
    if result.returncode:
        raise DeployError(f"noting the deploy on {remote.destination} failed: {result.stdout.strip()}")
    _remember(instance, data)


def _remember(instance: Instance, data: dict) -> None:
    path = instance.dir / RECORD
    path.touch(mode=0o600)
    path.write_text(json.dumps(data, indent=2) + "\n")


def pull(name: str, dry_run: bool = False, auto_approve: bool = False) -> int:
    """Copies the server's settings here (the instance's files in ~/inatrace), once confirmed,
    and notes them as seen: `up` from here then sends them back unchanged."""
    instance = load(name)
    remote = deployed(instance)
    ui.section(f"{'Planning to pull' if dry_run else 'Pulling'} the settings of {name} from {remote.destination}")
    server_record = read_server(remote)
    if server_record:
        ui.info(f"last deployed by {described(server_record)}")
    names = settings_names(instance, server_record)
    there = server_hashes(remote, names)
    here = local_hashes(instance, names)
    changes = changed_files(remote, instance, names, here, there)
    incoming = [name_ for name_, where, _ in changes if where != "here"]
    kept = [name_ for name_, where, _ in changes if where == "here" and name_ in instance_files(instance)]
    actions = [f"replace {name_}: {', '.join(keys)}" if where == "both" else f"add {name_}"
               for name_, where, keys in changes if where != "here"]
    note = f"kept here, not on the server: {', '.join(kept)}" if kept else ""
    problem = platform_problem(server_record)

    def render() -> None:
        if actions:
            ui.out.print(f"  [bold]{'Would' if dry_run else 'Will'} do in {shown(instance.dir)}:[/]")
            for action in actions:
                ui.out.print(f"   {action}")
        else:
            ui.out.print("  [dim]the settings here already match the server's[/]")
        if note:
            ui.info(note)

    ui.show("plan", {"actions": actions, "note": note, "files": incoming}, render)
    if problem:
        ui.warn(problem)
    if dry_run:
        ui.info("dry run: nothing was changed")
        return 0
    if actions and not auto_approve:
        try:
            go = ui.confirm("Replace the settings here with the server's?", False)
        except ui.NoAnswer:
            raise DeployError("no answer for “Replace the settings here?”: give --auto-approve",
                              flag="--auto-approve") from None
        if not go:
            ui.info("nothing was changed")
            return 0
    if incoming:
        with ui.doing(f"copying {', '.join(incoming)} from {remote.destination}"), \
                tempfile.TemporaryDirectory(prefix="inatrace-pull-") as staging:
            remote.fetch(incoming, Path(staging))
            for name_ in incoming:
                target = instance.dir / name_
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(Path(staging, Path(name_).name), target)
                target.chmod(0o600)
    data = dict(server_record or {})
    data["settings"] = fingerprint(server_hashes(remote, names))
    _remember(instance, data)
    ui.ok(f"the settings here are the server's: {', '.join(incoming)}" if incoming
          else "the settings here already were the server's (noted as seen)")
    ui.show("pulled", {"files": incoming, "into": shown(instance.dir)}, lambda: None)
    return 0
