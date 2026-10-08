#!/usr/bin/env python3
"""The devcontainer.json initializeCommand: runs on the HOST before the Compose
stack is created. Python 3.10+, standard library only.

It resolves every host path that compose.yaml bind-mounts, and writes them to
.devcontainer/.env. Anything optional the host lacks (a git config, an SSH
agent, a desktop session) is replaced by an empty placeholder under
.devcontainer/.state/, so:

  * the mount stays valid and the container starts without that feature,
    instead of failing;
  * Docker never creates a root-owned DIRECTORY in place of a missing source
    (which it does silently, shadowing the file or socket for good);
  * nothing is created in your home or runtime directory on the host.

It also keeps an EXISTING container in sync. `devcontainer up` reuses a
container that already exists (`compose up --no-recreate`), with the mounts and
ports it was created with. Sockets are mounted through stable directories, so
session changes never require that — but when a value in .devcontainer/.env
changes (the ports, or a host that gained a desktop session), the old container
is removed and this same `up` creates a fresh one. Nothing is lost: $HOME and
the nested Docker live in volumes, the repo is a bind mount, and provisioning is
idempotent.
"""

import os
import stat
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit("initialize.py needs Python 3.10 or newer.")

from common import env, shell  # noqa: E402


def note(message: str) -> None:
    print(f"initialize: {message}", file=sys.stderr, flush=True)


class Placeholders:
    def __init__(self, state: Path) -> None:
        self.state = state
        state.mkdir(parents=True, exist_ok=True)

    def file(self, name: str) -> Path:
        path = self.state / name
        path.touch(exist_ok=True)
        return path

    def dir(self, name: str) -> Path:
        path = self.state / f"{name}.d"
        path.mkdir(exist_ok=True)
        return path


def compose_env(settings: env.Settings, devcontainer_dir: Path,
                environ: dict[str, str], home: Path) -> dict[str, str]:
    """The values compose.yaml interpolates, with a note for every missing optional
    feature. `environ` and `home` are the host's."""
    ph = Placeholders(devcontainer_dir / ".state")

    # Git identity.
    gitconfig = home / ".gitconfig"
    if not gitconfig.is_file():
        gitconfig = ph.file("gitconfig")
        note("no ~/.gitconfig on the host — set user.name/user.email inside the container.")

    # SSH agent. The DIRECTORY is mounted, not the socket: OpenSSH >= 10.1 names
    # the socket ~/.ssh/agent/s.<random>, a new one every session. Agents with a
    # fixed socket in $XDG_RUNTIME_DIR (systemd units, GNOME, gpg-agent) come in
    # with the runtime dir below. Inside, a relay on SSH_AUTH_SOCK finds the live one.
    ssh_agent_dir = home / ".ssh" / "agent"
    if not ssh_agent_dir.is_dir():
        ssh_agent_dir = ph.dir("ssh-agent")
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    sock = environ.get("SSH_AUTH_SOCK", "")
    if not sock:
        note("no SSH agent (SSH_AUTH_SOCK) — the repos cannot be cloned over SSH. "
             "See docs/getting-started.md.")
    elif not (sock.startswith(f"{home}/.ssh/agent/") or (runtime and sock.startswith(f"{runtime}/"))):
        note(f"SSH_AUTH_SOCK={sock} is outside ~/.ssh/agent and $XDG_RUNTIME_DIR; "
             "the container will not see it. See docs/dev-container.md → SSH agent.")

    # Desktop session (best-effort). The whole runtime dir is mounted, so sockets
    # recreated by a new login (Wayland, PipeWire/PulseAudio, agents) show up in
    # the container without recreating it.
    if runtime and Path(runtime).is_dir():
        runtime_dir = Path(runtime)
        if not (runtime_dir / "pulse" / "native").is_socket():
            note("no PulseAudio/PipeWire socket — no microphone for Claude's /voice.")
    else:
        runtime_dir = ph.dir("runtime")
        note("no XDG_RUNTIME_DIR — no microphone, clipboard or headed browser.")

    wayland_display = (environ.get("WAYLAND_DISPLAY") or "wayland-0").rsplit("/", 1)[-1]
    headless = settings.get("PLAYWRIGHT_HEADLESS")
    if (runtime_dir / wayland_display).is_socket():
        headless = headless or "false"
    else:
        headless = headless or "true"
        note("no Wayland session — no clipboard paste; the Playwright browser runs headless.")

    # Names of their own: in Compose interpolation an exported shell variable wins
    # over .env, and the user's settings must not be overridden that way.
    return {
        "DC_INSTANCE": settings.instance,
        "HOST_GITCONFIG": str(gitconfig),
        "HOST_SSH_AGENT_DIR": str(ssh_agent_dir),
        "HOST_RUNTIME_DIR": str(runtime_dir),
        "HOST_WAYLAND_DISPLAY": wayland_display,
        "DC_PLAYWRIGHT_HEADLESS": headless,
        "DC_BIND_ADDRESS": settings.get("INATRACE_BIND_ADDRESS"),
        "DC_GATEWAY_PORT": settings.get("INATRACE_GATEWAY_PORT"),
        "DC_BACKEND_PORT": settings.get("INATRACE_BACKEND_PORT"),
        "DC_FRONTEND_PORT": settings.get("INATRACE_FRONTEND_PORT"),
    }


def render_env(values: dict[str, str]) -> str:
    lines = ["# Generated by .devcontainer/initialize.py — do not edit."]
    lines += [f"{key}={value}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def old_layout_notes(root: Path) -> list[str]:
    """Before /workspace, the repos were cloned into src/ and mounted alone at /src.
    Empty when no clone is left there."""
    old = root / "src"
    clones = [e for e in sorted(old.iterdir()) if (e / ".git").exists()] if old.is_dir() else []
    if not clones:
        return []
    notes = [f"clones in src/, from the old layout: the repos now live in repos/. "
             f"Move them, then run `up` again. In {root}:"]
    notes += [f"    mv src/{e.name} repos/" for e in clones]
    if (old / "tmp").is_dir():
        notes.append("    mv src/tmp tmp    # scratch files, git-ignored")
    notes.append("  and remove src/ once it holds nothing you need.")
    return notes


def mounts_workspace(container: str, root: Path) -> bool:
    """True when `container` bind-mounts `root` at /workspace."""
    mounts = shell.output(["docker", "inspect", container, "--format",
                           "{{range .Mounts}}{{.Source}}={{.Destination}}\n{{end}}"], check=False)
    return f"{root}=/workspace" in mounts.splitlines()


def main() -> int:
    # Stop before the container exists: provisioning would clone the repos again.
    old_layout = old_layout_notes(env.ROOT)
    for line in old_layout:
        note(line)
    if old_layout:
        return 1
    settings = env.load()
    if settings.token and env.USER_ENV.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
        note(".env holds a token but is readable by others — chmod 600 .env")

    values = compose_env(settings, env.DEVCONTAINER_DIR, dict(os.environ), Path.home())
    env_file = env.DEVCONTAINER_DIR / ".env"
    new = render_env(values)
    old = env_file.read_text() if env_file.exists() else None

    # Recreate the container if the host side changed, or if it was not created
    # from this checkout's layout (no record of it, or the repo not mounted at
    # /workspace: an older layout, another checkout): it may mount paths that no
    # longer exist.
    existing = shell.output(["docker", "ps", "-aq",
                             "--filter", f"label=com.docker.compose.project={values['DC_INSTANCE']}",
                             "--filter", "label=com.docker.compose.service=app"],
                            check=False).split()
    foreign = [c for c in existing if not mounts_workspace(c, env.ROOT)]
    if existing and (old != new or foreign):
        if old is None or foreign:
            note("found a container not created from this checkout's layout.")
        else:
            note("host paths or ports changed since the container was created:")
            before = set(old.splitlines())
            for line in new.splitlines():
                if line not in before and not line.startswith("#"):
                    print(f"    now: {line}", file=sys.stderr)
        note("removing the old container so this 'up' recreates it (volumes are kept).")
        shell.run(["docker", "rm", "-f", *existing], quiet=True)
    env_file.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
