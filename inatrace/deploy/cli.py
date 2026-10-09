"""`inatrace deploy …`: the commands, their options and their errors (docs/deploy.md)."""

from enum import Enum
from typing import Annotated, Optional

import typer

from .. import ui
from ..options import DryRun, exit_with
from . import backups, checks, lifecycle, monitoring, users, wizard
from .common import DeployError
from .instance import CDN_HEADERS, FRONTS

app = typer.Typer(help="INATrace on a server you reach with ssh (docs/deploy.md).", no_args_is_help=True)

Name = Annotated[str, typer.Argument(help="The instance: deploy/instances/<name>/.")]


def _deploy(action, *args) -> None:
    try:
        exit_with(action(*args))
    except (DeployError, ui.StepError) as error:
        ui.error(str(error) if ui.json_mode() else f"deploy: {error}", getattr(error, "flag", None))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        ui.blank()
        half_done = ("what was running on the server may be half done: the same command again picks up "
                     "from there (every deploy command is safe to repeat)")
        ui.error(f"interrupted: {half_done}" if ui.json_mode() else "interrupted")
        if not ui.json_mode():
            ui.warn(half_done)
        raise typer.Exit(130)


Front = Enum("Front", {f: f for f in FRONTS}, type=str)
Cdn = Enum("Cdn", {c: c for c in (*CDN_HEADERS, "other")}, type=str)
_ANSWER = "Answers (each skips its question)"


@app.command()
def init(
    name: Name,
    ssh: Annotated[Optional[str], typer.Option(help="user@host, or a Host from ~/.ssh/config.",
                                               rich_help_panel=_ANSWER)] = None,
    install: Annotated[Optional[bool], typer.Option(
        "--install/--no-install", help="Install the server's dependencies if missing.",
        rich_help_panel=_ANSWER)] = None,
    domain: Annotated[Optional[str], typer.Option(help="The domain; empty for an IP address.",
                                                  rich_help_panel=_ANSWER)] = None,
    ip: Annotated[Optional[str], typer.Option(help="The public IP address, without a domain.",
                                              rich_help_panel=_ANSWER)] = None,
    front: Annotated[Optional[Front], typer.Option(help="What sits in front.", rich_help_panel=_ANSWER)] = None,
    cdn: Annotated[Optional[Cdn], typer.Option(help="With --front cdn: which one.",
                                               rich_help_panel=_ANSWER)] = None,
    cdn_header: Annotated[Optional[str], typer.Option(help="With --front cdn: its client address header.",
                                                      rich_help_panel=_ANSWER)] = None,
    origin_secret: Annotated[Optional[bool], typer.Option(
        "--origin-secret/--no-origin-secret", help="Require X-Origin-Secret (cdn, lb).",
        rich_help_panel=_ANSWER)] = None,
    swagger: Annotated[Optional[bool], typer.Option("--swagger/--no-swagger", help="Open Swagger UI.",
                                                    rich_help_panel=_ANSWER)] = None,
    backend_image: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    backend_version: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    frontend_image: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    frontend_version: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    mail: Annotated[Optional[bool], typer.Option("--mail/--no-mail",
                                                 help="Enable e-mail sending support (an SMTP server).",
                                                 rich_help_panel=_ANSWER)] = None,
    mail_host: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    mail_port: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    mail_username: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    mail_password: Annotated[Optional[str], typer.Option(
        envvar="INATRACE_DEPLOY_MAIL_PASSWORD", show_envvar=True,
        help="Better in the environment variable: a flag stays in your shell's history.",
        rich_help_panel=_ANSWER)] = None,
    mail_from: Annotated[Optional[str], typer.Option(rich_help_panel=_ANSWER)] = None,
    monitoring: Annotated[Optional[bool], typer.Option(
        "--monitoring/--no-monitoring", help="The monitoring dashboard (Beszel), opened with `deploy dashboard`.",
        rich_help_panel=_ANSWER)] = None,
    restore: Annotated[Optional[str], typer.Option(
        metavar="TIME", help="With backups here: the one the first up puts back (newest, a time, or none).",
        rich_help_panel=_ANSWER)] = None,
    dry_run: DryRun = False,
    auto_approve: Annotated[bool, typer.Option(
        "--auto-approve", help="Skip only the final confirmation of the summary; every other question "
                               "still needs its answer or flag.")] = False,
) -> None:
    """Ask for an instance's settings, show what will happen, then save them and install
    the server's dependencies if asked. Again on the same name: change them. Every answer can
    be given as a flag; with all of them and --auto-approve, nothing is asked."""
    given = {"ssh": ssh, "install": install, "domain": domain, "ip": ip,
             "front": front.value if front else None, "cdn": cdn.value if cdn else None,
             "cdn-header": cdn_header, "origin-secret": origin_secret, "swagger": swagger,
             "backend-image": backend_image, "backend-version": backend_version,
             "frontend-image": frontend_image, "frontend-version": frontend_version,
             "mail": mail, "mail-host": mail_host, "mail-port": mail_port,
             "mail-username": mail_username, "mail-password": mail_password, "mail-from": mail_from,
             "monitoring": monitoring, "restore": restore}
    _deploy(wizard.init, name, given, auto_approve, dry_run)


@app.command()
def prepare(name: Name, dry_run: DryRun = False) -> None:
    """Install the server's dependencies (deploy/prereqs/: Debian, Ubuntu, the RHEL family)."""
    _deploy(lifecycle.prepare, name, dry_run)


@app.command()
def up(name: Name,
       backup: Annotated[Optional[bool], typer.Option(
           "--backup/--no-backup", help="Back up first, or not; without either, only when the "
                                        "backend or the database changes.")] = None,
       restore: Annotated[Optional[str], typer.Option(
           metavar="TIME", help="Then put this backup back (newest: the newest, here or there); one only "
                                "here goes to the server first.")] = None,
       dry_run: DryRun = False) -> None:
    """Copy the instance to its server and start it, or apply a change (versions, settings)."""
    _deploy(lifecycle.up, name, backup, dry_run, restore)


@app.command()
def destroy(name: Name,
            backup: Annotated[bool, typer.Option(
                "--backup/--no-backup", help="Back up now and copy every backup here first.")] = True,
            auto_approve: Annotated[bool, typer.Option(
                "--auto-approve", help="Skip typing the instance's name to confirm.")] = False,
            dry_run: DryRun = False) -> None:
    """Undo what up did on the server: containers, volumes, images, ~/inatrace, the scheduled
    backup. First a backup, and every backup copied here; Docker and the instance here stay."""
    _deploy(lifecycle.destroy, name, dry_run, auto_approve, backup)


@app.command()
def down(name: Name, dry_run: DryRun = False) -> None:
    """Stop and remove its containers; the data and the backups stay (`up` starts it again)."""
    _deploy(lifecycle.down, name, dry_run)


backup_app = typer.Typer(help="The backups, on the server (~/inatrace/backups) and copied here "
                              "(deploy/instances/<name>/backups): list, create, download, restore, delete.",
                         no_args_is_help=True)
app.add_typer(backup_app, name="backup")
Time = Annotated[str, typer.Argument(help="Which backup: its time, the first column of `backup list`.")]


@backup_app.command("list")
def backup_list(name: Name) -> None:
    """The backups on the server and here, newest first, and when the next ones come."""
    _deploy(backups.listing, name)


@backup_app.command()
def create(name: Name, dry_run: DryRun = False) -> None:
    """Dump the database and archive the uploads, now."""
    _deploy(backups.create, name, dry_run)


@backup_app.command()
def download(name: Name,
             times: Annotated[Optional[list[str]], typer.Argument(
                 help="Which backups; without them, every one not here yet.")] = None,
             dry_run: DryRun = False) -> None:
    """Copy backups from the server here, to keep them off it (deploy/instances/<name>/backups)."""
    _deploy(backups.download, name, times or [], dry_run)


@backup_app.command()
def restore(name: Name, time: Time,
            backup: Annotated[bool, typer.Option(help="Back up the current state first.")] = True,
            dry_run: DryRun = False) -> None:
    """Put a backup back, replacing the database and the uploads; one only here goes to the
    server first."""
    _deploy(backups.restore, name, time, dry_run, backup)


@backup_app.command()
def delete(name: Name,
           times: Annotated[list[str], typer.Argument(help="Which backups: their times (`backup list`).")],
           dry_run: DryRun = False,
           auto_approve: Annotated[bool, typer.Option("--auto-approve", help="Skip the confirmation.")] = False,
           force: Annotated[bool, typer.Option("--force", help="Even the last one left on the server.")] = False,
           local: Annotated[bool, typer.Option("--local", help="The copies here, not the server's.")] = False) -> None:
    """Delete backups from the server, once confirmed (never the last one without --force);
    with --local, the copies here."""
    _deploy(backups.delete, name, times, dry_run, auto_approve, force, local)


@app.command()
def status(name: Name,
           watch: Annotated[Optional[float], typer.Option(
               "--watch", "-w", min=0.5, metavar="[SECONDS]",
               help="Redraw it every SECONDS (2 without them), full screen, until Ctrl+C; "
                    "with --json, an event each time.")] = None) -> None:
    """The site, the server, the containers (CPU, memory) and the backups."""
    _deploy(checks.status, name, watch is not None, watch or 2.0)


@app.command()
def logs(name: Name,
         services: Annotated[Optional[list[str]], typer.Argument(help="Only these.")] = None,
         follow: Annotated[bool, typer.Option("--follow", "-f")] = False) -> None:
    """Their logs."""
    _deploy(checks.logs, name, services or [], follow)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def smoke(ctx: typer.Context, name: Name,
          url: Annotated[Optional[str], typer.Option(
              help="Reach it here instead of https://<site>, e.g. through a forwarded port.")] = None,
          verbose: Annotated[bool, typer.Option("--verbose", "-v",
                                                help="Each check as it runs, and what it does.")] = False) -> None:
    """Smoke-test it, only reading: the site from outside, the images, the database and the
    logs over ssh. Extra options go to pytest (docs/smoke-tests.md)."""
    _deploy(checks.smoke, name, url, verbose, ctx.args)


@app.command()
def dashboard(name: Name,
              port: Annotated[int, typer.Option("--port", "-p", help="The local port to open it on.")] = 8090) -> None:
    """Open the monitoring dashboard (INATRACE_MONITORING=on): forwards a local port to it
    through ssh, until Ctrl+C."""
    _deploy(monitoring.dashboard, name, port)


@app.command()
def admin(name: Name, email: Annotated[str, typer.Argument(help="The user's e-mail.")],
          company: Annotated[Optional[str], typer.Option(
              help="Also make this company (active), with the user as its admin: a new "
                   "installation has none, and a user without one cannot get past the login.")] = None,
          create: Annotated[bool, typer.Option(
              "--create", help="Register the user first, when not yet (the site's form, done here).")] = False,
          password: Annotated[Optional[str], typer.Option(
              envvar="INATRACE_DEPLOY_ADMIN_PASSWORD", show_envvar=True,
              help="With --create: the password. Better in the environment variable: a flag stays in your "
                   "shell's history. Without it, asked.")] = None,
          generate_password: Annotated[bool, typer.Option(
              "--generate-password", help="With --create: make one up, and show it once.")] = False,
          first_name: Annotated[str, typer.Option(help="With --create.")] = "INATrace",
          last_name: Annotated[str, typer.Option(help="With --create.")] = "Admin",
          dry_run: DryRun = False) -> None:
    """Make a user an active system admin (the first one has nobody to activate them); with
    --create, register them first."""
    _deploy(users.admin, name, email, company, dry_run, create, password, generate_password, first_name,
            last_name)
