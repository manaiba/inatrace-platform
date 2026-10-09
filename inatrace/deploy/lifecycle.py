"""`deploy prepare`, `up`, `down` and `destroy`: the dependencies, starting or changing it,
stopping it, undoing it."""

import shlex
import shutil
import time

from .. import ui
from . import monitoring
from .backups import CRON_MARK, available, backup_first, cron_plan, fetch, install_cron, missing_here, restore
from .common import DeployError, would
from .instance import Instance, load, server_files, shown
from .remote import (BUSY, MONITORING, MOUNTED, REMOTE_DIR, SERVICES, Changes, Remote, connect, deployed,
                     install_prereqs, wait_healthy)


def _wait_ready(remote: Remote, instance: Instance, minutes: int = 6) -> None:
    """Polls the site through Caddy, on the server: the frontend and the backend answer."""
    site = instance.get("INATRACE_SITE")
    headers = ""
    if instance.get("INATRACE_ORIGIN_SECRET"):
        headers = f" -H 'X-Origin-Secret: {instance.get('INATRACE_ORIGIN_SECRET')}'"
    curl = f"curl -sk -o /dev/null -w '%{{http_code}}' --resolve {site}:443:127.0.0.1{headers}"
    probe = f"{curl} https://{site}/ ; echo -n ' ' ; {curl} https://{site}/api/user/profile"
    deadline = time.monotonic() + minutes * 60
    answer = ""
    while time.monotonic() < deadline:
        answer = remote.output(probe) or ""
        if answer == "200 401":
            return
        time.sleep(5)
    raise DeployError(f"the site did not answer as expected in {minutes} min (frontend, backend: "
                      f"{answer or 'nothing'}): inatrace deploy logs {instance.name}")


# The way out first: each starts once what it calls is healthy.
START_ORDER = ("mysql", "backend", "frontend", "caddy")


def _check(remote: Remote, name: str) -> None:
    with ui.doing(f"checking {remote.destination}"):
        if not shutil.which("rsync"):
            raise DeployError("rsync is missing here: install it (docs/deploy.md → What you need)")
        if not remote.ready():
            raise DeployError(f"the dependencies do not work on {remote.destination} over ssh: "
                              f"inatrace deploy prepare {name}, or docs/deploy.md → The server")


def prepare(name: str, dry_run: bool = False) -> int:
    instance = load(name)
    remote = connect(instance)
    ui.section(f"Preparing {remote.destination}")
    with ui.doing(f"looking at {remote.destination}"):
        probe = remote.probe()
    if not probe.reached:
        raise DeployError(f"could not log in to {remote.destination} without a question")
    if probe.ready:
        ui.ok(f"{probe.system}, already with the dependencies")
        return 0
    if not probe.profile:
        raise DeployError(f"no profile for {probe.system} ({probe.ids.strip()}) in deploy/prereqs/: "
                          "install the dependencies as docs/deploy.md → The server says")
    if probe.busy:
        raise DeployError(f"{BUSY}: try again in a few minutes")
    if dry_run:
        would(f"install the dependencies on {remote.destination} with "
               f"deploy/prereqs/{probe.profile}.sh [dim](sudo)[/]")
        return 0
    install_prereqs(remote, probe.profile)
    return 0


def _show_plan(changes: "Changes", actions: list[tuple[str, str]], restarts: list[str],
               backs_up: bool, schedule: str | None, restore_time: str | None = None) -> None:
    data = {"files": {"add": changes.added, "change": changes.changed, "remove": changes.removed},
            "backup": backs_up, "containers": [{"action": a, "service": s} for a, s in actions],
            "restarts": restarts, "schedule": ui.plain(schedule) if schedule else None, "restore": restore_time}
    ui.show("plan", data, lambda: _render_plan(changes, actions, restarts, backs_up, schedule, restore_time))


def _render_plan(changes: "Changes", actions: list[tuple[str, str]], restarts: list[str],
                 backs_up: bool, schedule: str | None, restore_time: str | None = None) -> None:
    ui.out.print("  [bold]Would do:[/]")
    for title, paths_ in (("add", changes.added), ("change", changes.changed), ("remove", changes.removed)):
        if paths_:
            ui.out.print(f"   {title} [dim]in ~/{REMOTE_DIR}:[/] {', '.join(paths_)}")
    if not changes:
        ui.out.print("   [dim]no file changes[/]")
    if backs_up:
        ui.out.print("   back up the database and the uploads first")
    for action, service in actions:
        ui.out.print(f"   {action} {service}")
    for service in restarts:
        ui.out.print(f"   restart {service} [dim](its mounted files change)[/]")
    if not actions and not restarts:
        ui.out.print("   [dim]no container changes[/]")
    if schedule:
        ui.out.print(f"   {schedule}")
    if restore_time:
        ui.out.print(f"   then restore backup {restore_time} (from here, when only here)")


DATA = ("backend", "mysql")


def _backs_up(backup: bool | None, running: bool, actions: list[tuple[str, str]],
              restarts: list[str]) -> bool:
    """Whether `up` backs up first: only when it already runs; with --backup always, with
    --no-backup never, and otherwise when what touches the data (the backend, the database)
    is about to change."""
    if not running or backup is not None:
        return running and bool(backup)
    return any(service in DATA for _, service in actions) or any(s in DATA for s in restarts)


def up(name: str, backup: bool | None = None, dry_run: bool = False, restore_time: str | None = None) -> int:
    instance = load(name)
    remote = connect(instance)
    ui.section(f"{'Planning' if dry_run else 'Deploying'} {name} on {remote.destination}")
    if restore_time:
        # Before anything happens: a wrong time stops it here, not after the containers.
        found = available(instance, remote if remote.output(f"test -d ~/{REMOTE_DIR}") is not None else None)
        restore_time = (found[0] if found else None) if restore_time == "newest" else restore_time
        if restore_time not in found:
            raise DeployError(f"no backup {restore_time or ''} of {name} to restore, here or there: "
                              f"inatrace deploy backup list {name}")
    _check(remote, name)
    if not dry_run:
        monitoring.settle(remote, instance)
    files = server_files(instance)
    running = bool(remote.output(f"cd ~/{REMOTE_DIR} && docker compose ps -q mysql 2>/dev/null"))
    order = START_ORDER + (MONITORING if instance.monitoring else ())
    # Turned off: its containers go (their history stays, in a volume).
    leaving = not instance.monitoring and monitoring.present(remote)
    if dry_run:
        with ui.doing("comparing the files"):
            changes = remote.sync(files, dry_run=True)
        actions: list[tuple[str, str]] = []
        if running:
            with ui.doing("asking Compose what it would do"):
                actions = remote.compose_plan(files)
        else:
            actions = [("create", service) for service in order]
        actions += [("remove", service) for service in MONITORING if leaving]
        # Turned on: .env gets its profile only once `up` runs, so Compose cannot tell yet.
        if running and instance.monitoring and not monitoring.present(remote):
            actions += [("create", service) for service in MONITORING]
        recreated = {service for _, service in actions}
        restarts = [s for s, mounted in MOUNTED.items()
                    if running and s not in recreated and changes.touching(mounted)]
        _show_plan(changes, actions, restarts, _backs_up(backup, running, actions, restarts),
                   cron_plan(remote, instance)[0], restore_time)
        ui.info("dry run: nothing was changed")
        return 0
    with ui.doing("copying the files"):
        changes = remote.sync(files)
    with ui.doing("pulling the images"):
        remote.must("docker compose pull --quiet --ignore-buildable", "pulling the images")
    actions, restarts = [], []
    if running:
        actions = remote.plan()
        recreated = {service for _, service in actions}
        restarts = [s for s, mounted in MOUNTED.items() if s not in recreated and changes.touching(mounted)]
    if _backs_up(backup, running, actions, restarts):
        backup_first(remote, name, "the backend or the database changes; --no-backup skips it"
                      if backup is None else "--backup")
    planned = {service: action for action, service in actions} if running else {}
    existing = set((remote.output(f"cd ~/{REMOTE_DIR} && docker compose ps -a --services") or "").split())
    for service in order:
        if service == "beszel-agent":
            with ui.doing("pairing the monitoring's agent with its hub"):
                if monitoring.pair(remote, instance):
                    planned[service] = "recreate"
        verb, why = (("starting", "") if service not in existing or planned.get(service) == "create" else
                     ("recreating", "a new image or settings") if planned.get(service) == "recreate" else
                     ("restarting", "its mounted files changed") if service in restarts else
                     ("checking", ""))
        with ui.doing(f"{verb} {service}" + (f"  [dim]({why})[/]" if why else "")):
            command = "restart" if verb == "restarting" else "up -d --remove-orphans"
            remote.must(f"docker compose {command} {service}", f"{verb} {service}")
            wait_healthy(remote, name, service, minutes=6 if service == "backend" else 2)
    if instance.monitoring:
        with ui.doing("adding the server to the monitoring"):
            monitoring.register(remote, instance)
    if leaving:
        with ui.doing("removing the monitoring  [dim](INATRACE_MONITORING is off; its history stays)[/]"):
            remote.must(f"docker compose --profile monitoring rm -sf {' '.join(MONITORING)} beszel-init",
                        "removing the monitoring")
    schedule, lines = cron_plan(remote, instance)
    if schedule:
        with ui.doing(schedule):
            install_cron(remote, lines)
    with ui.doing("checking that the site answers"):
        _wait_ready(remote, instance, minutes=2)
    if restore_time:
        # Over what ran already, a backup first; a server just set up has nothing to keep.
        restore(name, restore_time, backup=running)
    url = f"https://{instance.get('INATRACE_SITE')}"
    ui.show("up", {"url": url}, lambda: ui.out.print(f"\n  [bold green]Up:[/] [bold]{url}[/]"))
    return 0


def down(name: str, dry_run: bool = False) -> int:
    """Stops and removes the containers; the volumes (database, uploads, certificates),
    the files and the backups stay, so `up` starts it again as it was."""
    instance = load(name)
    remote = deployed(instance)
    ui.section(f"{'Planning' if dry_run else 'Stopping'} {name} on {remote.destination}")
    found = remote.must("docker compose ps --format '{{.Service}}'", "listing the containers").split()
    # The way in first: nothing gets a request a stopped one should answer.
    running = [s for s in SERVICES if s in found] + sorted(s for s in found if s not in SERVICES)
    if not running:
        ui.ok(f"nothing to stop: {name} is already down (no container runs on {remote.destination})")
        return 0
    if dry_run:
        would(f"stop and remove {', '.join(running)} on {remote.destination}; the site goes offline",
               note="the database data, the uploads, the certificates and the backups stay")
        return 0
    for service in running:
        with ui.doing(f"stopping {service}"):
            remote.must(f"docker compose stop {shlex.quote(service)}", f"stopping {service}")
    with ui.doing("removing the containers"):
        remote.must("docker compose down --remove-orphans", "removing the containers")
    ui.info(f"the database data, the uploads, the certificates and the backups stay: "
            f"inatrace deploy up {name} starts it again")
    return 0


def _remove_cron(remote: Remote) -> bool:
    """Takes the scheduled backup's line out of the crontab, the user's own lines kept.
    Whether there was one."""
    current = (remote.output("crontab -l 2>/dev/null; true") or "").splitlines()
    kept = [line for line in current if CRON_MARK not in line]
    if kept == current:
        return False
    install_cron(remote, kept)
    return True


def destroy(name: str, dry_run: bool = False, auto_approve: bool = False, backup: bool = True) -> int:
    """Undoes what `up` did on the server: its containers, volumes and images, ~/inatrace
    with the backups in it, the scheduled backup. First, unless told not to, a backup now and
    every backup of the server copied here. What `prepare` installed stays (Docker, rsync,
    cron), and so does the instance here, with its .env and backups: `up` starts it again,
    on this server or another, `--restore` putting a backup back."""
    instance = load(name)
    remote = connect(instance)
    ui.section(f"{'Planning to destroy' if dry_run else 'Destroying'} {name} on {remote.destination}")
    there = remote.output(f"test -d ~/{REMOTE_DIR} && echo yes") == "yes"
    project = "--filter label=com.docker.compose.project=inatrace"
    found = remote.output(f"docker ps -aq {project} | wc -l; docker volume ls -q {project} | tr '\\n' ' '; echo; "
                          f"du -sh ~/{REMOTE_DIR} 2>/dev/null | cut -f1; crontab -l 2>/dev/null | grep -cF '{CRON_MARK}'; "
                          f"ls ~/{REMOTE_DIR}/backups 2>/dev/null | grep -c -- '-db.sql.gz$'") or ""
    containers, volumes, folder, cron, server_backups = (found.splitlines() + ["0", "", "", "0", "0"])[:5]
    running = there and bool(remote.output(f"cd ~/{REMOTE_DIR} && docker compose ps -q --status running mysql"))
    if not there and containers.strip() in ("", "0") and not volumes.strip() and cron.strip() in ("", "0"):
        ui.ok(f"nothing of {name} on {remote.destination}")
        return 0
    copies = missing_here(remote, instance) if there and backup else []
    actions = []
    if backup and running:
        actions.append("back up now")
    if backup and (copies or running):
        actions.append(f"copy here the server's backups not here yet ({len(copies) + (1 if running else 0)})")
    actions += [*(["remove the scheduled backup from the crontab"] if cron.strip() not in ("", "0") else []),
                f"remove {containers.strip() or 0} containers, the volumes ({volumes.strip() or 'none'}) and "
                "the images: the database, the uploads, the certificates, the monitoring's history",
                *([f"remove ~/{REMOTE_DIR} ({folder or '?'}), its {server_backups.strip() or 0} backups included"]
                  if there else [])]
    note = (f"stay: Docker, rsync and cron there; {shown(instance.dir)} here, with its .env and backups"
            + ("" if backup else " (--no-backup: nothing copied first)"))
    if dry_run:
        would(*actions, note=note)
        return 0
    def render() -> None:
        ui.out.print("  [bold]Will do:[/]")
        for action in actions:
            ui.out.print(f"   {action}")
        ui.info(note)

    ui.show("plan", {"actions": actions, "note": note}, render)
    if not auto_approve:
        try:
            typed = ui.ask_text(f"Type {name} to destroy it")
        except ui.NoAnswer:
            raise DeployError(f"no answer for “Type {name} to destroy it”: give --auto-approve",
                              flag="--auto-approve") from None
        if typed != name:
            ui.info("nothing was changed")
            return 0
    if backup and running:
        backup_first(remote, name, "--no-backup skips it")
        copies = missing_here(remote, instance)
    if backup and copies:
        fetch(remote, instance, copies)
    if _remove_cron(remote):
        ui.ok("the scheduled backup is out of the crontab")
    if there:
        with ui.doing("removing the containers, the volumes and the images"):
            remote.must("docker compose --profile monitoring --profile tools down --volumes --rmi all "
                        "--remove-orphans --timeout 30", "removing the containers")
        with ui.doing(f"removing ~/{REMOTE_DIR}"):
            remote.must(f"cd && rm -rf ~/{REMOTE_DIR}", f"removing ~/{REMOTE_DIR}")
    left = remote.output(f"docker ps -aq {project} | wc -l; docker volume ls -q {project} | wc -l; "
                         f"test -d ~/{REMOTE_DIR} && echo dir || echo none") or ""
    if left.split() != ["0", "0", "none"]:
        raise DeployError(f"something of {name} is left on {remote.destination}: {' '.join(left.split())} "
                          "(containers, volumes, directory)")
    ui.ok(f"{name} is gone from {remote.destination}")
    ui.info(note)
    ui.info("outside the CLI's reach, if they go too: the server itself, its DNS record, a CDN's rules")
    ui.info(f"again: inatrace deploy up {name}  (--restore <time> puts a backup back)")
    return 0
