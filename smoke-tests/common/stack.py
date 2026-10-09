"""What the smoke tests run against: the dev stack, or a deployment (docs/deploy.md).

The tests see both the same way: HTTP and a browser from outside, at its address, and
docker for what only shows from inside (the images, the database, the logs), run here
for the dev stack and on the server over ssh for a deployment.

Nothing here starts or stops anything (that is `inatrace stack up`, `inatrace deploy
up`); the tests run against whatever is running.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

import requests

# The gateway listens on 8000 wherever the dev stack runs (dev-stack/gateway.conf).
GATEWAY = "http://127.0.0.1:8000"
log = logging.getLogger("smoke")


class Host:
    """Where docker runs: here, or a server over ssh (one shared connection)."""

    def __init__(self, ssh: str | None = None) -> None:
        self.ssh = ssh
        if ssh:
            self._sockets = tempfile.mkdtemp(prefix="smoke-ssh-")
            atexit.register(shutil.rmtree, self._sockets, True)

    def run(self, *cmd: str, check: bool = True, text: bool = True,
            input: str | None = None) -> subprocess.CompletedProcess:
        if self.ssh:
            cmd = ("ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto",
                   "-o", f"ControlPath={self._sockets}/%C", "-o", "ControlPersist=60",
                   self.ssh, shlex.join(cmd))
        return subprocess.run(cmd, capture_output=True, text=text, check=check, input=input,
                              stdin=None if input is not None else subprocess.DEVNULL)

    def now(self) -> str:
        """Its clock, as `docker logs --since` takes it."""
        return self.run("date", "-u", "+%Y-%m-%dT%H:%M:%SZ").stdout.strip()


@dataclass
class Image:
    host: Host
    name: str

    @property
    def config(self) -> dict:
        return json.loads(self.host.run("docker", "image", "inspect", self.name).stdout)[0]["Config"]

    @property
    def tag(self) -> str:
        return self.name.rsplit(":", 1)[-1] if "@" not in self.name else ""

    def label(self, key: str) -> str:
        return (self.config.get("Labels") or {}).get(f"org.opencontainers.image.{key}", "")

    def shell(self, script: str) -> subprocess.CompletedProcess:
        """Runs a shell script in a throwaway container of the image."""
        log.info("in %s: %s", self.name, script)
        return self.host.run("docker", "run", "--rm", "--entrypoint", "sh", self.name, "-c", script,
                             check=False)


@dataclass
class Service:
    """A service running from its image (the backend or the frontend)."""

    host: Host
    name: str
    container: str

    @property
    def image(self) -> Image:
        return Image(self.host, self.inspect("{{.Config.Image}}"))

    @property
    def expected_version(self) -> str | None:
        """The image tag, when it is a version (2.40.1, 2.40.1-rc.1); `latest` and the
        like say nothing."""
        tag = self.image.tag
        return tag if re.match(r"^\d+\.\d+\.\d+", tag) else None

    @property
    def env(self) -> dict[str, str]:
        pairs = json.loads(self.inspect("{{json .Config.Env}}"))
        return dict(pair.split("=", 1) for pair in pairs)

    def inspect(self, template: str) -> str:
        return self.host.run("docker", "inspect", "-f", template, self.container).stdout.strip()

    def exec(self, *cmd: str, text: bool = True) -> subprocess.CompletedProcess:
        return self.host.run("docker", "exec", self.container, *cmd, check=False, text=text)

    def stored_file(self, storage_key: str) -> bytes:
        """An uploaded file read straight from the backend image's storage volume."""
        path = self.exec("find", "/data/storage", "-name", storage_key, "-type", "f").stdout.strip()
        return self.exec("cat", path, text=False).stdout if path else b""

    def logs(self, since: str | None = None) -> str:
        result = self.host.run("docker", "logs", *(["--since", since] if since else []), self.container,
                               check=False)
        return result.stdout + result.stderr

    def stop(self, timeout: int) -> tuple[float, str]:
        """Seconds it took to stop on SIGTERM, and its exit code."""
        log.info("stopping %s (SIGTERM, %ss before SIGKILL)", self.name, timeout)
        start = time.monotonic()
        self.host.run("docker", "stop", "-t", str(timeout), self.container)
        return time.monotonic() - start, self.inspect("{{.State.ExitCode}}")

    def start(self) -> None:
        log.info("starting %s", self.name)
        self.host.run("docker", "start", self.container)


class Stack:
    """A running INATrace: its address, its Compose project, and where its docker runs."""

    name = ""         # for the report
    project = ""      # the Compose project
    needs: tuple[str, ...] = ()  # the services that must run
    writes = True     # whether tests that create data may run against it
    swagger = True    # whether the API description is open

    def __init__(self, host: Host, base_url: str, *, verify: bool = True,
                 headers: dict[str, str] | None = None) -> None:
        self.host, self.base_url = host, base_url.rstrip("/")
        self.verify, self.headers = verify, headers or {}
        self.http = self.new_session()

    def new_session(self) -> requests.Session:
        """HTTP to it as a browser would reach it (its certificate, its headers)."""
        session = requests.Session()
        session.verify = self.verify
        session.headers.update(self.headers)
        return session

    def browser_context(self, browser):
        """A Playwright context that reaches it as people do: the same certificate, and its
        headers on its own requests only (as a CDN adds them), not on a font's."""
        context = browser.new_context(ignore_https_errors=not self.verify)
        if self.headers:
            context.route(f"{self.base_url}/**", lambda route: route.continue_(
                headers={**route.request.headers, **self.headers}))
        return context

    def _container(self, service: str) -> str:
        return self.host.run("docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={self.project}",
                             "--filter", f"label=com.docker.compose.service={service}").stdout.strip()

    def service(self, name: str) -> Service | None:
        """The service when it runs from its image; None when it does not run so."""
        container = self._container(name)
        return Service(self.host, name, container) if container else None

    def check(self) -> None:
        missing = [s for s in self.needs if not self._container(s)]
        if missing:
            raise RuntimeError(f"{self.name} is not running ({', '.join(missing)} missing)")

    def now(self) -> str:
        return self.host.now()

    def wait(self, what: str, path: str, status: int, timeout: int) -> float:
        """Seconds until GET `path` answers `status`."""
        start = time.monotonic()
        log.info("waiting for the %s (timeout %ss)", what, timeout)
        while time.monotonic() - start < timeout:
            try:
                if self.http.get(f"{self.base_url}{path}", timeout=5).status_code == status:
                    return time.monotonic() - start
            except requests.RequestException:
                pass
            time.sleep(2)
        raise TimeoutError(f"the {what} did not answer at {self.base_url} within {timeout}s")

    def wait_backend(self, timeout: int) -> float:
        # An anonymous request to a protected endpoint: 401 once the backend is up.
        return self.wait("backend", "/api/user/profile", 401, timeout)

    def wait_frontend(self, timeout: int) -> float:
        return self.wait("frontend", "/", 200, timeout)

    def login(self, email: str, password: str) -> requests.Session:
        session = self.new_session()
        session.post(f"{self.base_url}/api/user/login",
                     json={"username": email, "password": password}, timeout=10)
        return session

    def sql(self, query: str) -> str:
        """A query as the application's database user (the container knows it). Against a
        stack the tests may not write to, in a read-only session: a write fails."""
        log.info("SQL: %s", query)
        if not self.writes:
            query = f"SET SESSION TRANSACTION READ ONLY; {query}"
        result = self.host.run("docker", "exec", "-i", self._container("mysql"), "sh", "-c",
                               'exec mysql -N -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" 2>/dev/null',
                               input=query)
        return result.stdout.strip()


class DevStack(Stack):
    """`inatrace stack up`, in whatever mode: what runs from a checkout has no container."""

    project = "inatrace-dev-stack"
    needs = ("gateway", "mysql", "mail")

    def __init__(self) -> None:
        super().__init__(Host(), GATEWAY)

    def check(self) -> None:
        try:
            super().check()
        except RuntimeError as error:
            raise RuntimeError(f"{error}: start it with `inatrace stack up`") from None

    @property
    def mode(self) -> str:
        backend, frontend = self.service("backend"), self.service("frontend")
        return {(False, False): "fullstack-dev", (False, True): "back-dev",
                (True, False): "front-dev", (True, True): "images"}[(bool(backend), bool(frontend))]

    @property
    def name(self) -> str:
        return f"dev stack in {self.mode} mode"


class Deployment(Stack):
    """A server `inatrace deploy up` set up: reached at its address, and over ssh. Only
    read: tests that create data do not run against it, and its database is read only."""

    project = "inatrace"
    needs = ("caddy", "frontend", "backend", "mysql")
    writes = False

    def __init__(self, spec: dict) -> None:
        super().__init__(Host(spec["ssh"]), spec["url"], verify=spec.get("verify", True),
                         headers=spec.get("headers"))
        self.name = f"deployment {spec['name']}"
        self.swagger = spec.get("swagger", False)
        self.spec = spec


def target() -> Stack:
    """What `inatrace smoke` or `inatrace deploy smoke` asked for: SMOKE_DEPLOYMENT holds a
    deployment's details as JSON; without it, the dev stack."""
    spec = os.environ.get("SMOKE_DEPLOYMENT")
    return Deployment(json.loads(spec)) if spec else DevStack()
