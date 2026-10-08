"""The repo root's .env: every setting the user may change, in one file.

KEY=VALUE lines, see .env.example. Parsed, never sourced, and the only source
of these settings: variables exported in the shell are ignored. The dev
container reads its own keys too (.devcontainer/common/env.py).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .repos import Repo, fork_key

# Dev container: read by .devcontainer/ (the gateway port by the dev stack too).
# Known here only so they are not reported as unknown.
DEVCONTAINER_KEYS = {
    "INATRACE_INSTANCE", "INATRACE_BIND_ADDRESS", "INATRACE_GATEWAY_PORT",
    "INATRACE_BACKEND_PORT", "INATRACE_FRONTEND_PORT", "PLAYWRIGHT_HEADLESS",
}
# Dev stack: read by `inatrace stack`, passed to Compose.
STACK_KEYS = {
    "INATRACE_MODE",
    "INATRACE_BACKEND_IMAGE", "INATRACE_BACKEND_VERSION",
    "INATRACE_FRONTEND_IMAGE", "INATRACE_FRONTEND_VERSION",
}
# GitHub: the optional token, read by .devcontainer/provision.py.
TOKEN_KEY = "INATRACE_GH_TOKEN"
FORK_PREFIX = "INATRACE_FORK_"
_FORK_VALUE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


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
    warnings: list[str] = field(default_factory=list)

    def forks(self, repos: list[Repo]) -> dict[str, str]:
        """Repo name -> fork (owner/name), for the INATRACE_FORK_<REPO> lines that
        name a repo in repos.txt and a valid owner/name. The others are warned about."""
        by_key = {fork_key(repo.name): repo.name for repo in repos}
        forks = {}
        for key, value in self.values.items():
            if not key.startswith(FORK_PREFIX):
                continue
            if key not in by_key:
                self.warnings.append(f"{key} matches no repo in repos/repos.txt — ignored.")
            elif not _FORK_VALUE.match(value):
                self.warnings.append(f"{key}={value} is not owner/name — ignored.")
            else:
                forks[by_key[key]] = value
        return forks


def load(path: Path) -> Settings:
    """The settings in `path` (none when it does not exist). Unknown keys are
    kept out and reported in `warnings`."""
    settings = Settings()
    if not path.is_file():
        return settings
    for key, value in parse(path.read_text()).items():
        if (key in DEVCONTAINER_KEYS or key in STACK_KEYS or key == TOKEN_KEY
                or key.startswith(FORK_PREFIX)):
            settings.values[key] = value
        else:
            settings.warnings.append(f"unknown setting '{key}' — ignored (see .env.example).")
    return settings
