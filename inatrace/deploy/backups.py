"""`deploy backup list|create|restore|delete`, and the scheduled backup (the server's
crontab, which `up` keeps)."""

import datetime
import re
from pathlib import Path

from .. import ui
from .common import DeployError, age, size, would
from .instance import LOCAL_BACKUPS, Instance, load, shown
from .remote import REMOTE_DIR, Remote, deployed, wait_healthy


# The crontab line `up` keeps for the scheduled backup, found again by this mark; the
# user's other lines stay as they are.
CRON_MARK = "# inatrace: scheduled backup (inatrace deploy up)"


def schedule_of(instance: Instance) -> str:
    return instance.get("INATRACE_BACKUP_SCHEDULE", "@daily")


def in_words(schedule: str) -> str:
    """A schedule as people say it, when it is a common one; the expression otherwise."""
    named = {"@daily": "daily at midnight", "@midnight": "daily at midnight", "@hourly": "hourly",
             "@weekly": "weekly (Sunday midnight)", "@monthly": "monthly (the 1st, midnight)"}
    if schedule in named:
        return named[schedule]
    found = re.match(r"^(\d{1,2}) (\d{1,2}) \* \* \*$", schedule)
    return f"daily at {int(found.group(2)):02}:{int(found.group(1)):02}" if found else schedule


def cron_plan(remote: Remote, instance: Instance) -> tuple[str | None, list[str]]:
    """What the server's crontab should hold (the user's lines, and ours unless off),
    and what changes, in words; None when nothing does."""
    current = (remote.output("crontab -l 2>/dev/null; true") or "").splitlines()
    schedule = schedule_of(instance)
    wanted = [line for line in current if CRON_MARK not in line]
    if schedule != "off":
        wanted.append(f"{schedule} cd ~/{REMOTE_DIR} && ./backup.sh --scheduled >> backups/scheduled.log 2>&1 "
                      f"{CRON_MARK}")
    if wanted == current:
        return None, wanted
    if schedule == "off":
        return "stop the scheduled backup (INATRACE_BACKUP_SCHEDULE is off)", wanted
    zone = remote.output("date +%Z") or "?"
    return f"back up {in_words(schedule)}, {zone} [dim](the server's time; INATRACE_BACKUP_SCHEDULE)[/]", wanted


def install_cron(remote: Remote, lines: list[str]) -> None:
    result = remote.quiet("crontab -", "\n".join(lines) + "\n" if lines else "")
    if result.returncode:
        raise DeployError(f"the server's crontab refused the schedule: {result.stdout.strip()}")


STAMP = re.compile(r"^\d{8}T\d{6}Z$")


def create(name: str, dry_run: bool = False) -> int:
    instance = load(name)
    remote = deployed(instance)
    if dry_run:
        would(f"dump the database and archive the uploads into ~/{REMOTE_DIR}/backups on "
              f"{remote.destination}, removing those older than {instance.get('INATRACE_BACKUP_DAYS', '7')} days")
        return 0
    with ui.doing(f"backing up {name} on {remote.destination}"):
        done = remote.must("./backup.sh", "the backup")
    for line in done.splitlines():
        if line.startswith("backup: ") and "done" not in line:
            ui.info(line.removeprefix("backup: "))
    _made(name, _stamp(done))
    return 0


def _stamp(done: str) -> str:
    """The time backup.sh named its backup, from what it printed."""
    return next((line.split(", ")[-1] for line in done.splitlines() if "backup: done" in line), "")


def _made(name: str, stamp: str) -> None:
    ui.show("backup", {"time": stamp},
            lambda: ui.info(f"backup {stamp}: inatrace deploy backup restore {name} {stamp} puts it back"))


def backup_first(remote: Remote, name: str, why: str) -> str:
    """Backs up before a change that replaces data, and says how to go back; its time."""
    with ui.doing(f"backing up first  [dim]({why})[/]"):
        done = remote.must("./backup.sh", "the backup")
    _made(name, _stamp(done))
    return _stamp(done)


def _entry(stamp: str, database: int, uploads: int) -> dict:
    return {"time": stamp,
            "date": datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc),
            "database_bytes": database, "uploads_bytes": uploads}


def _from_sizes(sizes: dict[str, int]) -> list[dict]:
    """Backups, newest first, from their files' sizes by name."""
    stamps = sorted((f.removesuffix("-db.sql.gz") for f in sizes
                     if f.endswith("-db.sql.gz") and STAMP.match(f.removesuffix("-db.sql.gz"))), reverse=True)
    return [_entry(stamp, sizes[f"{stamp}-db.sql.gz"], sizes.get(f"{stamp}-storage.tar.gz", 0)) for stamp in stamps]


def _on_server(remote: Remote) -> list[dict]:
    """The backups there, newest first: time, date, sizes."""
    found = remote.must("cd backups 2>/dev/null && stat -c '%n %s' *-db.sql.gz *-storage.tar.gz 2>/dev/null || true",
                        "listing the backups")
    return _from_sizes({name: int(count) for name, count in
                        (line.rsplit(" ", 1) for line in found.splitlines() if " " in line)})


def _local_dir(instance: Instance) -> Path:
    return instance.dir / LOCAL_BACKUPS


def _here(instance: Instance) -> list[dict]:
    """The copies on this machine (`backup download`), newest first."""
    folder = _local_dir(instance)
    if not folder.is_dir():
        return []
    return _from_sizes({path.name: path.stat().st_size for path in folder.iterdir() if path.is_file()})


def _files(stamp: str) -> list[str]:
    return [f"{stamp}-db.sql.gz", f"{stamp}-storage.tar.gz"]


def _merged(server: list[dict], here: list[dict]) -> list[dict]:
    """Every backup, newest first, with where it is: on the server, here, or both."""
    found: dict[str, dict] = {}
    for where, backups in (("server", server), ("here", here)):
        for backup in backups:
            entry = found.setdefault(backup["time"], {**backup, "server": False, "here": False})
            entry[where] = True
    return sorted(found.values(), key=lambda backup: backup["time"], reverse=True)


def _table(backups: list[dict], where: bool = True):
    from rich.padding import Padding
    from rich.table import Table

    now = datetime.datetime.now(datetime.timezone.utc)
    table = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
    # The time (UTC, the date in it) whole, always: it is what restore and delete take.
    table.add_column(style="bold", no_wrap=True, min_width=16)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(style="dim")
    if where:
        table.add_column(style="cyan", no_wrap=True)
    for backup in backups:
        places = ", ".join(place for place in ("server", "here") if backup.get(place, False))
        table.add_row(backup["time"], age(backup["date"], now),
                      f"database {size(backup['database_bytes'])}, uploads {size(backup['uploads_bytes'])}",
                      *((places,) if where else ()))
    return Padding(table, (0, 0, 0, 2))


def _as_data(backups: list[dict]) -> list[dict]:
    return [{**backup, "date": backup["date"].isoformat()} for backup in backups]


def listing(name: str) -> int:
    """The backups on the server and here, newest first, and when the next ones come.
    Without the server, the ones here."""
    instance = load(name)
    try:
        remote = deployed(instance)
    except DeployError as error:
        remote = None
        ui.warn(f"only the copies here: {error}")
    server = _on_server(remote) if remote else []
    backups = _merged(server, _here(instance))
    found = (remote.output(f"crontab -l 2>/dev/null | grep -F '{CRON_MARK}'; date +%Z") or "").splitlines() \
        if remote else []
    line = next((entry for entry in found if CRON_MARK in entry), "")
    schedule, zone = (line.split(" cd ", 1)[0] if line else None), (found[-1] if found else "?")
    days = instance.get("INATRACE_BACKUP_DAYS", "7")
    ui.section(f"Backups of {name}, newest first")

    def render() -> None:
        if backups:
            ui.out.print(_table(backups))
        else:
            ui.info(f"none yet: inatrace deploy backup create {name}")
        ui.blank()
        if remote:
            ui.info((f"made {in_words(schedule)}, {zone}" if schedule else "none scheduled (INATRACE_BACKUP_SCHEDULE)")
                    + f"; kept {days} days on the server (INATRACE_BACKUP_DAYS); here, until you delete them")
        if backups:
            ui.info(f"inatrace deploy backup restore {name} <time> puts one back (from here too); "
                    "download copies them here")

    ui.show("backups", {"backups": _as_data(backups), "schedule": schedule, "zone": zone if schedule else None,
                        "days": int(days) if days.isdigit() else 7, "here": shown(_local_dir(instance))}, render)
    return 0


def missing_here(remote: Remote, instance: Instance) -> list[dict]:
    """The server's backups not copied here yet, newest first."""
    here = {backup["time"] for backup in _here(instance)}
    return [backup for backup in _on_server(remote) if backup["time"] not in here]


def fetch(remote: Remote, instance: Instance, backups: list[dict]) -> None:
    """Copies these backups of the server here."""
    total = sum(backup["database_bytes"] + backup["uploads_bytes"] for backup in backups)
    with ui.doing(f"copying {len(backups)} backup{'s' if len(backups) > 1 else ''} here  [dim]({size(total)})[/]"):
        remote.fetch([f"backups/{file}" for backup in backups for file in _files(backup["time"])],
                     _local_dir(instance))


def available(instance: Instance, remote: Remote | None = None) -> list[str]:
    """The backups' times, newest first, here or (with `remote`) on the server too."""
    return [backup["time"] for backup in _merged(_on_server(remote) if remote else [], _here(instance))]


def newest(instance: Instance, remote: Remote | None = None) -> str | None:
    found = available(instance, remote)
    return found[0] if found else None


def local_backups(instance: Instance) -> list[dict]:
    return _here(instance)


def _times(name: str, stamps: list[str]) -> list[str]:
    bad = [stamp for stamp in stamps if not STAMP.match(stamp)]
    if bad:
        raise DeployError(f"not a backup time: {', '.join(bad)}; inatrace deploy backup list {name} shows them")
    return list(dict.fromkeys(stamps))


def download(name: str, stamps: list[str], dry_run: bool = False) -> int:
    """Copies backups from the server here (deploy/instances/<name>/backups/): these, or
    every one not here yet."""
    instance = load(name)
    stamps = _times(name, stamps)
    remote = deployed(instance)
    server = {backup["time"]: backup for backup in _on_server(remote)}
    here = {backup["time"] for backup in _here(instance)}
    missing = [stamp for stamp in stamps if stamp not in server]
    if missing:
        raise DeployError(f"no backup {', '.join(missing)} on {remote.destination}: inatrace deploy backup list {name}")
    wanted = stamps or [stamp for stamp in server if stamp not in here]
    if not wanted:
        ui.ok(f"all {len(server)} backups of the server are here already ({shown(_local_dir(instance))})")
        return 0
    if dry_run:
        would(*(f"copy backup {stamp} here" for stamp in wanted), note=f"into {shown(_local_dir(instance))}")
        return 0
    fetch(remote, instance, [server[stamp] for stamp in wanted])
    ui.show("downloaded", {"times": wanted, "into": shown(_local_dir(instance))},
            lambda: ui.info(f"in {shown(_local_dir(instance))}: they stay until you delete them "
                            f"(inatrace deploy backup delete {name} <time> --local)"))
    return 0


def restore(name: str, stamp: str, dry_run: bool = False, backup: bool = True) -> int:
    instance = load(name)
    _times(name, [stamp])
    remote = deployed(instance)
    on_server = remote.output(f"test -f ~/{REMOTE_DIR}/backups/{stamp}-db.sql.gz && "
                              f"test -f ~/{REMOTE_DIR}/backups/{stamp}-storage.tar.gz") is not None
    here = [_local_dir(instance) / file for file in _files(stamp)]
    if not on_server and not all(path.is_file() for path in here):
        raise DeployError(f"no backup {stamp}, on {remote.destination} or here: inatrace deploy backup list {name}")
    if dry_run:
        would(*([] if on_server else [f"send backup {stamp} from here to the server"]),
              *(["back up the database and the uploads as they are now"] if backup else []),
              f"stop the backend, replace the database and the uploads with backup {stamp}, start it again")
        return 0
    if not on_server:
        with ui.doing(f"sending backup {stamp} to the server  "
                      f"[dim]({size(sum(path.stat().st_size for path in here))})[/]"):
            remote.send(here, "backups")
    if backup:
        backup_first(remote, name, "--no-backup skips it")
    with ui.doing(f"restoring {stamp}  [dim](the backend stops meanwhile)[/]"):
        remote.must(f"./restore.sh {stamp}", "the restore")
    with ui.doing("starting backend"):
        wait_healthy(remote, name, "backend", minutes=6)
    ui.ok(f"{name} is back to {stamp}")
    return 0


def _confirm(count: int) -> bool:
    try:
        return ui.confirm(f"Delete {'it' if count == 1 else f'these {count}'}?", False)
    except ui.NoAnswer:
        raise DeployError("no answer for “Delete?”: give --auto-approve", flag="--auto-approve") from None


def delete(name: str, stamps: list[str], dry_run: bool = False, auto_approve: bool = False,
           force: bool = False, local: bool = False) -> int:
    """Removes these backups from the server, once confirmed, never the last one left
    unless forced; or, `local`, the copies here."""
    instance = load(name)
    stamps = _times(name, stamps)
    remote = None if local else deployed(instance)
    backups = _here(instance) if local else _on_server(remote)
    place = f"here ({shown(_local_dir(instance))})" if local else remote.destination
    there = {backup["time"] for backup in backups}
    missing = [stamp for stamp in stamps if stamp not in there]
    if missing:
        raise DeployError(f"no backup {', '.join(missing)} {'here' if local else 'on ' + place}: "
                          f"inatrace deploy backup list {name}")
    if not local and len(stamps) == len(backups) and not force:
        raise DeployError("that leaves no backup on the server: --force if that is what you want")
    going = [backup for backup in backups if backup["time"] in stamps]
    left = len(backups) - len(going)
    ui.section(f"Deleting backups of {name}{' (the copies here)' if local else ''}")

    def render() -> None:
        ui.out.print(f"  [bold]{'Would' if dry_run else 'Will'} delete:[/]")
        ui.out.print(_table(going, where=False))
        ui.info(f"{left} left {place if local else 'on ' + place}")

    ui.show("plan", {"actions": [f"delete backup {stamp}" + (" here" if local else "") for stamp in stamps],
                     "backups": _as_data(going), "left": left}, render)
    if dry_run:
        ui.info("dry run: nothing was changed")
        return 0
    if not auto_approve and not _confirm(len(stamps)):
        ui.info("nothing was changed")
        return 0
    with ui.doing(f"deleting {len(stamps)} backup{'s' if len(stamps) > 1 else ''}"):
        if local:
            for stamp in stamps:
                for file in _files(stamp):
                    (_local_dir(instance) / file).unlink(missing_ok=True)
        else:
            remote.must("rm -f " + " ".join(f"backups/{file}" for stamp in stamps for file in _files(stamp)),
                        "deleting the backups")
    return 0
