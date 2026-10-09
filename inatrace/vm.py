"""`inatrace vm …`: local VMs to try deploys on, as a cloud would hand them over: a
distribution's cloud image in QEMU with KVM, your ssh keys through cloud-init, its ports
forwarded on 127.0.0.1, and a Host for it in ~/.ssh/config (docs/deploy.md → Try it on a
local VM). What the host needs is checked, never installed."""

import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.markup import escape

from . import ui
from .deploy.common import DeployError, size, would
from .deploy.instance import check_name

VMS = Path.home() / ".local" / "share" / "inatrace" / "vms"
IMAGES = Path.home() / ".cache" / "inatrace" / "images"
SSH_CONFIG = Path.home() / ".ssh" / "config"
SSH_WAIT = 300  # seconds for a new VM to boot and let ssh in, cloud-init done


@dataclass(frozen=True)
class Distro:
    url: str
    user: str  # the cloud image's own user, who gets the keys and sudo without a password

    @property
    def file(self) -> str:
        return self.url.rsplit("/", 1)[1]


DISTROS = {
    "ubuntu-26.04": Distro("https://cloud-images.ubuntu.com/releases/26.04/release/"
                           "ubuntu-26.04-server-cloudimg-amd64.img", "ubuntu"),
    "debian-13": Distro("https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2",
                        "debian"),
    "rocky-10": Distro("https://dl.rockylinux.org/pub/rocky/10/images/x86_64/"
                       "Rocky-10-GenericCloud-Base.latest.x86_64.qcow2", "rocky"),
}
DEFAULT_DISTRO = "ubuntu-26.04"
FIRST_PORTS = {"ssh": 2022, "http": 10080, "https": 10443}  # the VM's 22, 80 and 443

# What the host needs, and its package per family of distributions.
TOOLS = {
    "qemu-system-x86_64": {"debian": "qemu-system-x86", "fedora": "qemu-system-x86-core", "suse": "qemu-x86",
                           "arch": "qemu-system-x86"},
    "qemu-img": {"debian": "qemu-utils", "fedora": "qemu-img", "suse": "qemu-tools", "arch": "qemu-img"},
    "cloud-localds": {"debian": "cloud-image-utils", "fedora": "cloud-utils", "suse": "cloud-utils",
                      "arch": "cloud-image-utils"},
    "curl": {"debian": "curl", "fedora": "curl", "suse": "curl", "arch": "curl"},
    "ssh": {"debian": "openssh-client", "fedora": "openssh-clients", "suse": "openssh-clients", "arch": "openssh"},
}
INSTALL = {"debian": "sudo apt-get install", "fedora": "sudo dnf install", "suse": "sudo zypper install",
           "arch": "sudo pacman -S"}


class VmError(DeployError):
    """Something to fix first; the message says what."""


@dataclass
class Vm:
    name: str
    distro: str
    user: str
    memory: int  # GB
    cpus: int
    disk: int  # GB
    ports: dict[str, int]

    @property
    def dir(self) -> Path:
        return VMS / self.name

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "vm.json").write_text(json.dumps(asdict(self), indent=2) + "\n")


def load(name: str) -> Vm:
    path = VMS / name / "vm.json"
    if not path.exists():
        raise VmError(f"no VM {name}: inatrace vm create {name} (inatrace vm list shows them)")
    return Vm(**json.loads(path.read_text()))


def every() -> list[Vm]:
    return [load(path.parent.name) for path in sorted(VMS.glob("*/vm.json"))] if VMS.exists() else []


# --- the host ---

def family() -> str | None:
    """debian, fedora, suse or arch, from /etc/os-release; None for another."""
    try:
        release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    except OSError:
        return None
    ids = f"{release.get('ID', '')} {release.get('ID_LIKE', '')}".replace('"', "").split()
    for known, names in (("debian", ("debian", "ubuntu")), ("fedora", ("fedora", "rhel", "centos")),
                         ("suse", ("suse", "opensuse", "sles")), ("arch", ("arch",))):
        if any(name in ids or any(i.startswith(name) for i in ids) for name in names):
            return known
    return None


def agent_keys() -> list[str]:
    try:
        listed = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in listed.stdout.splitlines() if line.startswith(("ssh-", "ecdsa-", "sk-"))] \
        if listed.returncode == 0 else []


def missing() -> list[dict[str, str]]:
    """What the host lacks to run a VM, each with how to get it (nothing is installed)."""
    found = []
    if platform.machine() not in ("x86_64", "AMD64"):
        found.append({"what": f"an x86_64 machine (this is {platform.machine()})",
                      "how": "the VMs run the x86_64 cloud images with KVM"})
    kin = family()
    for tool, packages in TOOLS.items():
        if not shutil.which(tool):
            found.append({"what": tool, "how": f"{INSTALL[kin]} {packages[kin]}" if kin else
                          f"its package (Debian's: {packages['debian']})", "package": packages.get(kin or "debian")})
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        found.append({"what": "/dev/kvm, usable by you",
                      "how": "KVM on (virtualization in the BIOS), and you in its group: sudo usermod -aG kvm "
                             "$USER, then log in again" if Path("/dev/kvm").exists() else
                             "KVM: virtualization on in the BIOS (or nested, in a VM), the kvm module loaded"})
    if shutil.which("ssh") and not agent_keys():
        found.append({"what": "a key in your ssh agent", "how": "ssh-add (the VM lets in the agent's keys)"})
    return found


def _free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_ports(taken: list[dict[str, int]], given: dict[str, int | None]) -> dict[str, int]:
    """The first set of ports, one apart from the last, that no VM has and nothing listens on;
    a given one is kept as is."""
    used = {port for ports in taken for port in ports.values()}
    for step in range(100):
        ports = {kind: given.get(kind) or first + step for kind, first in FIRST_PORTS.items()}
        if all(given.get(kind) or (port not in used and _free(port)) for kind, port in ports.items()):
            return ports
    raise VmError("no free ports found: give --ssh-port, --http-port and --https-port")


# --- ~/.ssh/config ---

def _marks(name: str) -> tuple[str, str]:
    return f"# inatrace vm {name} (inatrace vm destroy {name} removes it)", f"# end of inatrace vm {name}"


def ssh_block(vm: Vm) -> str:
    begin, end = _marks(vm.name)
    return (f"{begin}\nHost {vm.name}\n    HostName 127.0.0.1\n    Port {vm.ports['ssh']}\n    User {vm.user}\n"
            f"    UserKnownHostsFile {vm.dir / 'known_hosts'}\n    StrictHostKeyChecking accept-new\n{end}\n")


def without_block(text: str, name: str) -> str:
    begin, end = _marks(name)
    if begin not in text:
        return text
    head, rest = text.split(begin, 1)
    tail = rest.split(end, 1)[1] if end in rest else ""
    return head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n")


def foreign_host(text: str, name: str) -> bool:
    """A `Host <name>` in ~/.ssh/config that is not one of ours."""
    mine = without_block(text, name)
    return any(line.split()[1:] and name in line.split()[1:] for line in mine.splitlines()
               if line.strip().lower().startswith("host "))


def _write_ssh_config(text: str) -> None:
    SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    SSH_CONFIG.write_text(text)
    SSH_CONFIG.chmod(0o600)


def _ssh_config() -> str:
    return SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""


# --- the VM's process ---

def pid(vm: Vm) -> int | None:
    """Its QEMU's process id, while it runs."""
    try:
        number = int((vm.dir / "qemu.pid").read_text().strip())
        command = Path(f"/proc/{number}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return number if b"qemu" in command and str(vm.dir).encode() in command else None


def qemu_command(vm: Vm) -> list[str]:
    forwards = ",".join(f"hostfwd=tcp:127.0.0.1:{vm.ports[kind]}-:{guest}"
                        for kind, guest in (("ssh", 22), ("http", 80), ("https", 443)))
    return ["qemu-system-x86_64", "-name", vm.name, "-enable-kvm", "-cpu", "host", "-m", f"{vm.memory}G",
            "-smp", str(vm.cpus), "-display", "none", "-daemonize", "-pidfile", str(vm.dir / "qemu.pid"),
            "-serial", f"file:{vm.dir / 'serial.log'}",
            "-drive", f"file={vm.dir / 'disk.qcow2'},if=virtio",
            "-drive", f"file={vm.dir / 'seed.img'},if=virtio,format=raw",
            "-nic", f"user,model=virtio-net-pci,{forwards}"]


def _run(command: list[str], what: str, timeout: int = 600) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode:
        raise ui.StepError(f"{what}: {(done.stderr or done.stdout).strip()[-500:]}")


def _boot(vm: Vm) -> None:
    with ui.doing(f"starting {vm.name} ({vm.memory} GB, {vm.cpus} CPUs)"):
        _run(qemu_command(vm), "QEMU did not start", timeout=60)
    with ui.doing("waiting for it to boot and let ssh in"):
        deadline = time.monotonic() + SSH_WAIT
        while True:
            reached = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", vm.name,
                                      "cloud-init status --wait >/dev/null 2>&1; true"],
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=SSH_WAIT, check=False)
            if reached.returncode == 0:
                break
            if pid(vm) is None:
                raise ui.StepError(f"{vm.name} stopped while booting: see {vm.dir / 'serial.log'}")
            if time.monotonic() > deadline:
                raise ui.StepError(f"no ssh after {SSH_WAIT} s: see {vm.dir / 'serial.log'}")
            time.sleep(3)


def _next(vm: Vm) -> None:
    def render() -> None:
        ui.ok(f"{vm.name} is up: ssh {vm.name}")
        ui.info(f"its HTTPS is https://127.0.0.1:{vm.ports['https']}, its HTTP 127.0.0.1:{vm.ports['http']}")
        ui.out.print(f"\n  Next: inatrace deploy init {vm.name}  [dim](as its public address: 127.0.0.1)[/]")

    ui.show("vm", {"name": vm.name, "ssh": vm.name, "ports": vm.ports,
                   "site": f"https://127.0.0.1:{vm.ports['https']}"}, render)


# --- the commands ---

def create(name: str, distro: str = DEFAULT_DISTRO, memory: int = 4, cpus: int = 2, disk: int = 20,
           ports: dict[str, int | None] | None = None, dry_run: bool = False, auto_approve: bool = False) -> int:
    """Makes and starts a VM like a fresh cloud server: the distribution's cloud image, a disk on
    top of it, your agent's ssh keys for its user (with sudo), ports on 127.0.0.1, and `Host
    <name>` in ~/.ssh/config, so `inatrace deploy init <name>` takes it as it is."""
    check_name(name)
    ui.section(f"{'Planning' if dry_run else 'Creating'} the VM {name}")
    lacking = missing()
    if lacking:
        def render() -> None:
            ui.warn("this machine lacks what a VM needs; nothing was installed:")
            for item in lacking:
                ui.out.print(f"   [bold]{escape(item['what'])}[/]: {escape(item['how'])}")

        ui.show("missing", {"missing": lacking}, render)
        raise VmError("install what is missing, then the same command again")
    if (VMS / name / "vm.json").exists():
        raise VmError(f"{name} exists: inatrace vm start {name}, or inatrace vm destroy {name} first")
    if foreign_host(_ssh_config(), name):
        raise VmError(f"~/.ssh/config already has a Host {name}: pick another name")
    image = DISTROS[distro]
    vm = Vm(name, distro, image.user, memory, cpus, disk,
            free_ports([v.ports for v in every()], ports or {}))
    keys = agent_keys()
    cached = IMAGES / image.file
    actions = [f"download {distro}'s cloud image ({image.url})" if not cached.exists()
               else f"use {distro}'s cloud image, downloaded before ({cached})",
               f"make its disk ({disk} GB, on top of the image) and its cloud-init: the {len(keys)} keys in "
               f"your ssh agent for {image.user}, who has sudo",
               f"start it ({memory} GB, {cpus} CPUs) with ssh on 127.0.0.1:{vm.ports['ssh']}, HTTP on "
               f"{vm.ports['http']} and HTTPS on {vm.ports['https']}",
               f"add Host {name} to ~/.ssh/config, and wait until it lets ssh in"]
    note = f"it lives in {vm.dir}; the image stays in {IMAGES}"
    if dry_run:
        would(*actions, note=note)
        return 0

    def render() -> None:
        ui.out.print("  [bold]Will do:[/]")
        for number, action in enumerate(actions, 1):
            ui.out.print(f"   {number}. {escape(action)}")
        ui.info(note)

    ui.show("plan", {"actions": actions, "note": note}, render)
    if not auto_approve:
        try:
            if not ui.confirm("Go ahead?", True):
                ui.info("nothing was changed")
                return 0
        except ui.NoAnswer:
            raise VmError("no answer for “Go ahead?”: give --auto-approve", flag="--auto-approve") from None
    if not cached.exists():
        IMAGES.mkdir(parents=True, exist_ok=True)
        partial = cached.with_suffix(cached.suffix + ".part")
        with ui.doing(f"downloading {distro}'s cloud image"):
            _run(["curl", "-fsSL", "--retry", "3", "-o", str(partial), image.url], "the download failed",
                 timeout=3600)
            partial.rename(cached)
        ui.info(f"{size(cached.stat().st_size)}, kept for the next VMs in {IMAGES}")
    vm.save()
    with ui.doing("making its disk and its cloud-init"):
        _run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2", "-b", str(cached),
              str(vm.dir / "disk.qcow2"), f"{disk}G"], "qemu-img failed")
        (vm.dir / "user-data").write_text("#cloud-config\nhostname: " + name + "\nssh_authorized_keys:\n"
                                          + "".join(f"  - {key}\n" for key in keys))
        (vm.dir / "meta-data").write_text(f"instance-id: {name}\nlocal-hostname: {name}\n")
        _run(["cloud-localds", str(vm.dir / "seed.img"), str(vm.dir / "user-data"), str(vm.dir / "meta-data")],
             "cloud-localds failed")
    rest = without_block(_ssh_config(), name).rstrip("\n")
    _write_ssh_config((rest + "\n\n" if rest else "") + ssh_block(vm))
    ui.ok(f"Host {name} is in ~/.ssh/config")
    _boot(vm)
    _next(vm)
    return 0


def start(name: str) -> int:
    vm = load(name)
    if pid(vm):
        ui.ok(f"{name} is running already: ssh {name}")
        return 0
    _boot(vm)
    _next(vm)
    return 0


def _stop(vm: Vm) -> None:
    with ui.doing(f"stopping {vm.name}"):
        subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", vm.name, "sudo", "systemctl",
                        "poweroff"], stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=False)
        deadline = time.monotonic() + 60
        while pid(vm) and time.monotonic() < deadline:
            time.sleep(1)
        if (number := pid(vm)) is not None:  # it would not power off (a running apt holds it): QEMU goes
            os.kill(number, signal.SIGTERM)
            time.sleep(2)


def stop(name: str) -> int:
    vm = load(name)
    if not pid(vm):
        ui.ok(f"{name} is stopped already")
        return 0
    _stop(vm)
    ui.info(f"its disk stays: inatrace vm start {name}")
    return 0


def destroy(name: str, dry_run: bool = False, auto_approve: bool = False) -> int:
    vm = load(name)
    ui.section(f"{'Planning to destroy' if dry_run else 'Destroying'} the VM {name}")
    used = sum(f.stat().st_size for f in vm.dir.iterdir() if f.is_file())
    actions = [*([f"stop {name}"] if pid(vm) else []), f"remove {vm.dir} ({size(used)}), its disk included",
               f"remove Host {name} from ~/.ssh/config"]
    note = f"the cloud image stays in {IMAGES}, for the next VM"
    if dry_run:
        would(*actions, note=note)
        return 0

    def render() -> None:
        ui.out.print("  [bold]Will do:[/]")
        for action in actions:
            ui.out.print(f"   {escape(action)}")
        ui.info(note)

    ui.show("plan", {"actions": actions, "note": note}, render)
    if not auto_approve:
        try:
            if ui.ask_text(f"Type {name} to destroy it") != name:
                ui.info("nothing was changed")
                return 0
        except ui.NoAnswer:
            raise VmError(f"no answer for “Type {name} to destroy it”: give --auto-approve",
                          flag="--auto-approve") from None
    if pid(vm):
        _stop(vm)
    shutil.rmtree(vm.dir)
    _write_ssh_config(without_block(_ssh_config(), name))
    ui.ok(f"{name} is gone")
    return 0


def listing() -> int:
    vms = every()
    rows = [{"name": vm.name, "distro": vm.distro, "running": pid(vm) is not None, "ssh": vm.name,
             "ports": vm.ports, "memory_gb": vm.memory, "cpus": vm.cpus} for vm in vms]

    def render() -> None:
        from rich.table import Table

        if not rows:
            ui.info("no VMs: inatrace vm create <name>")
            return
        table = Table(box=None, padding=(0, 2), header_style="dim")
        for column in ("", "name", "distribution", "ssh · https · http on 127.0.0.1", "size"):
            table.add_column(column)
        for row in rows:
            ports = row["ports"]
            table.add_row("[green]●[/]" if row["running"] else "[dim]○[/]", row["name"], row["distro"],
                          f"{ports['ssh']} · {ports['https']} · {ports['http']}",
                          f"{row['memory_gb']} GB · {row['cpus']} CPUs")
        ui.out.print(table)

    ui.show("vms", {"vms": rows}, render)
    return 0
