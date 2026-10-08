"""The dev stack as it is running: what comes from an image, and how to reach it.

Nothing here starts or stops the stack (that is `inatrace stack up`); the
smoke tests run against whatever mode it was started in.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

PROJECT = "inatrace-dev-stack"
# The gateway listens on 8000 wherever the stack runs (dev-stack/gateway.conf).
GATEWAY = "http://127.0.0.1:8000"
log = logging.getLogger("smoke")


def run(*cmd: str, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=text, check=check)


def now() -> str:
    """A timestamp usable with `docker logs --since`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Image:
    name: str

    @property
    def config(self) -> dict:
        return json.loads(run("docker", "image", "inspect", self.name).stdout)[0]["Config"]

    @property
    def tag(self) -> str:
        return self.name.rsplit(":", 1)[-1] if "@" not in self.name else ""

    def label(self, key: str) -> str:
        return (self.config.get("Labels") or {}).get(f"org.opencontainers.image.{key}", "")

    def shell(self, script: str) -> subprocess.CompletedProcess:
        """Runs a shell script in a throwaway container of the image."""
        log.info("in %s: %s", self.name, script)
        return run("docker", "run", "--rm", "--entrypoint", "sh", self.name, "-c", script, check=False)


@dataclass
class Service:
    """A dev stack service running from its image (the backend or the frontend)."""

    name: str
    container: str

    @property
    def image(self) -> Image:
        return Image(self.inspect("{{.Config.Image}}"))

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
        return run("docker", "inspect", "-f", template, self.container).stdout.strip()

    def exec(self, *cmd: str, text: bool = True) -> subprocess.CompletedProcess:
        return run("docker", "exec", self.container, *cmd, check=False, text=text)

    def stored_file(self, storage_key: str) -> bytes:
        """An uploaded file read straight from the backend image's storage volume."""
        path = self.exec("find", "/data/storage", "-name", storage_key, "-type", "f").stdout.strip()
        return self.exec("cat", path, text=False).stdout if path else b""

    def logs(self, since: str | None = None) -> str:
        result = run("docker", "logs", *(["--since", since] if since else []), self.container,
                     check=False)
        return result.stdout + result.stderr

    def stop(self, timeout: int) -> tuple[float, str]:
        """Seconds it took to stop on SIGTERM, and its exit code."""
        log.info("stopping %s (SIGTERM, %ss before SIGKILL)", self.name, timeout)
        start = time.monotonic()
        run("docker", "stop", "-t", str(timeout), self.container)
        return time.monotonic() - start, self.inspect("{{.State.ExitCode}}")

    def start(self) -> None:
        log.info("starting %s", self.name)
        run("docker", "start", self.container)


class DevStack:
    base_url = GATEWAY

    def _container(self, service: str) -> str:
        return run("docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
                   "--filter", f"label=com.docker.compose.service={service}").stdout.strip()

    def service(self, name: str) -> Service | None:
        """The service when it runs from its image; None when it runs from a checkout."""
        container = self._container(name)
        return Service(name, container) if container else None

    def check(self) -> None:
        missing = [s for s in ("gateway", "mysql", "mail") if not self._container(s)]
        if missing:
            raise RuntimeError(f"the dev stack is not running ({', '.join(missing)} missing): "
                               "start it with `inatrace stack up`")

    @property
    def mode(self) -> str:
        backend, frontend = self.service("backend"), self.service("frontend")
        return {(False, False): "fullstack-dev", (False, True): "back-dev",
                (True, False): "front-dev", (True, True): "images"}[(bool(backend), bool(frontend))]

    def wait(self, what: str, path: str, status: int, timeout: int) -> float:
        """Seconds until GET `path` through the gateway answers `status`."""
        start = time.monotonic()
        log.info("waiting for the %s (timeout %ss)", what, timeout)
        while time.monotonic() - start < timeout:
            try:
                if requests.get(f"{self.base_url}{path}", timeout=5).status_code == status:
                    return time.monotonic() - start
            except requests.RequestException:
                pass
            time.sleep(2)
        raise TimeoutError(f"the {what} did not answer through the gateway within {timeout}s")

    def wait_backend(self, timeout: int) -> float:
        # An anonymous request to a protected endpoint: 401 once the backend is up.
        return self.wait("backend", "/api/user/profile", 401, timeout)

    def wait_frontend(self, timeout: int) -> float:
        return self.wait("frontend", "/", 200, timeout)

    def login(self, email: str, password: str) -> requests.Session:
        session = requests.Session()
        session.post(f"{self.base_url}/api/user/login",
                     json={"username": email, "password": password}, timeout=10)
        return session

    def sql(self, query: str) -> str:
        log.info("SQL: %s", query)
        result = run("docker", "exec", self._container("mysql"), "mysql", "-N", "-uinatrace",
                     "-pinatrace", "inatrace", "-e", query)
        return result.stdout.strip()
