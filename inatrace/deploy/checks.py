"""Looking at it: `deploy status`, `logs` and `smoke`."""

import datetime
import json
import re
import shlex
import time

from rich.markup import escape

from .. import shell, ui
from .. import smoke as smoke_
from .backups import CRON_MARK, in_words, schedule_of
from .common import age, size
from .instance import Instance, load
from .remote import MONITORING, SERVICES, Remote, deployed


def _site_probe(instance: Instance) -> str:
    """A shell line printing the codes of the frontend and of the API, through Caddy."""
    site = instance.get("INATRACE_SITE")
    headers = ""
    if instance.get("INATRACE_ORIGIN_SECRET"):
        headers = f" -H 'X-Origin-Secret: {instance.get('INATRACE_ORIGIN_SECRET')}'"
    curl = f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 10 --resolve {site}:443:127.0.0.1{headers}"
    return f"{curl} https://{site}/ ; echo -n ' ' ; {curl} https://{site}/api/user/profile"


def _certificate_probe(instance: Instance) -> str:
    """A shell line printing what curl says of the server's own certificate: whether it
    is trusted (checked against the system's CAs, which -k does not even load), then its
    issuer and end (with -k, so an untrusted one shows them too). Straight to Caddy, around
    a CDN: the one it renews."""
    site = instance.get("INATRACE_SITE")
    curl = f"curl -sv -o /dev/null --max-time 10 --resolve {site}:443:127.0.0.1 https://{site}/"
    return (f"{{ {curl}; {curl.replace('curl -sv', 'curl -skv')}; }} 2>&1 "
            "| grep -E 'expire date:|issuer:|certificate verify ok'")


def certificate_of(lines: list[str], tls: str, now: datetime.datetime) -> dict | None:
    """The certificate, from curl's words: issuer, end, days left, trusted; and how it
    stands: ok, renew (fewer than 14 days left: renewing fails), bad (untrusted, or
    fewer than 7). A self-signed one (internal) or staging's is never trusted, and fine."""
    text = "\n".join(lines)
    ends = re.search(r"expire date: (.+)", text)
    if not ends:
        return None
    try:
        expires = datetime.datetime.strptime(ends.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    issuer = re.search(r"issuer: .*?O ?= ?([^;,/]+)", text)
    trusted = "verify ok" in text
    days = (expires - now).days
    state = ("ok" if tls != "acme" else "bad" if not trusted or days < 7 else "renew" if days < 14 else "ok")
    return {"issuer": issuer.group(1).strip() if issuer else None, "expires": expires.isoformat(),
            "days": days, "trusted": trusted, "state": state}


# Each container's CPU and memory, read from its cgroup (docker stats spends ~2 s sampling).
# CPU is a rate: its counters are read when the gathering starts and again when it ends, so
# the interval is the time the gathering takes anyway. Memory is less the reclaimable page
# cache, as docker stats counts it.
_USAGE_START = r"""
cg() { for f in /sys/fs/cgroup/system.slice/docker-$1.scope /sys/fs/cgroup/docker/$1; do
         [ -d "$f" ] && { echo "$f"; return; }; done; }
cpu() { awk '/^usage_usec/ {print $2}' "$1/cpu.stat" 2>/dev/null || echo 0; }
ids=$(docker compose ps -q); t1=$(date +%s%N); before=""
for id in $ids; do before="$before $id=$(cpu "$(cg "$id")")"; done
"""


_USAGE_END = r"""
t2=$(date +%s%N); echo '#usage'
for id in $ids; do
  f=$(cg "$id"); service=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
  start=$(for pair in $before; do [ "${pair%%=*}" = "$id" ] && echo "${pair#*=}"; done)
  memory=$(( $(cat "$f/memory.current" 2>/dev/null || echo 0) - $(awk '/^inactive_file / {print $2}' "$f/memory.stat" 2>/dev/null || echo 0) ))
  echo "$service ${start:-0} $(cpu "$f") $memory"
done
echo "interval $((t2 - t1))"
"""


def _status_data(remote: Remote, instance: Instance) -> dict:
    """What `status` shows, gathered in one login: the site, the server, the containers
    (state, image, CPU, memory, uptime) and the backups."""
    found = remote.must(
        _USAGE_START + "echo '#containers'; docker compose ps -a --format json; "
        "echo '#site'; " + _site_probe(instance) + "; echo; "
        "echo '#certificate'; " + _certificate_probe(instance) + "; "
        "echo '#system'; . /etc/os-release; echo \"${PRETTY_NAME:-$ID}\"; "
        "docker version --format '{{.Server.Version}}'; df -P -B1 / | tail -1; "
        "free -b | awk '/^Mem:/ {print $2, $7}'; cut -d' ' -f1 /proc/loadavg; nproc; "
        "echo '#backups'; ls -1 backups 2>/dev/null | grep -- '-db.sql.gz$' | sort -r; "
        f"echo '#schedule'; crontab -l 2>/dev/null | grep -F {shlex.quote(CRON_MARK)}; date +%Z\n" + _USAGE_END,
        "the status")
    parts: dict[str, list[str]] = {}
    current = ""
    for line in found.splitlines():
        if line.startswith("#"):
            current = line[1:]
            parts[current] = []
        elif line.strip():
            parts.setdefault(current, []).append(line)

    codes = (parts.get("site") or [""])[0].split()
    data: dict = {"site": {"url": f"https://{instance.get('INATRACE_SITE')}", "codes": codes,
                           "tls": instance.get("INATRACE_TLS", "acme"),
                           "answers": codes == ["200", "401"]}}
    system = parts.get("system", [])
    if len(system) >= 6:
        disk, mem = system[2].split(), system[3].split()
        data["server"] = {"system": system[0], "docker": system[1],
                          "disk": {"total": int(disk[1]), "used": int(disk[2])},
                          "memory": {"total": int(mem[0]), "used": int(mem[0]) - int(mem[1])},
                          "load": float(system[4]), "cpus": int(system[5])}

    usage, interval = {}, 0
    for line in parts.get("usage", []):
        fields = line.split()
        if fields[0] == "interval":
            interval = int(fields[1])
        elif len(fields) == 4:
            usage[fields[0]] = (int(fields[1]), int(fields[2]), int(fields[3]))
    containers = []
    by_service = {}
    for line in parts.get("containers", []):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        by_service[entry.get("Service", "")] = entry
    for service in SERVICES + (MONITORING if instance.monitoring else ()):
        entry = by_service.get(service)
        if not entry:
            containers.append({"service": service, "state": "missing"})
            continue
        before, after, memory = usage.get(service, (0, 0, 0))
        cpu = round((after - before) * 1000 * 100 / interval, 1) if interval and after >= before else None
        uptime = (entry.get("Status", "").removeprefix("Up ").split(" (")[0]
                  .replace("About an", "1").replace("About a", "1").replace("Less than a second", "a moment"))
        containers.append({"service": service, "state": entry.get("State", ""),
                           "health": entry.get("Health", ""), "image": entry.get("Image", ""),
                           "cpu_percent": cpu, "memory": memory or None,
                           "uptime": uptime if entry.get("State") == "running" else entry.get("Status", "")})
    data["containers"] = containers
    backups = [b.removesuffix("-db.sql.gz") for b in parts.get("backups", [])]
    days = instance.get("INATRACE_BACKUP_DAYS", "7")
    scheduled = parts.get("schedule", [])
    line = next((entry for entry in scheduled if CRON_MARK in entry), "")
    data["backups"] = {"count": len(backups), "newest": backups[0] if backups else None,
                       "days": int(days) if days.isdigit() else 7,
                       "schedule": line.split(" cd ", 1)[0] if line else None,
                       "zone": scheduled[-1] if scheduled and CRON_MARK not in scheduled[-1] else None}
    data["certificate"] = certificate_of(parts.get("certificate", []), instance.get("INATRACE_TLS", "acme"),
                                         datetime.datetime.now(datetime.timezone.utc))
    data["ok"] = (data["site"]["answers"] and all(_container_color(c) == "green" for c in containers)
                  and (data["certificate"] or {}).get("state") != "bad")
    return data


def _certificate_words(certificate: dict | None, tls: str, name: str) -> str:
    """The certificate, for the Site line: fine in dim, renewing late in yellow, wrong in red."""
    if tls == "internal":
        return " [dim]· self-signed (an IP address)[/]"
    if not certificate:
        return " · [yellow]no certificate seen[/]"
    if tls == "acme-staging":
        return f" [dim]· Let's Encrypt's staging (not trusted), {certificate['days']} days left[/]"
    said = f"certificate {certificate['issuer'] or '?'}, {certificate['days']} days left"
    if certificate["state"] == "bad":
        return f" · [red]{said}{'' if certificate['trusted'] else ', not trusted'}[/]"
    if certificate["state"] == "renew":
        return f" · [yellow]{said}: renewing fails? (inatrace deploy logs {name} caddy)[/]"
    return f" [dim]· {said}[/]"


def _container_color(container: dict) -> str:
    state, health = container.get("state"), container.get("health", "")
    if state == "running" and health in ("", "healthy"):
        return "green"
    return "yellow" if state in ("restarting", "created") or health == "starting" else "red"


def _status_view(name: str, destination: str, data: dict):
    """The status as one rich renderable (printed once, or redrawn by --watch)."""
    from rich.console import Group
    from rich.padding import Padding
    from rich.table import Table
    from rich.text import Text

    site = data["site"]
    head = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
    head.add_column(style="bold")
    head.add_column()
    head.add_row("Site", f"{site['url']}  " + ("[green]✓ answers[/]" if site["answers"] else
                                               f"[red]✗ does not answer as expected[/] [dim](frontend, "
                                               f"API: {' '.join(site['codes']) or 'nothing'})[/]")
                 + _certificate_words(data.get("certificate"), site.get("tls", "acme"), name))
    server = data.get("server")
    if server:
        disk, mem = server["disk"], server["memory"]
        head.add_row("Server", f"{server['system']} · Docker {server['docker']} · load {server['load']:.2f} "
                               f"on {server['cpus']} CPUs · memory {mem['used'] * 100 // mem['total']}% of "
                               f"{size(mem['total'])} · disk {disk['used'] * 100 // disk['total']}% of "
                               f"{size(disk['total'])}")
    table = Table(box=None, padding=(0, 2), pad_edge=False, header_style="dim")
    for title, justify in (("", "left"), ("state", "left"), ("image", "left"), ("CPU", "right"),
                           ("memory", "right"), ("", "left")):
        table.add_column(title, justify=justify)
    for c in data["containers"]:
        color = _container_color(c)
        if c["state"] == "missing":
            table.add_row(f"[red]●[/] [bold]{c['service']}[/]", "[red]missing[/]", "", "", "", "")
            continue
        state = c["state"] + (f", {c['health']}" if c["health"] else "")
        cpu = "" if c["cpu_percent"] is None else f"{c['cpu_percent']:.1f}%"
        table.add_row(f"[{color}]●[/] [bold]{c['service']}[/]", f"[{color}]{state}[/]",
                      c["image"].rsplit("/", 1)[-1].replace(":", " "), cpu,
                      size(c["memory"]) if c["memory"] else "",
                      Text(f"up {c['uptime']}" if c["state"] == "running" else c["uptime"], style="dim"))
    if all(c["state"] == "missing" for c in data["containers"]):
        table = Text.from_markup(f"[yellow]down[/]: no container runs [dim](inatrace deploy up {name} "
                                 "starts it)[/]")
    backups = data["backups"]
    if backups["newest"]:
        when = datetime.datetime.strptime(backups["newest"], "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc)
        last = (f"  [bold]Backups[/]   newest {backups['newest']}, "
                f"{age(when, datetime.datetime.now(datetime.timezone.utc))} · {backups['count']} on the server")
    else:
        last = f"  [bold]Backups[/]   [yellow]none[/] [dim](inatrace deploy backup create {name})[/]"
    last += (f" · {in_words(backups['schedule'])}, {backups['zone'] or '?'}" if backups["schedule"]
             else f" · [yellow]none scheduled[/] [dim](INATRACE_BACKUP_SCHEDULE, then inatrace deploy up {name})[/]")
    return Group(Padding(head, (0, 0, 0, 2)), Text(""), Padding(table, (0, 0, 0, 2)), Text(""),
                 Text.from_markup(last))


def status(name: str, watch: bool = False, interval: float = 2.0) -> int:
    """The site, the server, its containers and the newest backup; with `watch`, the whole
    screen redrawn every `interval` seconds until Ctrl+C, like watch(1). The exit code
    says whether all is well."""
    instance = load(name)
    remote = deployed(instance)
    title = f"{name} on {remote.destination}"
    data = _status_data(remote, instance)
    if ui.json_mode():
        # Watching, one event each time; Ctrl+C (SIGINT) stops it.
        try:
            while True:
                ui.emit("status", **data)
                if not watch:
                    break
                time.sleep(interval)
                data = _status_data(remote, instance)
        except KeyboardInterrupt:
            pass
        return 0 if data["ok"] else 1
    if not watch:
        ui.section(title)
        ui.out.print(_status_view(name, remote.destination, data))
        return 0 if data["ok"] else 1
    from rich.console import Group
    from rich.live import Live
    from rich.rule import Rule
    from rich.text import Text

    def frame(data: dict, footer: bool = True):
        parts = [Rule(f"[bold cyan]{escape(title)}", align="left", style="cyan"),
                 _status_view(name, remote.destination, data)]
        if footer:
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            parts.append(Text(f"\n  {stamp} · every {interval:g}s · Ctrl+C stops", style="dim"))
        return Group(*parts)

    try:
        with Live(frame(data), console=ui.out, screen=True, auto_refresh=False) as live:
            while True:
                time.sleep(interval)
                data = _status_data(remote, instance)
                live.update(frame(data), refresh=True)
    except KeyboardInterrupt:
        pass
    # The full screen goes away with Live: leave the last look on the terminal.
    ui.out.print()
    ui.out.print(frame(data, footer=False))
    return 0 if data["ok"] else 1


def smoke(name: str, url: str | None, verbose: bool, extra: list[str]) -> int:
    """The smoke tests that only read, against the instance: from outside at its address,
    and over ssh for the images, the database and the logs."""
    instance = load(name)
    deployed(instance)
    secret = instance.get("INATRACE_ORIGIN_SECRET")
    return smoke_.run(verbose, False, extra, deployment={
        "name": name, "ssh": instance.get("INATRACE_SSH"),
        "url": url or f"https://{instance.get('INATRACE_SITE')}",
        # A self-signed certificate (an IP address) cannot be checked; Let's Encrypt's must be valid.
        "verify": instance.get("INATRACE_TLS", "acme") == "acme",
        "tls": instance.get("INATRACE_TLS", "acme"),
        "headers": {"X-Origin-Secret": secret} if secret else {},
        "swagger": instance.get("INATRACE_SWAGGER") == "on",
        "site": instance.get("INATRACE_SITE"), "secret": bool(secret),
        "images": {part.lower(): f"{instance.get(f'INATRACE_{part}_IMAGE') or f'ghcr.io/agstack/inatrace-{part.lower()}'}"
                                 f":{instance.get(f'INATRACE_{part}_VERSION')}" for part in ("BACKEND", "FRONTEND")},
        "backup_days": int(instance.get("INATRACE_BACKUP_DAYS", "7") or 7),
        "monitoring": instance.monitoring, "backup_schedule": schedule_of(instance)})


def logs(name: str, services: list[str], follow: bool) -> int:
    instance = load(name)
    command = "docker compose logs --tail 200" + (" --follow" if follow else "")
    command += "".join(f" {shlex.quote(s)}" for s in services)
    remote = deployed(instance)
    if not ui.json_mode():
        return remote.run(command, check=False, tty=follow)

    def line(text: str) -> None:
        service, said = shell.compose_log(text.rstrip("\n"))
        ui.emit("log", service=service, text=said)

    try:
        return remote.lines(command + " --no-color", line)
    except KeyboardInterrupt:
        return 0
