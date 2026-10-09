"""An instance: deploy/instances/<name>/, its settings (.env) checked and saved, and the
files `up` puts on the server."""

import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, paths
from .common import DeployError

EXAMPLE = paths.DEPLOY_DIR / ".env.example"


LOCAL_PROPERTIES = "backend.local.properties"


# The CDNs we know, and the header each sets with the client's address.
CDN_HEADERS = {
    "cloudflare": "CF-Connecting-IP",
    "cloudfront": "CloudFront-Viewer-Address",
    "fastly": "Fastly-Client-IP",
    "akamai": "True-Client-IP",
}


TLS = ("acme", "acme-staging", "internal")


FRONTS = ("direct", "cdn", "lb")


REQUIRED = ("INATRACE_SSH", "INATRACE_SITE", "INATRACE_BACKEND_VERSION", "INATRACE_FRONTEND_VERSION",
            "INATRACE_DB_PASSWORD", "INATRACE_DB_ROOT_PASSWORD", "INATRACE_JWT_KEY")


SECRETS = ("INATRACE_DB_PASSWORD", "INATRACE_DB_ROOT_PASSWORD", "INATRACE_JWT_KEY", "INATRACE_MONITORING_PASSWORD")


_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


_ALNUM = re.compile(r"^[A-Za-z0-9]+$")


EMAIL = re.compile(r"^[^@\s'\"\\]+@[^@\s'\"\\]+$")


HEADER = re.compile(r"^[A-Za-z0-9-]+$")
# A crontab schedule: five fields, or one of cron's @ names (not @reboot).
CRON = re.compile(r"^(@(yearly|annually|monthly|weekly|daily|midnight|hourly)|([0-9A-Za-z*/,-]+\s+){4}[0-9A-Za-z*/,-]+)$")


@dataclass
class Instance:
    name: str
    values: dict[str, str] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return paths.DEPLOY_INSTANCES / self.name

    @property
    def env_file(self) -> Path:
        return self.dir / ".env"

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key) or default

    @property
    def monitoring(self) -> bool:
        return self.get("INATRACE_MONITORING") == "on"


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise DeployError(f"'{name}' is not a valid instance name: lowercase letters, digits and -.")
    return name


def load(name: str) -> Instance:
    instance = Instance(check_name(name))
    if not instance.env_file.is_file():
        raise DeployError(f"no instance '{name}' ({instance.env_file} is missing): "
                          f"inatrace deploy init {name}")
    instance.values = config.parse(instance.env_file.read_text())
    return instance


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def problems(values: dict[str, str]) -> list[str]:
    """What is wrong or missing in an instance's settings, in words."""
    found = [f"{key} is empty" for key in REQUIRED if not values.get(key)]
    site = values.get("INATRACE_SITE", "")
    if site and not (is_ip(site) or DOMAIN.match(site)):
        found.append(f"INATRACE_SITE={site} is neither a domain nor an IP address (no https://, no path)")
    tls = values.get("INATRACE_TLS") or "acme"
    if tls not in TLS:
        found.append(f"INATRACE_TLS={tls} is not one of {', '.join(TLS)}")
    elif tls.startswith("acme") and is_ip(site):
        found.append(f"INATRACE_TLS={tls} needs a domain: use internal for an IP address")
    front = values.get("INATRACE_FRONT") or "direct"
    if front not in FRONTS:
        found.append(f"INATRACE_FRONT={front} is not one of {', '.join(FRONTS)}")
    header = values.get("INATRACE_CDN_IP_HEADER", "")
    if front == "cdn" and not header:
        found.append("INATRACE_FRONT=cdn needs INATRACE_CDN_IP_HEADER")
    if front == "cdn" and is_ip(site):
        found.append("INATRACE_FRONT=cdn needs a domain in INATRACE_SITE")
    if header and not HEADER.match(header):
        found.append(f"INATRACE_CDN_IP_HEADER={header} is not a header name")
    secret = values.get("INATRACE_ORIGIN_SECRET", "")
    if secret and not _ALNUM.match(secret):
        found.append("INATRACE_ORIGIN_SECRET takes letters and digits only")
    swagger = values.get("INATRACE_SWAGGER") or "off"
    if swagger not in ("on", "off"):
        found.append(f"INATRACE_SWAGGER={swagger} is not on or off")
    if values.get("INATRACE_MAIL_ENABLED") == "true" and not values.get("INATRACE_MAIL_HOST"):
        found.append("INATRACE_MAIL_ENABLED=true needs INATRACE_MAIL_HOST")
    monitoring = values.get("INATRACE_MONITORING") or "off"
    if monitoring not in ("on", "off"):
        found.append(f"INATRACE_MONITORING={monitoring} is not on or off")
    schedule = values.get("INATRACE_BACKUP_SCHEDULE") or "@daily"
    if not (schedule == "off" or CRON.match(schedule)):
        found.append(f"INATRACE_BACKUP_SCHEDULE={schedule} is not a cron expression (5 fields, or @daily...) "
                     "nor off")
    days = values.get("INATRACE_BACKUP_DAYS", "7")
    if not days.isdigit() or int(days) < 1:
        found.append(f"INATRACE_BACKUP_DAYS={days} is not a number of days, 1 or more")
    return found


def render_env(example: str, values: dict[str, str]) -> str:
    """The example .env with these values: each KEY= or #KEY= line of a key in
    `values` becomes KEY=value; the comments stay. Keys the example lacks go at the end."""
    lines, done = [], set()
    for line in example.splitlines():
        match = re.match(r"^#?([A-Z][A-Z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in values and key not in done:
            lines.append(f"{key}={values[key]}")
            done.add(key)
        else:
            lines.append(line)
    extra = [f"{key}={value}" for key, value in values.items() if key not in done]
    return "\n".join(lines + extra) + "\n"


def save(instance: Instance) -> None:
    instance.dir.mkdir(parents=True, exist_ok=True)
    instance.dir.chmod(0o700)
    instance.env_file.touch(mode=0o600)
    instance.env_file.chmod(0o600)
    instance.env_file.write_text(render_env(EXAMPLE.read_text(), instance.values))
    local = instance.dir / LOCAL_PROPERTIES
    if not local.exists():
        local.write_text((paths.DEPLOY_DIR / LOCAL_PROPERTIES).read_text())


def new_secret() -> str:
    return secrets.token_hex(24)


# The instance's own copies of its backups (`deploy backup download`): they stay here.
LOCAL_BACKUPS = "backups"


def server_files(instance: Instance) -> dict[str, Path]:
    """What `up` puts in ~/inatrace: deploy/server/, then the instance's own files
    (.env, backend.local.properties and anything else, but its backups/), which win on a
    clash."""
    files = {str(path.relative_to(paths.DEPLOY_SERVER)): path
             for path in sorted(paths.DEPLOY_SERVER.rglob("*")) if path.is_file()}
    files[LOCAL_PROPERTIES] = paths.DEPLOY_DIR / LOCAL_PROPERTIES
    files.update({str(path.relative_to(instance.dir)): path
                  for path in sorted(instance.dir.rglob("*"))
                  if path.is_file() and path.relative_to(instance.dir).parts[0] != LOCAL_BACKUPS})
    return files


def shown(path: Path) -> str:
    """The path as the user would type it: relative to the repo when inside it."""
    return str(path.relative_to(paths.ROOT)) if path.is_relative_to(paths.ROOT) else str(path)
