"""The optional monitoring (INATRACE_MONITORING=on): Beszel's hub and agent on the server
(deploy/server/compose.yaml, profile monitoring), set up by `up`, and `deploy dashboard`,
which opens the hub through ssh. Nothing of it is published: the hub listens on the
server's 127.0.0.1 only."""

import json
import shlex
import subprocess
import urllib.parse

from .. import ui
from .common import DeployError
from .instance import Instance, load, new_secret, save, server_files
from .remote import MONITORING, REMOTE_DIR, Remote, deployed

PORT = 8090  # the hub's, on the server's 127.0.0.1
USER = "admin@inatrace.local"  # the hub's one user (compose.yaml)
AGENT = "/beszel_socket/beszel.sock"  # where the hub reaches its agent


def settle(remote: Remote, instance: Instance) -> bool:
    """.env as `up` needs it: COMPOSE_PROFILES from INATRACE_MONITORING, so every Compose
    command there (by hand too) sees the monitoring or not; with it, its user's password
    (an instance from before the monitoring has none) and the server's docker group,
    which lets the agent read Docker. Whether .env changed."""
    before = dict(instance.values)
    instance.values["COMPOSE_PROFILES"] = "monitoring" if instance.monitoring else ""
    if not instance.monitoring:
        return _keep(instance, before)
    if not instance.get("INATRACE_MONITORING_PASSWORD"):
        instance.values["INATRACE_MONITORING_PASSWORD"] = new_secret()
    gid = remote.output("getent group docker | cut -d: -f3")
    if not gid or not gid.isdigit():
        raise DeployError(f"no docker group on {remote.destination}: the monitoring's agent reads Docker through it")
    instance.values["INATRACE_DOCKER_GID"] = gid
    return _keep(instance, before)


def _keep(instance: Instance, before: dict[str, str]) -> bool:
    if instance.values == before:
        return False
    save(instance)
    return True


def _api(remote: Remote, method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    """A call to the hub's API, from inside Caddy's container (it has curl), over the
    Compose network; the body goes on stdin, so no password shows in a process list."""
    command = (f"docker compose exec -T caddy curl -s -X {method} -H 'Content-Type: application/json'"
               + (f" -H {shlex.quote('Authorization: ' + token)}" if token else "")
               + (" --data-binary @-" if body is not None else "")
               + " " + shlex.quote(f"http://beszel:{PORT}{path}"))
    result = remote.quiet(command, json.dumps(body) if body is not None else None)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise DeployError(f"the monitoring's hub did not answer {path}: {result.stdout.strip()[:200]}") from None


def _login(remote: Remote, instance: Instance) -> tuple[str, str]:
    """(token, user id) of the hub's one user."""
    answer = _api(remote, "POST", "/api/collections/users/auth-with-password",
                  {"identity": USER, "password": instance.get("INATRACE_MONITORING_PASSWORD")})
    if "token" not in answer:
        raise DeployError(f"could not log in to the monitoring's hub: {answer.get('message', answer)}")
    return answer["token"], answer["record"]["id"]


def pair(remote: Remote, instance: Instance) -> bool:
    """Gives the agent the hub's key, once the hub runs: kept in .env, which `up` then
    sends again. Whether it changed (the agent must start anew)."""
    token, _ = _login(remote, instance)
    key = _api(remote, "GET", "/api/beszel/getkey", token=token).get("key", "")
    if not key:
        raise DeployError("the monitoring's hub gave no key")
    if instance.get("INATRACE_MONITORING_KEY") == key:
        return False
    instance.values["INATRACE_MONITORING_KEY"] = key
    save(instance)
    remote.sync(server_files(instance))
    return True


def register(remote: Remote, instance: Instance) -> None:
    """The server as the hub's one system, reached through the agent's socket; safe to
    repeat."""
    token, user = _login(remote, instance)
    query = urllib.parse.urlencode({"filter": f"host='{AGENT}'"})
    found = _api(remote, "GET", f"/api/collections/systems/records?{query}", token=token)
    if found.get("totalItems"):
        return
    answer = _api(remote, "POST", "/api/collections/systems/records",
                  {"name": instance.get("INATRACE_SITE") or instance.name, "host": AGENT, "port": "45876",
                   "users": [user]}, token=token)
    if "id" not in answer:
        raise DeployError(f"could not add the server to the monitoring: {answer.get('message', answer)}")


def present(remote: Remote) -> bool:
    """Whether the monitoring's containers exist there (running or not)."""
    return bool(remote.output(f"cd ~/{REMOTE_DIR} && docker compose --profile monitoring ps -a -q "
                              f"{' '.join(MONITORING)} 2>/dev/null"))


def dashboard(name: str, port: int) -> int:
    """Forwards a local port to the hub, through ssh, until Ctrl+C."""
    instance = load(name)
    remote = deployed(instance)
    if not instance.monitoring:
        raise DeployError(f"{name} has no monitoring: INATRACE_MONITORING=on in its .env (or init again), "
                          f"then inatrace deploy up {name}")
    if not remote.output(f"cd ~/{REMOTE_DIR} && docker compose ps -q --status running beszel"):
        raise DeployError(f"the monitoring is not running on {remote.destination}: inatrace deploy up {name}")
    url = f"http://localhost:{port}"
    def render() -> None:
        ui.ok(f"the dashboard of {name} is at {url}")
        ui.info("open it in your browser; Ctrl+C closes it")

    ui.show("dashboard", {"url": url}, render)
    # Its own connection, not the shared one: the forward lives as long as this command.
    tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                               "-L", f"127.0.0.1:{port}:127.0.0.1:{PORT}", remote.destination],
                              stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        code = tunnel.wait()
    except KeyboardInterrupt:
        tunnel.terminate()
        tunnel.wait(timeout=5)
        ui.blank()
        ui.info("closed")
        return 0
    why = tunnel.stderr.read().strip()
    hint = f"; is port {port} in use here? -p picks another" if "forward" in why.lower() else ""
    raise DeployError(f"the tunnel closed (exit {code}): {why or 'no reason given'}{hint}")
