"""`inatrace stack …`: the dev stack in dev-stack/compose.yaml.

The mode (INATRACE_MODE) and the images come from the root .env, passed to
Compose with --env-file. Unlike plain Compose, variables exported in the shell
do not override them: the .env is the only source of settings.
"""

import os
import subprocess
import sys

from . import config, paths, shell

MODES = ("fullstack-dev", "back-dev", "front-dev", "images")
DEFAULT_MODE = "fullstack-dev"
# Before dev-stack/ the gateway ran alone as this Compose project, on port 8000.
OLD_GATEWAY_PROJECT = "inatrace-gateway"


def mode(settings: config.Settings, override: str | None = None) -> str:
    chosen = override or settings.values.get("INATRACE_MODE") or DEFAULT_MODE
    if chosen not in MODES:
        raise SystemExit(f"inatrace stack: unknown mode '{chosen}' — one of {', '.join(MODES)}.")
    return chosen


def compose_env(chosen_mode: str | None) -> dict[str, str]:
    """The environment for `docker compose`: no INATRACE_* from the shell, and the
    mode as the Compose profile ('*' selects every service)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INATRACE_") and k != "COMPOSE_PROFILES"}
    if chosen_mode:
        env["COMPOSE_PROFILES"] = chosen_mode
    return env


def compose_cmd(*args: str) -> list[str]:
    cmd = ["docker", "compose"]
    if paths.USER_ENV.is_file():
        cmd += ["--env-file", str(paths.USER_ENV)]
    return cmd + ["-f", str(paths.DEV_STACK_COMPOSE), *args]


def _compose(chosen_mode: str | None, *args: str) -> int:
    return subprocess.run(compose_cmd(*args), env=compose_env(chosen_mode)).returncode


def _remove_old_gateway() -> None:
    old = shell.output(["docker", "ps", "-aq", "--filter",
                        f"label=com.docker.compose.project={OLD_GATEWAY_PROJECT}"], check=False).split()
    if old:
        print(f"inatrace stack: removing the old gateway ({OLD_GATEWAY_PROJECT}); "
              "the dev stack replaces it.", file=sys.stderr)
        shell.run(["docker", "rm", "-f", *old], quiet=True)


def _warn_old_mysql() -> None:
    # Older setups started MySQL with `docker run --name inatrace-mysql`.
    if shell.output(["docker", "ps", "-aq", "--filter", "name=^inatrace-mysql$"], check=False):
        print("inatrace stack: the old inatrace-mysql container holds port 3306; move its data "
              "to the stack's MySQL (docs/dev-stack.md → Troubleshooting) and remove it.", file=sys.stderr)


def up(override: str | None, pull: bool) -> int:
    settings = config.load(paths.USER_ENV)
    chosen = mode(settings, override)
    _remove_old_gateway()
    _warn_old_mysql()
    print(f"inatrace stack: mode {chosen}", flush=True)
    # Services of the other modes go away: --remove-orphans does not cover
    # profiles, so stop what this mode does not run.
    others = {"backend": chosen in ("front-dev", "images"),
              "frontend": chosen in ("back-dev", "images")}
    stale = [s for s, wanted in others.items() if not wanted]
    if stale:
        _compose("*", "rm", "--stop", "--force", *stale)
    args = ["up", "-d"] + (["--pull", "always"] if pull else [])
    return _compose(chosen, *args)


def down(volumes: bool) -> int:
    return _compose("*", "down", *(["--volumes"] if volumes else []))


def status() -> int:
    return _compose("*", "ps")


def logs(services: list[str], follow: bool) -> int:
    return _compose("*", "logs", *(["--follow"] if follow else []), *services)
