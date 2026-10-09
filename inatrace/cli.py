"""Command line: `inatrace <command>`. The same with or without the dev container;
.devcontainer/ holds what is specific to it."""

import subprocess
import sys
from enum import Enum
from typing import Annotated, Optional

import typer

from . import (config, doctor as doctor_, paths, permissions as permissions_, repos as repos_,
               smoke as smoke_, stack as stack_, ui, vm as vm_)
from .deploy import cli as deploy_cli
from .options import DryRun, exit_with

app = typer.Typer(help="Control the INATrace platform: the repos, the dev stack and deploys.",
                  no_args_is_help=True)
repos_app = typer.Typer(help="The INATrace repos in repos/repos.txt.", no_args_is_help=True)
stack_app = typer.Typer(help="The dev stack: gateway, MySQL, Mailpit and, per mode, the images.",
                        no_args_is_help=True)
app.add_typer(repos_app, name="repos")
app.add_typer(stack_app, name="stack")
app.add_typer(deploy_cli.app, name="deploy")
vm_app = typer.Typer(help="Local VMs to try deploys on, like a fresh cloud server: QEMU, the distribution's cloud "
                          "image, your ssh keys (docs/deploy.md → Try it on a local VM).", no_args_is_help=True)
app.add_typer(vm_app, name="vm")

Json = Annotated[bool, typer.Option("--json", help="JSON Lines on stdout, one event a line, for programs; "
                                                   "asks nothing. Anywhere on the line (docs/cli.md → JSON).")]
Mode = Enum("Mode", {m.replace("-", "_"): m for m in stack_.MODES}, type=str)


def _mode_help() -> str:
    """Which mode `stack up` uses without --mode, and where it comes from."""
    configured = config.load(paths.USER_ENV).values.get("INATRACE_MODE")
    source = f"INATRACE_MODE in .env: {configured}" if configured else \
        f"INATRACE_MODE is not set: {stack_.DEFAULT_MODE}, the default"
    return f"For this run only. Without it: {source}."


def _run(action, *args) -> None:
    try:
        exit_with(action(*args))
    except (ui.StepError, OSError, subprocess.CalledProcessError) as error:
        ui.error(str(error))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        ui.blank()
        ui.error("interrupted")
        raise typer.Exit(130)


@app.command()
def doctor() -> None:
    """Report what the environment provides."""
    _run(doctor_.report)


def _fix_permissions(dry_run: bool) -> int:
    if dry_run:
        found = permissions_.writable(paths.ROOT)

        def render() -> None:
            ui.out.print(f"  [bold]Would do:[/] remove group/other write from {len(found)} files and "
                         f"directories under {paths.ROOT}")
            for path in found[:10]:
                ui.info(path)
            if len(found) > 10:
                ui.info(f"and {len(found) - 10} more")

        ui.show("plan", {"actions": [f"remove group/other write from {len(found)} files and directories "
                                     f"under {paths.ROOT}"], "paths": [str(p) for p in found]}, render)
        ui.info("dry run: nothing was changed")
        return 0
    fixed, failed = permissions_.fix(paths.ROOT)
    loose = permissions_.umask_lets_others_write()

    def render() -> None:
        ui.ok(f"{fixed} files and directories under {paths.ROOT} were writable by others; no longer")
        for path in failed[:10]:
            ui.warn(f"not yours, left as is: {path}")
        if len(failed) > 10:
            ui.warn(f"and {len(failed) - 10} more")
        if loose:
            ui.warn(f"your umask is {permissions_.umask():04o}: new files will be writable by others again "
                    "(docs/dev-container.md → Troubleshooting)")

    ui.show("permissions", {"fixed": fixed, "not_yours": [str(p) for p in failed],
                            "umask": f"{permissions_.umask():04o}", "umask_lets_others_write": loose}, render)
    return 1 if failed else 0


@app.command("fix-permissions")
def fix_permissions(dry_run: DryRun = False) -> None:
    """Make the platform's files and the repos writable by you alone (chmod -R go-w)."""
    _run(_fix_permissions, dry_run)


def _sync() -> int:
    settings = config.load(paths.USER_ENV)
    listed = repos_.load(paths.REPOS_FILE)
    forks = settings.forks(listed)
    for warning in settings.warnings:
        ui.warn(f".env: {warning}")
    ui.section(f"repos sync: {len(listed)} repos from repos/repos.txt into repos/")
    repos_.sync(listed, paths.REPOS_DIR, forks)
    ui.ok("done (nothing was pulled or merged)")
    return 0


@repos_app.command()
def sync() -> None:
    """Clone missing repos, fetch existing ones (never merges or pulls)."""
    _run(_sync)


@stack_app.command()
def up(mode: Annotated[Optional[Mode], typer.Option(help=_mode_help())] = None,
       pull: Annotated[bool, typer.Option("--pull", help="Pull newer images first.")] = False) -> None:
    """Start it in INATRACE_MODE (default fullstack-dev), or --mode."""
    _run(stack_.up, mode.value if mode else None, pull)


@stack_app.command()
def down(volumes: Annotated[bool, typer.Option(
        "--volumes", help="Also erase MySQL and the uploads.")] = False) -> None:
    """Stop and remove its containers."""
    _run(stack_.down, volumes)


@stack_app.command()
def status() -> None:
    """Its containers."""
    _run(stack_.status)


@stack_app.command()
def logs(services: Annotated[Optional[list[str]], typer.Argument(help="Only these.")] = None,
         follow: Annotated[bool, typer.Option("--follow", "-f")] = False) -> None:
    """Their logs."""
    _run(stack_.logs, services or [], follow)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def smoke(ctx: typer.Context,
          verbose: Annotated[bool, typer.Option("--verbose", "-v",
                                                help="Each check as it runs, and what it does.")] = False,
          lifecycle: Annotated[bool, typer.Option(
              "--lifecycle", help="Also stop and start the services that run from images.")] = False) -> None:
    """Smoke-test the running dev stack, in whatever mode it runs.

    Extra options go to pytest, e.g. `inatrace smoke -- -k api`. See smoke-tests/README.md.
    """
    _run(smoke_.run, verbose, lifecycle, ctx.args)


VmName = Annotated[str, typer.Argument(help="The VM's name, also its Host in ~/.ssh/config.")]
Distro = Enum("Distro", {d: d for d in vm_.DISTROS}, type=str)
AutoApprove = Annotated[bool, typer.Option("--auto-approve", help="Skip the confirmation.")]


def _vm(action, *args) -> None:
    try:
        exit_with(action(*args))
    except (vm_.VmError, ui.StepError, OSError, subprocess.SubprocessError) as error:
        ui.error(str(error) if ui.json_mode() else f"vm: {error}", getattr(error, "flag", None))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        ui.blank()
        ui.error("interrupted")
        raise typer.Exit(130)


@vm_app.command("create")
def vm_create(name: VmName,
              distro: Annotated[Distro, typer.Option(help="Its distribution.")] = Distro(vm_.DEFAULT_DISTRO),
              memory: Annotated[int, typer.Option(help="GB of memory.")] = 4,
              cpus: Annotated[int, typer.Option(help="CPUs.")] = 2,
              disk: Annotated[int, typer.Option(help="GB of disk, at most (it grows as it fills).")] = 20,
              ssh_port: Annotated[Optional[int], typer.Option(help="Its ssh, on 127.0.0.1. Without it: the first "
                                                                   "free from 2022.")] = None,
              http_port: Annotated[Optional[int], typer.Option(help="Its port 80. Without it: from 10080.")] = None,
              https_port: Annotated[Optional[int], typer.Option(help="Its port 443. Without it: from 10443.")] = None,
              dry_run: DryRun = False, auto_approve: AutoApprove = False) -> None:
    """Make and start one, with Host <name> in ~/.ssh/config; first, what this machine lacks (installs nothing)."""
    _vm(vm_.create, name, distro.value, memory, cpus, disk,
        {"ssh": ssh_port, "http": http_port, "https": https_port}, dry_run, auto_approve)


@vm_app.command("list")
def vm_list() -> None:
    """The VMs, running or not, with their ports."""
    _vm(vm_.listing)


@vm_app.command("start")
def vm_start(name: VmName) -> None:
    """Start one again (its disk kept what it had)."""
    _vm(vm_.start, name)


@vm_app.command("stop")
def vm_stop(name: VmName) -> None:
    """Power one off; its disk stays."""
    _vm(vm_.stop, name)


@vm_app.command("destroy")
def vm_destroy(name: VmName, dry_run: DryRun = False, auto_approve: Annotated[bool, typer.Option(
        "--auto-approve", help="Skip typing its name to confirm.")] = False) -> None:
    """Stop one and remove it: its disk, and its Host in ~/.ssh/config (the image stays)."""
    _vm(vm_.destroy, name, dry_run, auto_approve)


@app.callback()
def options(json: Json = False) -> None:
    if json:
        ui.use_json()


# Options whose value may be left out, and the value they take then. typer's click has no
# optional values, so a bare one gets its value before typer reads the arguments.
OPTIONAL_VALUES = {"-w": "2", "--watch": "2"}


def with_optional_values(argv: list[str]) -> list[str]:
    """`status vm -w` becomes `status vm -w 2`; `-w 5` stays."""
    filled = []
    for index, arg in enumerate(argv):
        filled.append(arg)
        following = argv[index + 1] if index + 1 < len(argv) else None
        if arg in OPTIONAL_VALUES and (following is None or not following.replace(".", "", 1).isdigit()):
            filled.append(OPTIONAL_VALUES[arg])
    return filled


def with_global_options(argv: list[str]) -> list[str]:
    """`--json` is the program's option, taken anywhere on the line (up to a `--`):
    `deploy status vm --json` becomes `--json deploy status vm`."""
    end = argv.index("--") if "--" in argv else len(argv)
    if "--json" not in argv[:end]:
        return argv
    return ["--json"] + [arg for index, arg in enumerate(argv) if arg != "--json" or index > end]


def main() -> None:
    app(args=with_global_options(with_optional_values(sys.argv[1:])))
