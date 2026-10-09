"""The server, over ssh: commands, the file sync (rsync), Compose's plan, what is installed
(the probe), installing it (deploy/prereqs/), waiting for a container to be healthy."""

import atexit
import os
import re
import select
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths, ui
from .common import DeployError
from .instance import LOCAL_PROPERTIES, Instance, is_ip, problems

REMOTE_DIR = "inatrace"


# deploy/prereqs/<profile>.sh, and the distributions (/etc/os-release ID or ID_LIKE) it fits.
PREREQ_PROFILES = {"debian": ("debian", "ubuntu"), "rhel": ("rhel", "centos", "rocky", "almalinux")}


BUSY = ("the server's package manager is busy (a server just created often updates itself "
        "for a few minutes)")


# A stage the prereqs scripts report: `    · what it does`.
_STAGE = re.compile(r"^\s*· (.+)\n$")


class Remote:
    """Commands on the server, over one shared ssh connection."""

    def __init__(self, destination: str) -> None:
        self.destination = destination
        self._sockets = tempfile.mkdtemp(prefix="inatrace-ssh-")
        atexit.register(shutil.rmtree, self._sockets, True)

    def _ssh(self, *options: str) -> list[str]:
        return ["ssh", "-o", "ControlMaster=auto", "-o", f"ControlPath={self._sockets}/%C",
                "-o", "ControlPersist=60", *options, self.destination]

    def run(self, command: str, *, check: bool = True, tty: bool = False) -> int:
        """Runs `command` in ~/inatrace (a shell line), output to the terminal."""
        line = f"cd ~/{REMOTE_DIR} && {command}"
        code = subprocess.run(self._ssh(*(["-t"] if tty else [])) + [line],
                              stdin=None if tty else subprocess.DEVNULL).returncode
        if check and code:
            raise DeployError(f"on {self.destination}: `{command}` failed (exit {code})")
        return code

    def quiet(self, command: str, input: str | None = None) -> subprocess.CompletedProcess:
        """Runs `command` in ~/inatrace, keeping what it prints (stdout and stderr, in
        order) for when it fails, instead of mixing it with ours."""
        return subprocess.run(self._ssh() + [f"cd ~/{REMOTE_DIR} && {command}"], input=input,
                              stdin=None if input is not None else subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def must(self, command: str, what: str) -> str:
        """`quiet`, raising with the tail of its output when it fails."""
        result = self.quiet(command)
        if result.returncode:
            tail = "\n".join(result.stdout.strip().splitlines()[-15:])
            raise DeployError(f"{what} failed on {self.destination}:\n{tail}")
        return result.stdout

    def probe(self) -> "Probe":
        """What is there, read only, in one login."""
        found = self.output('. /etc/os-release; echo "${PRETTY_NAME:-$ID}"; echo "$ID ${ID_LIKE:-}"; '
                            "docker info --format '{{.ServerVersion}}' >/dev/null 2>&1 "
                            "&& docker compose version >/dev/null 2>&1 && command -v rsync >/dev/null "
                            "&& command -v crontab >/dev/null "
                            "&& echo ready || echo missing; "
                            # Whether apt's or dpkg's locks are held (a server just created often
                            # updates itself): /proc/locks lists every lock by inode, readable
                            # without root, unlike the lock files.
                            "held=idle; for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock "
                            "/var/lib/apt/lists/lock /var/cache/apt/archives/lock; do "
                            "i=$(stat -c %i \"$f\" 2>/dev/null) && "
                            "awk -v i=\":$i\" '$6 ~ i\"$\"' /proc/locks | grep -q . && held=busy; done; "
                            # dnf takes no such lock: whether one runs.
                            "{ pgrep -x dnf || pgrep -x dnf5; } >/dev/null 2>&1 && held=busy; "
                            "echo $held")
        if found is None:
            return Probe(reached=False)
        lines = found.splitlines() + ["", "", "", ""]
        return Probe(True, lines[0], lines[1], lines[2] == "ready", lines[3] == "busy")

    def script(self, path: Path, on_output, *, tty: bool, minutes: int = 30) -> int:
        """Runs a local shell script there, `tty` when sudo will ask a password: what it
        prints goes to `on_output`, a line at a time, or as soon as it stops for a moment
        mid-line (a prompt). Without a terminal, Ctrl+C reaches this process, which then
        closes the connection; either way it is stopped after `minutes`."""
        target = f"/tmp/inatrace-{path.name}"
        copied = subprocess.run(self._ssh() + [f"cat > {target}"], input=path.read_bytes())
        if copied.returncode:
            return copied.returncode
        process = subprocess.Popen(self._ssh("-q", *(["-t"] if tty else []))
                                   + [f"sh {target}; code=$?; rm -f {target}; exit $code"],
                                   stdin=None if tty else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            return self._stream(process, on_output, minutes)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise

    @staticmethod
    def _stream(process: subprocess.Popen, on_output, minutes: int) -> int:
        fd, pending, quiet_since = process.stdout.fileno(), b"", time.monotonic()
        deadline = time.monotonic() + minutes * 60
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                data = os.read(fd, 4096)
                if not data:
                    break
                # A terminal's line ends are \r\n, and sudo's sends a few NUL bytes.
                pending += data.replace(b"\r\n", b"\n").replace(b"\x00", b"")
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    on_output(line.decode(errors="replace") + "\n")
                quiet_since = time.monotonic()
            elif pending and time.monotonic() - quiet_since > 0.3:
                on_output(pending.decode(errors="replace"))
                pending = b""
        else:
            process.kill()
            on_output(f"\n(stopped after {minutes} minutes)\n")
        if pending:
            on_output(pending.decode(errors="replace"))
        return process.wait()

    def lines(self, command: str, on_line) -> int:
        """Runs `command` in ~/inatrace, handing each line it prints to `on_line`."""
        process = subprocess.Popen(self._ssh("-q") + [f"cd ~/{REMOTE_DIR} && {command}"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace")
        try:
            for text in process.stdout:
                on_line(text)
            return process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def reconnect(self) -> None:
        """Drops the shared connection: the next command logs in anew (new groups apply)."""
        subprocess.run(self._ssh("-O", "exit"), stdin=subprocess.DEVNULL, capture_output=True)

    def ready(self) -> bool:
        """Docker (the daemon, for this user: `docker compose version` alone answers
        without it), its Compose plugin, rsync and cron (the daily backup) work there."""
        return self.output("docker info --format '{{.ServerVersion}}' && docker compose version --short "
                           "&& command -v rsync && command -v crontab") is not None

    def output(self, command: str) -> str | None:
        """The output of `command` (in the home directory); None when it fails."""
        # stdin closed: ssh would otherwise read what the user types ahead.
        result = subprocess.run(self._ssh("-o", "BatchMode=yes", "-o", "ConnectTimeout=15") + [command],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def _rsync(self, *args: str, what: str) -> None:
        ssh = " ".join(shlex.quote(part) for part in self._ssh()[:-1])
        result = subprocess.run(["rsync", "-t", "--chmod=F600", "-e", ssh, *args],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if result.returncode:
            raise DeployError(f"{what} failed: {result.stderr.strip()}")

    def fetch(self, names: list[str], into: Path) -> None:
        """Copies these files of ~/inatrace (paths in it) here, into `into`, only readable
        by you: backups hold every password hash."""
        into.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._rsync(*(f"{self.destination}:{REMOTE_DIR}/{name}" for name in names), f"{into}/",
                    what=f"copying from {self.destination}")

    def send(self, files: list[Path], into: str) -> None:
        """Copies these files there, into ~/inatrace/`into`/."""
        self._rsync(*(str(path) for path in files), f"{self.destination}:{REMOTE_DIR}/{into}/",
                    what=f"copying to {self.destination}")

    def sync(self, files: dict[str, Path], dry_run: bool = False) -> "Changes":
        """Makes ~/inatrace hold exactly these files (name -> local file), except
        backups/: rsync removes what is not in `files`. Says what changed, or with
        `dry_run` what would, changing nothing."""
        with tempfile.TemporaryDirectory(prefix="inatrace-deploy-") as staging:
            for name, source in files.items():
                target = Path(staging, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            ssh = " ".join(shlex.quote(part) for part in self._ssh()[:-1])
            # Modes from a checkout vary (umask, mounts): 755 for directories and
            # scripts, 644 for the rest, then 600 for the secrets.
            cmd = ["rsync", "-rlpcz", "--delete", "--exclude=/backups/", "--itemize-changes",
                   "--chmod=Du=rwx,Dgo=rx,Fu=rwX,Fgo=rX", "-e", ssh,
                   *(["--dry-run"] if dry_run else []),
                   f"{staging}/", f"{self.destination}:{REMOTE_DIR}/"]
            result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if result.returncode or (not dry_run and self.output(f"chmod 600 {REMOTE_DIR}/.env") is None):
                raise DeployError(f"copying the files to {self.destination}:~/{REMOTE_DIR} failed:\n"
                                  + result.stderr.strip())
        return Changes.parse(result.stdout)

    def compose_plan(self, files: dict[str, Path]) -> list[tuple[str, str]]:
        """What `docker compose up` would do with these compose.yaml and .env: (action,
        service) pairs, from Compose's own dry run. The two files go to a temporary
        directory there, the project stays ~/inatrace, so the mounts compare right."""
        temp = self.output("mktemp -d")
        if not temp:
            raise DeployError(f"could not plan on {self.destination}")
        try:
            ssh = " ".join(shlex.quote(part) for part in self._ssh()[:-1])
            copied = subprocess.run(["rsync", "-q", "--chmod=F600", "-e", ssh, str(files["compose.yaml"]),
                                     str(files[".env"]), f"{self.destination}:{temp}/"],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if copied.returncode:
                raise DeployError(f"could not plan on {self.destination}: {copied.stderr.strip()}")
            planned = self.must(f"docker compose --project-name inatrace --project-directory ~/{REMOTE_DIR} "
                                f"-f {temp}/compose.yaml --env-file {temp}/.env --dry-run "
                                "up -d --remove-orphans 2>&1", "planning")
        finally:
            self.output(f"rm -rf {temp}")
        return planned_actions(planned)

    def plan(self) -> list[tuple[str, str]]:
        """What `docker compose up` would do with the files already in ~/inatrace."""
        return planned_actions(self.must("docker compose --dry-run up -d --remove-orphans 2>&1", "planning"))


def planned_actions(planned: str) -> list[tuple[str, str]]:
    """(action, service) pairs from the output of Compose's dry run; not one-shot
    containers (beszel-init), which run at every start."""
    actions = []
    for line in planned.splitlines():
        words = line.split()
        if len(words) == 3 and words[0] == "Container" and words[2] in ("Create", "Recreate", "Remove"):
            service = re.sub(r"^inatrace-(.*)-\d+$", r"\1", words[1])
            if not service.endswith("-init"):
                actions.append((words[2].lower(), service))
    return actions


@dataclass
class Changes:
    """Files rsync put in place (or would), by path in ~/inatrace."""
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, itemized: str) -> "Changes":
        """From `rsync --itemize-changes`: `<f+++++++++ name` new (`<` sent, `>` received),
        `<fc.....` changed, `*deleting name` removed; directories and attribute-only lines
        (`.f`) ignored."""
        changes = cls()
        for line in itemized.splitlines():
            flags, _, name = line.partition(" ")
            if flags == "*deleting":
                if not name.endswith("/"):
                    changes.removed.append(name.strip())
            elif flags[:2] in ("<f", ">f"):
                (changes.added if "+++++++" in flags else changes.changed).append(name)
        return changes

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.removed)

    def touching(self, prefixes: tuple[str, ...]) -> bool:
        return any(path.startswith(prefixes) for path in self.added + self.changed + self.removed)


# The Compose services, the way in first; and the monitoring's, when it is on.
SERVICES = ("caddy", "frontend", "backend", "mysql")
MONITORING = ("beszel", "beszel-agent")

# Services that read files from ~/inatrace: Compose recreates a container when its
# configuration changes, not when a mounted file does, so `up` restarts them then.
MOUNTED = {"caddy": ("Caddyfile", "caddy/"),
           "backend": ("backend.properties", LOCAL_PROPERTIES)}


@dataclass
class Probe:
    reached: bool
    system: str = ""
    ids: str = ""
    ready: bool = False
    busy: bool = False  # its package manager is running: installing now would fail

    @property
    def profile(self) -> str | None:
        return prereq_profile(self.ids)


def ssh_host(destination: str) -> str:
    """The host name ssh connects to for `destination` (resolves ~/.ssh/config aliases)."""
    result = subprocess.run(["ssh", "-G", destination], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("hostname "):
            return line.split(" ", 1)[1]
    return destination.rsplit("@", 1)[-1]


def public_address(remote: Remote) -> str:
    """The server's public IPv4 address as the internet sees it, or else the
    address ssh connects to; empty when neither is known."""
    seen = remote.output("curl -4 -fsS --max-time 10 ifconfig.me")
    if seen and is_ip(seen):
        return seen
    try:
        return socket.gethostbyname(ssh_host(remote.destination))
    except OSError:
        return ""


def prereq_profile(os_release: str) -> str | None:
    """The profile in deploy/prereqs/ for a server's /etc/os-release ID and ID_LIKE
    (space-separated), if one fits."""
    names = os_release.split()
    return next((profile for profile, fits in PREREQ_PROFILES.items()
                 if any(name in fits for name in names)), None)


def install_prereqs(remote: Remote, profile: str) -> None:
    """Installs Docker, rsync and cron on the server with a profile in deploy/prereqs/."""
    ui.step(f"installing the dependencies on {remote.destination} with deploy/prereqs/{profile}.sh")
    # A terminal only for sudo's password: then Ctrl+C goes to the server, not to us.
    tty = remote.output("sudo -n true") is None
    if tty and ui.json_mode():
        raise DeployError(f"sudo asks for a password on {remote.destination}, and --json asks nothing: "
                          f"run it in a terminal, or let {remote.destination} sudo without one")
    if tty:
        ui.info("sudo will ask your password there. Ctrl+C now reaches the server; if it does not "
                "stop, press Enter then ~. to drop the connection")
    with ui.Stages() as stages:
        def show(text: str) -> None:
            stage = _STAGE.match(text)
            if stage:
                stages.begin(stage.group(1))
            else:
                stages.text(text)

        code = remote.script(paths.DEPLOY_DIR / "prereqs" / f"{profile}.sh", show, tty=tty)
        stages.end(code == 0)
    if code:
        raise DeployError(f"deploy/prereqs/{profile}.sh failed on {remote.destination}")
    with ui.doing("logging in again, so the docker group applies"):
        remote.reconnect()
        if not remote.ready():
            raise DeployError(f"the dependencies still do not work on {remote.destination} over ssh")
    ui.ok("the dependencies are ready")


def connect(instance: Instance) -> Remote:
    found = problems(instance.values)
    if found:
        raise DeployError(f"{instance.env_file}:\n  - " + "\n  - ".join(found))
    return Remote(instance.get("INATRACE_SSH"))


def deployed(instance: Instance) -> Remote:
    """The instance's server, once `up` has put it there."""
    remote = connect(instance)
    if remote.output(f"test -f ~/{REMOTE_DIR}/compose.yaml") is None:
        if remote.output("true") is None:
            raise DeployError(f"could not log in to {remote.destination} without a question")
        raise DeployError(f"{instance.name} is not on {remote.destination} yet: "
                          f"inatrace deploy up {instance.name}")
    return remote


def wait_healthy(remote: Remote, name: str, service: str, minutes: int) -> None:
    """Polls until the container runs and its health check passes (or it has none)."""
    deadline = time.monotonic() + minutes * 60
    found = ""
    while time.monotonic() < deadline:
        found = remote.output(f"cd ~/{REMOTE_DIR} && docker compose ps -a --format "
                              f"'{{{{.State}}}} {{{{.Health}}}}' {service}") or ""
        state, _, health = found.partition(" ")
        if state == "running" and health in ("healthy", ""):
            return
        if state in ("exited", "dead") or health == "unhealthy":
            raise DeployError(f"{service} is {found}: inatrace deploy logs {name} {service}")
        time.sleep(2)
    raise DeployError(f"{service} is not healthy after {minutes} min ({found or 'not found'}): "
                      f"inatrace deploy logs {name} {service}")
