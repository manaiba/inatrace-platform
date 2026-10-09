"""What the CLI's command modules share (inatrace/cli.py, inatrace/deploy/cli.py)."""

from typing import Annotated

import typer

DryRun = Annotated[bool, typer.Option("--dry-run", "-n", help="Only look, and say what it would do.")]


def exit_with(code: int) -> None:
    if code:
        raise typer.Exit(code)
