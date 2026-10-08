"""`inatrace doctor`: what the environment provides right now.

The same checks with or without the dev container; inside it, also the extras
it bridges from the host (agent, token, audio, Wayland).
"""

import os
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import shell

NVM_DIR = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
# inatrace-frontend (Angular 10) does not run on newer Node majors.
NODE_FRONTEND = "14"
# The dev container's agent relay (container-init in .devcontainer/Dockerfile).
SSH_AGENT_RELAY = "/tmp/ssh-agent.sock"


def in_devcontainer() -> bool:
    return os.environ.get("INATRACE_DEVCONTAINER") == "1"


def gateway_running() -> bool:
    from .stack import compose_cmd
    return bool(shell.output(compose_cmd("ps", "--status", "running", "-q", "gateway"),
                             check=False))


def ssh_agent_usable() -> bool:
    """Asks the relay every ssh is pointed at (IdentityAgent), not SSH_AUTH_SOCK:
    IDEs inject their own forwarded socket there, which may not even exist."""
    # ssh-add -l: 0 = keys listed, 1 = agent reachable but empty, 2 = no agent.
    try:
        result = shell.run(["env", f"SSH_AUTH_SOCK={SSH_AGENT_RELAY}", "ssh-add", "-l"],
                           quiet=True, check=False)
    except OSError:
        return False
    return result.returncode in (0, 1)


def node_frontend_available() -> bool:
    """Node 14 through nvm (the dev container's way), or as the `node` on the PATH."""
    if shell.ok(["bash", "-c", '. "$1/nvm.sh" && nvm ls "$2"', "nvm", NVM_DIR, NODE_FRONTEND]):
        return True
    return (shell.have("node")
            and shell.output(["node", "--version"], check=False).startswith(f"v{NODE_FRONTEND}."))


def _wayland() -> bool:
    display = os.environ.get("WAYLAND_DISPLAY", "")
    return bool(display) and Path(display).is_socket()


def checks() -> list[tuple[str, bool]]:
    results = [
        ("docker", shell.ok(["docker", "info"])),
        ("gateway", gateway_running()),
        ("java 17", shell.ok(["java", "-version"])),
        ("maven", shell.ok(["mvn", "-v"])),
        (f"node {NODE_FRONTEND}", node_frontend_available()),
    ]
    if in_devcontainer():
        results += [
            ("claude", shell.ok(["claude", "--version"])),
            ("ssh agent", ssh_agent_usable()),
            ("gh (token)", shell.ok(["gh", "auth", "status"])),
            ("audio", shell.ok(["pactl", "info"])),
            ("wayland", _wayland()),
        ]
    return results


def report() -> int:
    results = checks()
    table = Table(title="environment", title_justify="left", show_header=False, box=None,
                  padding=(0, 2))
    for label, ok in results:
        table.add_row(label, "[green]OK[/]" if ok else "[yellow]UNAVAILABLE[/]")
    if in_devcontainer():
        headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true") == "true"
        table.add_row("browser", "headless" if headless else "headed")
    console = Console()
    console.print()
    console.print(table)
    console.print()
    if in_devcontainer():
        console.print("UNAVAILABLE is expected for optional features the host does not provide "
                      "(token, agent, audio, wayland). See docs/dev-container.md.")
    else:
        console.print("Java 17 and Maven are needed for the backend, Node 14 for the frontend, "
                      "only in the modes that run them from your checkout. See docs/getting-started.md.")
    if not dict(results)["gateway"]:
        console.print("Start the dev stack (gateway, MySQL, Mailpit) with: inatrace stack up")
    return 0
