#!/usr/bin/env python3
"""Provisions the dev container: brings $HOME and the repos to their expected state.

Runs as the devcontainer.json postCreateCommand; safe to re-run at any time:
.devcontainer/provision.py. The image carries system packages only. $HOME is a persistent volume, and what
lives there is installed or refreshed here: shell and git config,
~/.ssh/known_hosts, Node 14 (nvm), Claude Code, the Playwright browser and MCP
server — plus the cloned repos. Each step checks before acting, so a second
run changes nothing and finishes quickly.

It only runs in the dev container (INATRACE_DEVCONTAINER=1, set by the image):
anywhere else it would rewrite the user's own ~/.bashrc and git config. It
starts no services. The repos are cloned, and the report printed, by `inatrace`.
"""

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

from common import env, files, shell, ui

NVM_VERSION = "v0.40.8"
# inatrace-frontend (Angular 10) does not run on newer Node majors.
NODE_FRONTEND = "14"

HOME = Path.home()
CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
GIT_CONFIG = Path(os.environ.get("GIT_CONFIG_GLOBAL", HOME / ".gitconfig.local"))
NVM_DIR = Path(os.environ.get("NVM_DIR", HOME / ".nvm"))
# The agent relay (container-init in the Dockerfile) listens here.
SSH_AGENT_RELAY = "/tmp/ssh-agent.sock"


def in_devcontainer() -> bool:
    return os.environ.get("INATRACE_DEVCONTAINER") == "1"


def fix_home_ownership() -> None:
    """$HOME is a volume created with the uid/gid dev had at the time. If the host
    user's uid changed since (another machine, another account), give it back."""
    uid, gid = os.getuid(), os.getgid()
    st = HOME.stat()
    if (st.st_uid, st.st_gid) == (uid, gid):
        return
    ui.info(f"{HOME} belongs to {st.st_uid}:{st.st_gid}; handing it to {uid}:{gid}")
    # -xdev: stay on the volume; the read-only bind mounts inside it are skipped.
    shell.run(["sudo", "find", HOME, "-xdev", "(", "!", "-user", str(uid), "-o", "!",
               "-group", str(gid), ")", "-exec", "chown", "-h", f"{uid}:{gid}", "{}", "+"])


def configure_shell() -> None:
    """A managed block at the end of ~/.bashrc, rewritten on every run."""
    body = """\
        [ -f /usr/share/bash-completion/bash_completion ] && . /usr/share/bash-completion/bash_completion
        export HISTSIZE=100000 HISTFILESIZE=200000
        PROMPT_COMMAND="history -a${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        [ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"
        # Chromium for tools that look for Chrome, such as the frontend's Karma tests.
        export CHROME_BIN="$HOME/.local/bin/chrome"
        """
    rc = HOME / ".bashrc"
    text = rc.read_text() if rc.exists() else ""
    if files.write_if_changed(rc, files.with_block(text, textwrap.dedent(body))):
        ui.info("updated ~/.bashrc")


def configure_git(settings: env.Settings) -> None:
    """A writable global git config that includes the read-only host ~/.gitconfig.
    GitHub is reached over SSH through the host agent; the optional token only
    adds `gh` and an HTTPS credential helper."""
    host_cfg = str(HOME / ".gitconfig.host")
    GIT_CONFIG.touch()
    includes = shell.output(["git", "config", "--file", GIT_CONFIG, "--get-all", "include.path"],
                            check=False).splitlines()
    if host_cfg not in includes:
        shell.run(["git", "config", "--file", GIT_CONFIG, "--add", "include.path", host_cfg])
        ui.info("included the host git config")
    if settings.token:
        if not shell.ok(["gh", "auth", "status"]):
            shell.run(["gh", "auth", "login", "--with-token"], input=settings.token, quiet=True)
            ui.info("gh logged in with the token")
        shell.run(["gh", "auth", "setup-git"])


def configure_ssh() -> None:
    """known_hosts lives on the volume, so github.com is added once. Keys never
    are: they stay on the host, behind the agent.

    ~/.ssh/config gets a managed block, FIRST because ssh uses the first value it
    finds, pointing every ssh at the agent relay (container-init in the
    Dockerfile). IdentityAgent overrides SSH_AUTH_SOCK, which matters because
    IDEs inject their own forwarded agent socket into the processes they start
    (VS Code does, microsoft/vscode-remote-release#11413), and it may point at an
    agent without your keys."""
    ssh = HOME / ".ssh"
    ssh.mkdir(mode=0o700, exist_ok=True)
    known = ssh / "known_hosts"
    known.touch(mode=0o600)
    if not shell.ok(["ssh-keygen", "-F", "github.com", "-f", known]):
        keys = shell.output(["ssh-keyscan", "-t", "ed25519,ecdsa,rsa", "github.com"])
        if not keys:
            raise ui.StepError("ssh-keyscan github.com returned nothing")
        with known.open("a") as fh:
            fh.write(keys + "\n")
        ui.info("added github.com to ~/.ssh/known_hosts")

    cfg = ssh / "config"
    text = cfg.read_text() if cfg.exists() else ""
    body = f"Host *\n    IdentityAgent {SSH_AGENT_RELAY}"
    if files.write_if_changed(cfg, files.with_block(text, body, first=True), mode=0o600):
        ui.info("~/.ssh/config: every ssh uses the agent relay")


INATRACE = env.ROOT / "bin" / "inatrace"


def sync_repos() -> None:
    shell.run([INATRACE, "repos", "sync"])


# Runs under bash: nvm is a shell function. The system Node from the image stays
# the default; the frontend opts in with `nvm use 14`.
_NVM_SCRIPT = """\
set -e
export NVM_DIR="$1"
want_nvm="$2" node="$3"
current=""
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && current="v$(nvm --version)"
if [ "$current" != "$want_nvm" ]; then
  mkdir -p "$NVM_DIR"
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/$want_nvm/install.sh" \\
    | PROFILE=/dev/null bash >/dev/null 2>&1
  echo "    nvm ${current:-not installed} -> $want_nvm"
fi
. "$NVM_DIR/nvm.sh"
if ! nvm ls "$node" >/dev/null 2>&1; then
  nvm install "$node" >/dev/null 2>&1
  echo "    installed Node $node"
fi
[ "$(cat "$NVM_DIR/alias/default" 2>/dev/null)" = "system" ] || nvm alias default system >/dev/null
"""


def install_node() -> None:
    shell.run(["bash", "-c", _NVM_SCRIPT, "nvm", NVM_DIR, NVM_VERSION, NODE_FRONTEND])


def install_claude() -> None:
    """Claude Code (native installer, ~/.local), then defaults that are only SET IF
    ABSENT, so your own changes survive re-runs."""
    if not shell.have("claude"):
        shell.run(["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"], quiet=True)
        ui.info("installed Claude Code")
    CLAUDE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Claude keeps memory and sessions per working directory. The workspace moved
    # from /src to /workspace: copy them over once (the old ones stay).
    projects = CLAUDE_CONFIG_DIR / "projects"
    old, new = projects / "-src", projects / f"-{str(env.ROOT).strip('/').replace('/', '-')}"
    if old.is_dir() and not new.exists() and old != new:
        shutil.copytree(old, new, symlinks=True)
        ui.info(f"copied Claude's memory and sessions from {old.name} to {new.name}")
    # The container is the sandbox: Claude runs without permission prompts. Voice
    # input is on; it needs the (best-effort) microphone bridge.
    if files.json_defaults(CLAUDE_CONFIG_DIR / "settings.json", {
        "permissions": {"defaultMode": "bypassPermissions",
                        "skipDangerousModePermissionPrompt": True},
        "voice": {"enabled": True, "mode": "hold"},
    }):
        ui.info("updated settings.json")
    # Without the flag a fresh volume replays the onboarding wizard, which looks
    # like being asked to log in again. .claude.json holds the account: owner-only.
    if files.json_defaults(CLAUDE_CONFIG_DIR / ".claude.json",
                           {"hasCompletedOnboarding": True}, mode=0o600):
        ui.info("updated .claude.json")


def headless() -> bool:
    return os.environ.get("PLAYWRIGHT_HEADLESS", "true") == "true"


def setup_playwright() -> None:
    """The Chromium build matching the global Playwright in the image (a no-op when
    already installed), the MCP config, and its registration with Claude."""
    shell.run(["playwright", "install", "chromium"], quiet=True)

    # --no-sandbox: Chrome's sandbox needs privileges the container lacks.
    # --disable-dev-shm-usage: Docker's 64 MB /dev/shm is too small for Chromium.
    # Headed mode renders on the host's Wayland compositor (software only; the
    # D-Bus/DRM errors Chromium logs at startup are noise).
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if not headless():
        args += ["--ozone-platform=wayland", "--enable-features=UseOzonePlatform"]
    mcp_config = HOME / ".config" / "playwright-mcp.json"
    content = json.dumps({
        "browser": {
            "browserName": "chromium",
            "launchOptions": {"headless": headless(), "args": args},
            "contextOptions": {"viewport": {"width": 1280, "height": 820}},
        },
        "outputDir": str(HOME / ".cache" / "playwright-mcp" / "output"),
    }, indent=2) + "\n"
    if files.write_if_changed(mcp_config, content):
        ui.info(f"wrote {mcp_config} (headless: {str(headless()).lower()})")

    if not shell.ok(["claude", "mcp", "get", "playwright"]):
        shell.run(["claude", "mcp", "add", "-s", "user", "playwright", "--",
                   "playwright-mcp", "--config", mcp_config], quiet=True)
        ui.info("registered the playwright MCP server with Claude")

    setup_chrome_launcher()


# ~/.local/bin/chrome: the same Chromium for other tools, such as the frontend's
# Karma tests (CHROME_BIN in ~/.bashrc points here). It adds the container flags
# and, with a window, uses the host's Wayland: the X11 display VS Code forwards
# needs credentials Chromium does not have.
_CHROME_LAUNCHER = """\
#!/bin/sh
# Playwright's Chromium with the flags this container needs (managed by .devcontainer/provision.py).
chrome="$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux64/chrome 2>/dev/null | sort -V | tail -1)"
[ -n "$chrome" ] || { echo "Playwright's Chromium is missing; run .devcontainer/provision.py" >&2; exit 1; }
case " $* " in
  *" --headless"*) ;;
  *) [ -n "$WAYLAND_DISPLAY" ] && set -- --ozone-platform=wayland "$@" ;;
esac
exec "$chrome" --no-sandbox --disable-dev-shm-usage "$@"
"""


def setup_chrome_launcher() -> None:
    launcher = HOME / ".local" / "bin" / "chrome"
    if files.write_if_changed(launcher, _CHROME_LAUNCHER, mode=0o755):
        ui.info(f"wrote {launcher}")


def main() -> int:
    if not in_devcontainer():
        print("provision.py configures the dev container: it rewrites ~/.bashrc and the git",
              file=sys.stderr)
        print("config, and installs nvm, Claude Code and Playwright in $HOME. This does not",
              file=sys.stderr)
        print("look like the dev container (INATRACE_DEVCONTAINER is not set). Without the dev",
              file=sys.stderr)
        print("container, see docs/getting-started.md → On your machine.", file=sys.stderr)
        return 1
    settings = env.load()
    steps = ui.Steps()
    steps.run("home directory ownership", fix_home_ownership)
    steps.run("shell", configure_shell)
    steps.run("git", lambda: configure_git(settings))
    steps.run("ssh", configure_ssh)
    steps.run("repositories", sync_repos)
    steps.run(f"node {NODE_FRONTEND} (nvm)", install_node)
    steps.run("claude code", install_claude)
    steps.run("playwright mcp", setup_playwright)
    shell.run([INATRACE, "doctor"], check=False)
    steps.summary()
    # Never fail the container create: every step is recoverable by re-running.
    return 0


if __name__ == "__main__":
    sys.exit(main())
