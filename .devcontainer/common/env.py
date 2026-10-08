"""The repo root's .env, as the dev container reads it.

KEY=VALUE lines, see .env.example. Parsed, never sourced: variables exported in
the shell are ignored. Only the keys the dev container uses are read here;
`inatrace` reads the rest (and reports unknown keys).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

DEVCONTAINER_DIR = Path(__file__).resolve().parent.parent
ROOT = DEVCONTAINER_DIR.parent
USER_ENV = ROOT / ".env"

# The dev container's settings and their defaults.
DEFAULTS = {
    "INATRACE_INSTANCE": "inatrace-platform",
    "INATRACE_BIND_ADDRESS": "127.0.0.1",
    "INATRACE_GATEWAY_PORT": "8000",
    "INATRACE_BACKEND_PORT": "9000",
    "INATRACE_FRONTEND_PORT": "9080",
    "PLAYWRIGHT_HEADLESS": "",
}
# GitHub: the optional token, used by provision.py.
TOKEN_KEY = "INATRACE_GH_TOKEN"
_INSTANCE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def parse(text: str) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments skipped, one level of quotes removed."""
    values = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass
class Settings:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.values.get(key) or DEFAULTS.get(key, "")

    @property
    def token(self) -> str:
        return self.values.get(TOKEN_KEY, "").strip()

    @property
    def instance(self) -> str:
        """Names the Compose project and its volumes."""
        name = self.get("INATRACE_INSTANCE")
        if not _INSTANCE.match(name):
            raise SystemExit(f"INATRACE_INSTANCE={name} must be lower-case letters, digits, "
                             "'-' and '_'.")
        return name


def load(path: Path = USER_ENV) -> Settings:
    """The settings in `path` (none when it does not exist)."""
    if not path.is_file():
        return Settings()
    return Settings({k: v for k, v in parse(path.read_text()).items()
                     if k in DEFAULTS or k == TOKEN_KEY})
