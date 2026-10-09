"""`deploy init`: the questions (each with a flag), the summary, then saving and installing."""

from dataclasses import dataclass, field

from rich.markup import escape

from .. import config, registry, ui
from .common import DeployError
from .backups import local_backups
from .instance import (CDN_HEADERS, DOMAIN, EMAIL, FRONTS, HEADER, SECRETS, Instance, check_name, is_ip,
                       new_secret, problems, save, shown)
from .remote import BUSY, Probe, Remote, install_prereqs, public_address


def _ask(text: str, default: str = "", *, required: bool = False, hide: bool = False,
         check=None) -> str:
    """A question with a default (Enter keeps it). `check` returns an error or None."""
    while True:
        answer = ui.ask_text(text, default, hide=hide)
        if required and not answer:
            ui.warn("required")
            continue
        error = check(answer) if (check and answer) else None
        if error:
            ui.warn(error)
            continue
        return answer


def _ip_error(text: str) -> str | None:
    return None if is_ip(text) else "an IP address"


def _port_error(text: str) -> str | None:
    return None if text.isdigit() else "a port number"


class Asker:
    """The wizard's questions, each with a flag (`inatrace deploy init --help`): an answer
    given as a flag is checked like a typed one and not asked; the rest is asked, and
    without a terminal a missing answer stops `init` naming its flag."""

    def __init__(self, given: dict[str, object] | None = None) -> None:
        self.given = {key: value for key, value in (given or {}).items() if value is not None}

    def has(self, flag: str) -> bool:
        return flag in self.given

    def _from_flag(self, flag: str, question: str, shown: str) -> None:
        ui.show("answer", {"flag": f"--{flag}", "question": question, "value": shown},
                lambda: ui.out.print(f"  [bold]{escape(question)}[/]: {escape(shown)}  [dim](--{flag})[/]"))

    def text(self, flag: str, question: str, default: str = "", *, required: bool = False,
             hide: bool = False, check=None) -> str:
        if flag in self.given:
            value = str(self.given[flag]).strip()
            error = ("empty" if required and not value else
                     check(value) if (check and value) else None)
            if error:
                raise DeployError(f"--{flag} {value!r}: {error}")
            self._from_flag(flag, question, "••••••" if hide and value else value or "(empty)")
            return value
        try:
            return _ask(question, default, required=required, hide=hide, check=check)
        except ui.NoAnswer:
            raise DeployError(f"no answer for “{question}”: give it with --{flag}", flag=f"--{flag}") from None

    def choose(self, flag: str, question: str, options: tuple[str, ...], default: str) -> str:
        return self.text(flag, f"{question} [{'/'.join(options)}]", default, required=True,
                         check=lambda o: None if o in options else f"one of {', '.join(options)}")

    def confirm(self, flag: str, question: str, default: bool, *, negatable: bool = True) -> bool:
        if flag in self.given:
            value = bool(self.given[flag])
            self._from_flag(flag if value else f"no-{flag}", question, "yes" if value else "no")
            return value
        try:
            return ui.confirm(question, default)
        except ui.NoAnswer:
            flags = f"--{flag} or --no-{flag}" if negatable else f"--{flag}"
            raise DeployError(f"no answer for “{question}”: give {flags}", flag=f"--{flag}") from None


@dataclass
class Plan:
    """What `init` will do once the answers are confirmed."""
    install: str | None = None  # the prereqs profile to run on the server
    notes: list[str] = field(default_factory=list)
    restore: str | None = None  # a backup here for the first `up` to put back


def _ask_server(ask: Asker, values: dict[str, str], old: dict[str, str],
                name: str) -> tuple[Remote, Probe, Plan]:
    ui.section("The server")
    values["INATRACE_SSH"] = ask.text("ssh", "ssh destination (user@host, or a Host from ~/.ssh/config)",
                                      old.get("INATRACE_SSH", ""), required=True)
    remote = Remote(values["INATRACE_SSH"])
    with ui.doing(f"looking at {values['INATRACE_SSH']}"):
        probe = remote.probe()
    plan = Plan()
    if not probe.reached:
        ui.warn("could not log in without a question: see docs/deploy.md → What you need. "
                "`deploy up` needs it.")
    elif probe.ready:
        ui.ok(f"{probe.system}, with the dependencies")
    elif probe.profile:
        ui.warn(f"{probe.system}: dependencies missing")
        if probe.busy:
            ui.warn(BUSY)
            plan.notes.append(f"install the dependencies in a while: inatrace deploy prepare {name}")
        elif ask.confirm("install", "Install them there once you approve (uses sudo)?", True):
            plan.install = probe.profile
        else:
            plan.notes.append(f"install the dependencies later: inatrace deploy prepare {name}")
    else:
        ui.warn(f"{probe.system}: dependencies missing, and no profile for it in deploy/prereqs/: "
                "install them as docs/deploy.md → The server says")
    return remote, probe, plan


def _ask_address(ask: Asker, values: dict[str, str], old: dict[str, str], remote: Remote,
                 probe: Probe) -> None:
    ui.section("The address")
    old_site = old.get("INATRACE_SITE", "")
    if ask.has("ip") and not ask.has("domain"):
        domain = ""
    else:
        domain = ask.text("domain", "Domain (empty: by IP address, with a self-signed certificate)",
                          "" if is_ip(old_site) else old_site,
                          check=lambda d: None if DOMAIN.match(d) else "a domain name, e.g. inatrace.example.org")
    if domain:
        # Let's Encrypt; its staging, for tests, stays when the .env says so.
        values["INATRACE_SITE"] = domain
        values["INATRACE_TLS"] = "acme-staging" if old.get("INATRACE_TLS") == "acme-staging" else "acme"
        return
    detected = old_site if is_ip(old_site) else ""
    if not detected and probe.reached and not ask.has("ip"):
        with ui.doing("finding the server's public address"):
            detected = public_address(remote)
    values["INATRACE_SITE"] = ask.text("ip", "Public IP address", detected, required=True, check=_ip_error)
    values["INATRACE_TLS"] = "internal"


def _ask_front(ask: Asker, values: dict[str, str], old: dict[str, str]) -> None:
    ui.section("In front of the server")
    front = ask.choose("front", "What sits in front: direct (DNS straight to the server), cdn, "
                       "lb (cloud load balancer)", FRONTS, old.get("INATRACE_FRONT") or "direct")
    values["INATRACE_FRONT"] = front
    values.pop("INATRACE_CDN_IP_HEADER", None)
    if front == "cdn":
        provider = "other" if ask.has("cdn-header") and not ask.has("cdn") else \
            ask.choose("cdn", "Which CDN", (*CDN_HEADERS, "other"),
                       next((p for p, h in CDN_HEADERS.items()
                             if h == old.get("INATRACE_CDN_IP_HEADER")), "cloudflare"))
        if provider in CDN_HEADERS and not ask.has("cdn-header"):
            # A known CDN's: nothing to ask (--cdn-header still sets another).
            values["INATRACE_CDN_IP_HEADER"] = CDN_HEADERS[provider]
            ui.info(f"its header with the client's address: {CDN_HEADERS[provider]}")
        else:
            values["INATRACE_CDN_IP_HEADER"] = ask.text(
                "cdn-header", "Its header with the client's address", old.get("INATRACE_CDN_IP_HEADER", ""),
                required=True, check=lambda h: None if HEADER.match(h) else "a header name")
        if provider in ("cloudfront", "fastly", "akamai"):
            ui.info("this header needs setting up on the CDN: see docs/deploy.md → Behind a CDN")
    if front != "direct":
        use = ask.confirm("origin-secret", "Require a secret header from it (X-Origin-Secret; "
                          "recommended, the CDN or load balancer adds it)",
                          front == "cdn" or bool(old.get("INATRACE_ORIGIN_SECRET")))
        values["INATRACE_ORIGIN_SECRET"] = (old.get("INATRACE_ORIGIN_SECRET") or new_secret()) if use else ""
    else:
        values["INATRACE_ORIGIN_SECRET"] = ""
    swagger = ask.confirm("swagger", "Open the API description and Swagger UI to anyone",
                          old.get("INATRACE_SWAGGER") == "on")
    values["INATRACE_SWAGGER"] = "on" if swagger else "off"


VERSIONS_SHOWN = 5


def _ask_version(ask: Asker, flag: str, label: str, image: str, current: str) -> str:
    """The image's newest versions from its registry, five at a time, to choose by number;
    any tag can be typed instead. Without the list (a private registry, no network), a
    plain question. Given as a flag, nothing is listed."""
    if ask.has(flag):
        return ask.text(flag, f"{label} version", required=True)
    try:
        with ui.doing(f"listing the versions of {image}"):
            found = registry.versions(registry.tags(image))
    except registry.RegistryError as error:
        ui.warn(f"{error}: type the version")
        found = []
    if not found:
        return ask.text(flag, f"{label} version (image tag)", current, required=True)
    if ui.json_mode():
        ui.emit("versions", flag=f"--{flag}", image=image, versions=found[:20], current=current or None)
        return ask.text(flag, f"{label} version", current, required=True)
    shown = 0
    while True:
        for number in range(shown, min(shown + VERSIONS_SHOWN, len(found))):
            marks = [mark for mark, on in (("newest", number == 0), ("current", found[number] == current)) if on]
            ui.out.print(f"    [cyan]{number + 1:>2}[/]  {found[number]}"
                         + (f"  [dim]{', '.join(marks)}[/]" if marks else ""))
        shown = min(shown + VERSIONS_SHOWN, len(found))
        more = shown < len(found)
        hint = "a number, a tag" + (", or m for more" if more else "")
        answer = ask.text(flag, f"{label} version ({hint})", current, required=True)
        if answer == "m" and more:
            continue
        if answer.isdigit() and 1 <= int(answer) <= shown:
            return found[int(answer) - 1]
        if answer.isdigit():
            ui.warn(f"1 to {shown}")
            continue
        if answer not in found:
            ui.info(f"{answer} is not among the X.Y.Z versions listed: taken as is")
        return answer


def _ask_images(ask: Asker, values: dict[str, str], old: dict[str, str]) -> None:
    ui.section("What runs")
    ui.info("your choice: nothing picks a version for you")
    for part in ("BACKEND", "FRONTEND"):
        image_key, version_key = f"INATRACE_{part}_IMAGE", f"INATRACE_{part}_VERSION"
        values[image_key] = ask.text(f"{part.lower()}-image", f"{part.title()} image", old.get(image_key)
                                     or f"ghcr.io/agstack/inatrace-{part.lower()}", required=True)
        values[version_key] = _ask_version(ask, f"{part.lower()}-version", part.title(),
                                           values[image_key], old.get(version_key, ""))


def _ask_mail(ask: Asker, values: dict[str, str], old: dict[str, str]) -> None:
    ui.section("E-mail")
    mail = ask.confirm("mail", "Enable e-mail sending support (registration, password reset; an SMTP "
                       "server)? Without it an admin activates new users",
                       old.get("INATRACE_MAIL_ENABLED") == "true")
    values["INATRACE_MAIL_ENABLED"] = "true" if mail else "false"
    if not mail:
        return
    values["INATRACE_MAIL_HOST"] = ask.text("mail-host", "SMTP host", old.get("INATRACE_MAIL_HOST", ""),
                                            required=True)
    port = ask.text("mail-port", "SMTP port (587: STARTTLS, 465: SSL)",
                    old.get("INATRACE_MAIL_PORT") or "587", required=True, check=_port_error)
    values["INATRACE_MAIL_PORT"] = port
    values["INATRACE_MAIL_STARTTLS"] = "false" if port == "465" else "true"
    values["INATRACE_MAIL_SSL"] = "true" if port == "465" else "false"
    user = ask.text("mail-username", "SMTP username (empty: none)", old.get("INATRACE_MAIL_USERNAME", ""))
    values["INATRACE_MAIL_USERNAME"] = user
    values["INATRACE_MAIL_AUTH"] = "true" if user else "false"
    if user:
        saved = old.get("INATRACE_MAIL_PASSWORD", "")
        password = ask.text("mail-password", "SMTP password (Enter keeps the saved one)" if saved
                            else "SMTP password", hide=True, required=not saved)
        values["INATRACE_MAIL_PASSWORD"] = password or saved
    site = values.get("INATRACE_SITE", "")
    default_from = old.get("INATRACE_MAIL_FROM") or ("" if is_ip(site) else f"inatrace@{site}")
    values["INATRACE_MAIL_FROM"] = ask.text("mail-from", "Sender address", default_from, required=True,
                                            check=lambda a: None if EMAIL.match(a) else "an e-mail address")


def _ask_monitoring(ask: Asker, values: dict[str, str], old: dict[str, str]) -> None:
    ui.section("Monitoring")
    on = ask.confirm("monitoring", "A monitoring dashboard (Beszel: the server and its containers, with "
                     "history; about 20 MB; `inatrace deploy dashboard` opens it through ssh)",
                     old.get("INATRACE_MONITORING", "on" if not old else "off") == "on")
    values["INATRACE_MONITORING"] = "on" if on else "off"


def _ask_restore(ask: Asker, instance: Instance, plan: Plan) -> None:
    """With backups of this instance here (copied before `destroy`, or from another server),
    which one the first `up` puts back: asked, and said in the command to run next."""
    found = [backup["time"] for backup in local_backups(instance)]
    if not found and not ask.has("restore"):
        return
    ui.section("Backups")
    if found:
        ui.info(f"{len(found)} here, the newest {found[0]} ({shown(instance.dir / 'backups')})")
    answer = ask.text("restore", "Restore one on the first up (a time, newest, or none)", found[0] if found else "none",
                      required=True,
                      check=lambda t: None if t in ("none", "newest", *found) else "a time from the list, newest or none")
    if answer != "none":
        plan.restore = found[0] if answer == "newest" else answer
        plan.notes.append(f"inatrace deploy up {instance.name} --restore {plan.restore} puts it back")


def _summary(instance: Instance, old: dict[str, str], probe: Probe, plan: Plan) -> None:
    """The answers, what changed since the saved ones, and what will happen."""
    from rich.padding import Padding
    from rich.table import Table

    values = instance.values

    def changed(*keys: str) -> str:
        return " [yellow](changed)[/]" if old and any(old.get(k, "") != values.get(k, "") for k in keys) else ""

    server = values["INATRACE_SSH"] + (f"  [dim]{probe.system}[/]" if probe.system else "")
    if probe.reached and not probe.ready:
        server += ("  [yellow]dependencies missing[/] → [green]will install them[/]" if plan.install
                   else "  [yellow]dependencies missing[/] → [yellow]not installed now[/]")
    tls = {"acme": "Let's Encrypt", "acme-staging": "Let's Encrypt's staging (not trusted)"}.get(
        values["INATRACE_TLS"], "self-signed")
    front = values["INATRACE_FRONT"]
    if front == "cdn":
        front += f", client from {values['INATRACE_CDN_IP_HEADER']}"
    if values.get("INATRACE_ORIGIN_SECRET"):
        front += ", secret header required"
    mail = (f"via {values['INATRACE_MAIL_HOST']}:{values['INATRACE_MAIL_PORT']}, from "
            f"{values['INATRACE_MAIL_FROM']}" if values["INATRACE_MAIL_ENABLED"] == "true"
            else "off (an admin activates new users)")
    table = Table(show_header=False, box=None, padding=(0, 2), pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Server", server + changed("INATRACE_SSH"))
    table.add_row("Address", f"https://{values['INATRACE_SITE']}  [dim]{tls}[/]"
                  + changed("INATRACE_SITE", "INATRACE_TLS"))
    table.add_row("In front", front + changed("INATRACE_FRONT", "INATRACE_CDN_IP_HEADER",
                                              "INATRACE_ORIGIN_SECRET"))
    table.add_row("Swagger UI", values["INATRACE_SWAGGER"] + changed("INATRACE_SWAGGER"))
    for part in ("BACKEND", "FRONTEND"):
        table.add_row(part.title(), f"{values[f'INATRACE_{part}_IMAGE']}:{values[f'INATRACE_{part}_VERSION']}"
                      + changed(f"INATRACE_{part}_IMAGE", f"INATRACE_{part}_VERSION"))
    table.add_row("E-mail", mail + changed(*(k for k in values if k.startswith("INATRACE_MAIL_"))))
    table.add_row("Monitoring", ("on (Beszel: inatrace deploy dashboard)" if values["INATRACE_MONITORING"] == "on"
                                 else "off") + changed("INATRACE_MONITORING"))
    steps = [f"save {shown(instance.env_file)}"
             + ("" if old else " [dim](with new passwords)[/]")]
    if plan.install:
        steps.append(f"install the dependencies on {values['INATRACE_SSH']} "
                     f"[dim](deploy/prereqs/{plan.install}.sh, sudo)[/]")
    ui.section("Summary")

    def render() -> None:
        ui.out.print(Padding(table, (0, 0, 0, 2)))
        ui.out.print()
        ui.out.print("  [bold]Will do:[/]")
        for number, text in enumerate(steps, 1):
            ui.out.print(f"   {number}. {text}")
        for note in plan.notes:
            ui.out.print(f"   [dim]then {note}[/]")

    mail_on = values["INATRACE_MAIL_ENABLED"] == "true"
    settings = {
        "ssh": values["INATRACE_SSH"], "system": probe.system or None,
        "dependencies": ("ready" if probe.ready else "will install" if plan.install else "missing")
        if probe.reached else "unknown",
        "site": values["INATRACE_SITE"], "tls": values["INATRACE_TLS"], "front": values["INATRACE_FRONT"],
        "cdn_header": values.get("INATRACE_CDN_IP_HEADER") or None,
        "origin_secret": bool(values.get("INATRACE_ORIGIN_SECRET")),
        "swagger": values["INATRACE_SWAGGER"] == "on",
        **{part.lower(): f"{values[f'INATRACE_{part}_IMAGE']}:{values[f'INATRACE_{part}_VERSION']}"
           for part in ("BACKEND", "FRONTEND")},
        "mail": {"host": values["INATRACE_MAIL_HOST"], "port": values["INATRACE_MAIL_PORT"],
                 "username": values.get("INATRACE_MAIL_USERNAME") or None,
                 "from": values["INATRACE_MAIL_FROM"]} if mail_on else None,
        "monitoring": values["INATRACE_MONITORING"] == "on",
    }
    changes = sorted(k for k in {*old, *values} if old and old.get(k, "") != values.get(k, ""))
    ui.show("summary", {"settings": settings, "changed": changes,
                        "will_do": [ui.plain(step) for step in steps], "then": plan.notes}, render)


def wizard(name: str, given: dict[str, object] | None = None, auto_approve: bool = False,
           dry_run: bool = False) -> Instance | None:
    """Asks for an instance's settings (those not `given` as flags), doing only reads
    meanwhile; shows a summary and, once approved (or with `auto_approve`), saves them and
    installs Docker if asked. On an existing instance its values are the defaults, and
    the secrets are kept. None when not confirmed."""
    ask = Asker(given)
    instance = Instance(check_name(name))
    if instance.env_file.is_file():
        instance.values = config.parse(instance.env_file.read_text())
        ui.info(f"instance '{name}' exists: Enter keeps each value")
    old = dict(instance.values)
    values = instance.values

    remote, probe, plan = _ask_server(ask, values, old, name)
    _ask_address(ask, values, old, remote, probe)
    _ask_front(ask, values, old)
    _ask_images(ask, values, old)
    _ask_mail(ask, values, old)
    _ask_monitoring(ask, values, old)
    _ask_restore(ask, instance, plan)
    for key in SECRETS:
        values[key] = old.get(key) or new_secret()
    found = problems(values)
    if found:
        raise DeployError("\n  - ".join(["the answers do not add up:", *found]))

    _summary(instance, old, probe, plan)
    ui.blank()
    if dry_run:
        ui.info("dry run: nothing was changed")
        return None
    if not auto_approve and not ask.confirm("auto-approve", "Go ahead?", True, negatable=False):
        ui.info("nothing was changed")
        return None
    ui.blank()
    save(instance)
    ui.ok(f"saved {shown(instance.env_file)}")
    ui.info("keep a copy of it somewhere safe: it holds the passwords")
    if plan.install:
        try:
            install_prereqs(remote, plan.install)
        except DeployError as error:
            ui.fail(str(error))
            ui.info(f"fix it, then: inatrace deploy prepare {name}")
    ui.info("other settings (maps, backups): edit that file; see deploy/.env.example")
    command = f"inatrace deploy up {name}" + (f" --restore {plan.restore}" if plan.restore else "")
    ui.show("next", {"command": command}, lambda: ui.out.print(f"\n  Next: [bold cyan]{command}[/]"))
    return instance


def init(name: str, given: dict[str, object] | None = None, auto_approve: bool = False,
         dry_run: bool = False) -> int:
    wizard(name, given, auto_approve, dry_run)
    return 0
