"""Command line: `inatrace <command>`. The same with or without the dev container;
.devcontainer/ holds what is specific to it."""

import subprocess
from enum import Enum
from typing import Annotated, Optional

import typer

from . import (config, doctor as doctor_, paths, permissions as permissions_, repos as repos_,
               smoke as smoke_, stack as stack_, ui)

app = typer.Typer(help="Control the INATrace platform: the repos and the dev stack.",
                  no_args_is_help=True)
repos_app = typer.Typer(help="The INATrace repos in repos/repos.txt.", no_args_is_help=True)
stack_app = typer.Typer(help="The dev stack: gateway, MySQL, Mailpit and, per mode, the images.",
                        no_args_is_help=True)
app.add_typer(repos_app, name="repos")
app.add_typer(stack_app, name="stack")

Mode = Enum("Mode", {m.replace("-", "_"): m for m in stack_.MODES}, type=str)


def _mode_help() -> str:
    """Which mode `stack up` uses without --mode, and where it comes from."""
    configured = config.load(paths.USER_ENV).values.get("INATRACE_MODE")
    source = f"INATRACE_MODE in .env: {configured}" if configured else \
        f"INATRACE_MODE is not set: {stack_.DEFAULT_MODE}, the default"
    return f"For this run only. Without it: {source}."


def _exit(code: int) -> None:
    if code:
        raise typer.Exit(code)


@app.command()
def doctor() -> None:
    """Report what the environment provides."""
    _exit(doctor_.report())


@app.command("fix-permissions")
def fix_permissions() -> None:
    """Make the platform's files and the repos writable by you alone (chmod -R go-w)."""
    fixed, failed = permissions_.fix(paths.ROOT)
    print(f"fix-permissions: {fixed} files and directories under {paths.ROOT} were writable "
          "by others; no longer", flush=True)
    for path in failed[:10]:
        ui.warn(f"!! not yours, left as is: {path}")
    if len(failed) > 10:
        ui.warn(f"!! and {len(failed) - 10} more")
    if permissions_.umask_lets_others_write():
        print(f"Your umask is {permissions_.umask():04o}: new files will be writable by others again. "
              "Why: docs/dev-container.md → Troubleshooting.")
    _exit(1 if failed else 0)


@repos_app.command()
def sync() -> None:
    """Clone missing repos, fetch existing ones (never merges or pulls)."""
    settings = config.load(paths.USER_ENV)
    try:
        listed = repos_.load(paths.REPOS_FILE)
        forks = settings.forks(listed)
        for warning in settings.warnings:
            ui.warn(f".env: {warning}")
        print(f"repos sync: {len(listed)} repos from repos/repos.txt into repos/", flush=True)
        repos_.sync(listed, paths.REPOS_DIR, forks)
        print("repos sync: done (nothing was pulled or merged)", flush=True)
    except (ui.StepError, OSError, subprocess.CalledProcessError) as error:
        ui.warn(f"!! repos sync: {error}")
        raise typer.Exit(1)


@stack_app.command()
def up(mode: Annotated[Optional[Mode], typer.Option(help=_mode_help())] = None,
       pull: Annotated[bool, typer.Option("--pull", help="Pull newer images first.")] = False) -> None:
    """Start it in INATRACE_MODE (default fullstack-dev), or --mode."""
    _exit(stack_.up(mode.value if mode else None, pull))


@stack_app.command()
def down(volumes: Annotated[bool, typer.Option(
        "--volumes", help="Also erase MySQL and the uploads.")] = False) -> None:
    """Stop and remove its containers."""
    _exit(stack_.down(volumes))


@stack_app.command()
def status() -> None:
    """Its containers."""
    _exit(stack_.status())


@stack_app.command()
def logs(services: Annotated[Optional[list[str]], typer.Argument(help="Only these.")] = None,
         follow: Annotated[bool, typer.Option("--follow", "-f")] = False) -> None:
    """Their logs."""
    _exit(stack_.logs(services or [], follow))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def smoke(ctx: typer.Context,
          verbose: Annotated[bool, typer.Option("--verbose", "-v",
                                                help="Each check as it runs, and what it does.")] = False,
          lifecycle: Annotated[bool, typer.Option(
              "--lifecycle", help="Also stop and start the services that run from images.")] = False) -> None:
    """Smoke-test the running dev stack, in whatever mode it runs.

    Extra options go to pytest, e.g. `inatrace smoke -- -k api`. See smoke-tests/README.md.
    """
    _exit(smoke_.run(verbose, lifecycle, ctx.args))


def main() -> None:
    app()
