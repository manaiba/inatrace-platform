"""What a deployment adds to the stack: HTTPS, the edge's settings, the containers'
hardening, the versions it runs, backups and room. Only against a deployment
(`inatrace deploy smoke`); the edge's own answers are asked from inside Caddy's container."""
import datetime
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

import pytest

SERVICES = ("caddy", "frontend", "backend", "mysql")
MONITORING = ("beszel", "beszel-agent")


@pytest.fixture(scope="module")
def services(stack) -> tuple[str, ...]:
    """The containers that should run there: the monitoring's too, when it is on."""
    return SERVICES + (MONITORING if stack.spec.get("monitoring") else ())


@pytest.fixture(scope="module")
def caddy(stack):
    return stack.service("caddy")


def ask_caddy(caddy, *curl: str) -> tuple[str, str]:
    """(HTTP status, redirect target) of a request made inside Caddy's container; status
    000 when nothing answers."""
    found = caddy.exec("curl", "-s", "-o", "/dev/null", "--max-time", "5",
                       "-w", "%{http_code} %{redirect_url}", *curl).stdout.strip()
    status, _, target = found.partition(" ")
    return status, target


def straight(stack) -> list[str]:
    """curl options reaching the server itself under the site's name, around a CDN."""
    return ["--resolve", f"{stack.spec['site']}:443:127.0.0.1", f"https://{stack.spec['site']}/"]


def test_certificate(stack, detail):
    """Visitors get a valid HTTPS certificate (behind a CDN, the CDN's)"""
    if stack.spec.get("tls") == "acme-staging":
        pytest.skip("Let's Encrypt's staging: its CA is not trusted, by design")
    if not stack.verify:
        pytest.skip("self-signed (an IP address): nothing to check")
    url = urlsplit(stack.base_url)
    context = ssl.create_default_context()
    with socket.create_connection((url.hostname, url.port or 443), timeout=10) as raw, \
            context.wrap_socket(raw, server_hostname=url.hostname) as tls:
        certificate = tls.getpeercert()
    expires = datetime.datetime.fromtimestamp(ssl.cert_time_to_seconds(certificate["notAfter"]),
                                              datetime.timezone.utc)
    days = (expires - datetime.datetime.now(datetime.timezone.utc)).days
    issuer = dict(field[0] for field in certificate["issuer"])
    detail(f"{issuer.get('organizationName', '?')}, {days} days left")
    assert days > 7


def test_server_certificate(stack, caddy, detail):
    """The server's own certificate is valid, and not about to expire"""
    if stack.spec.get("tls") == "acme-staging":
        pytest.skip("Let's Encrypt's staging: its CA is not trusted, by design")
    if not stack.verify:
        pytest.skip("self-signed (an IP address): nothing to check")
    shown = caddy.exec("curl", "-sv", "--max-time", "10", "-o", "/dev/null", *straight(stack)).stderr
    expires = re.search(r"expire date: (.+)", shown)
    issuer = re.search(r"issuer: (.+)", shown)
    assert expires, f"no certificate checked: {shown.strip().splitlines()[-1:]}"
    when = datetime.datetime.strptime(expires.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=datetime.timezone.utc)
    days = (when - datetime.datetime.now(datetime.timezone.utc)).days
    organization = re.search(r"O=([^;,]+)", issuer.group(1)) if issuer else None
    detail(f"{organization.group(1) if organization else '?'}, {days} days left")
    # Caddy renews 30 days before: fewer than 14 left means renewing fails.
    assert days > 14


def test_http_redirects(stack, caddy, detail):
    """Plain HTTP redirects to HTTPS"""
    site = stack.spec["site"]
    status, target = ask_caddy(caddy, "-H", f"Host: {site}", "http://127.0.0.1/en/login")
    detail(f"HTTP {status} → {target}")
    assert status in ("301", "302", "307", "308") and target.startswith(f"https://{site}/")


def test_origin_secret(stack, caddy, detail):
    """Without the secret header, the server refuses (403)"""
    if not stack.spec.get("secret"):
        pytest.skip("no secret header (INATRACE_ORIGIN_SECRET)")
    status, _ = ask_caddy(caddy, "-k", *straight(stack))
    detail(f"HTTP {status} straight to it, without the header")
    assert status == "403"


def test_caddy_admin_off(caddy):
    """Caddy's admin API is off"""
    status, _ = ask_caddy(caddy, "http://127.0.0.1:2019/config/")
    assert status == "000", f"it answered HTTP {status}"


def test_runs_the_set_versions(stack, detail):
    """Backend and frontend run the images in .env"""
    running = {part: stack.service(part).image.name for part in ("backend", "frontend")}
    detail(", ".join(name.rsplit("/", 1)[-1] for name in running.values()))
    assert running == stack.spec["images"], "inatrace deploy up applies a changed .env"


def test_containers_hardened(stack, services, detail):
    """Every container: not root, all capabilities dropped, no new privileges"""
    problems = []
    for name in services:
        service = stack.service(name)
        if not service:  # missing: the health check says so
            continue
        uid = stack.host.run("ps", "-o", "uid=", "-p", service.inspect("{{.State.Pid}}")).stdout.strip()
        if uid in ("", "0"):
            problems.append(f"{name} runs as root")
        if "ALL" not in (json.loads(service.inspect("{{json .HostConfig.CapDrop}}")) or []):
            problems.append(f"{name} keeps its capabilities")
        if not any(option.startswith("no-new-privileges")
                   for option in json.loads(service.inspect("{{json .HostConfig.SecurityOpt}}")) or []):
            problems.append(f"{name} may gain privileges")
    detail(", ".join(services))
    assert not problems, "; ".join(problems)


def test_containers_healthy(stack, services, detail):
    """Every container passes its health check"""
    missing = [name for name in services if not stack.service(name)]
    assert not missing, f"not running: {', '.join(missing)}"
    health = {name: stack.service(name).inspect("{{.State.Health.Status}}") for name in services}
    detail(", ".join(f"{name} {state}" for name, state in health.items()))
    assert all(state == "healthy" for state in health.values())


def test_recent_backup(stack, detail):
    """A backup newer than INATRACE_BACKUP_DAYS"""
    found = stack.host.run("sh", "-c", "ls -1 ~/inatrace/backups 2>/dev/null | grep -- '-db.sql.gz$' | sort -r",
                           check=False).stdout.split()
    if not found:
        # A new deployment, before its first scheduled backup: not a failure yet.
        created = stack.service("mysql").inspect("{{.Created}}")[:19]
        new = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(created).replace(
            tzinfo=datetime.timezone.utc) < datetime.timedelta(hours=25)
        if new and stack.spec.get("backup_schedule", "@daily") != "off":
            pytest.skip(f"none yet: the first comes on schedule ({stack.spec.get('backup_schedule', '@daily')})")
        pytest.fail(f"no backup yet: inatrace deploy backup create {stack.spec['name']}", pytrace=False)
    newest = datetime.datetime.strptime(found[0][:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - newest
    detail(f"newest {found[0][:16]}, {age.days} days ago; {len(found)} on the server")
    # Older than what is kept: the next backup removes every other one.
    assert age.days < stack.spec["backup_days"]


def test_scheduled_backup(stack, detail):
    """Backups run on INATRACE_BACKUP_SCHEDULE (the server's crontab)"""
    schedule = stack.spec.get("backup_schedule", "@daily")
    if schedule == "off":
        pytest.skip("INATRACE_BACKUP_SCHEDULE is off")
    lines = [line for line in stack.host.run("sh", "-c", "crontab -l 2>/dev/null; true").stdout.splitlines()
             if "inatrace: scheduled backup" in line]
    detail(lines[0].split(" cd ", 1)[0] if lines else "not in the crontab")
    assert len(lines) == 1 and lines[0].startswith(f"{schedule} "), f"inatrace deploy up {stack.spec['name']} sets it"


def test_disk_room(stack, detail):
    """The server's disk is less than 90% full"""
    fields = stack.host.run("df", "-P", "-B1", "/").stdout.splitlines()[-1].split()
    used = int(fields[4].rstrip("%"))
    detail(f"{used}% used, {int(fields[3]) / 1e9:.1f} GB free")
    assert used < 90
