"""`deploy admin`: the first admin (registered here if need be), and a first company for them."""

import json
import secrets
import shlex
import string

from rich.markup import escape

from .. import ui
from .common import DeployError, would
from .instance import EMAIL, load
from .remote import deployed


def _sql_text(value: str) -> str:
    """A string literal for MySQL."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


MIN_PASSWORD = 8
_MYSQL = ("docker compose exec -T -u mysql mysql sh -c "
          + shlex.quote('exec mysql -N -B --default-character-set=utf8mb4 -uroot '
                        '-p"$MYSQL_ROOT_PASSWORD" inatrace 2>/dev/null'))


def _password(given: str | None, generate: bool) -> tuple[str, bool]:
    """The new user's password, and whether it was generated (then shown, once): given
    (its flag's environment variable), generated, or asked twice, hidden."""
    if generate:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(20)), True
    if given is not None:
        if len(given) < MIN_PASSWORD:
            raise DeployError(f"the password needs {MIN_PASSWORD} characters at least")
        return given, False
    try:
        while True:
            first = ui.ask_text(f"Password for the new user ({MIN_PASSWORD} characters at least)", hide=True)
            if len(first) < MIN_PASSWORD:
                ui.warn(f"{MIN_PASSWORD} characters at least")
                continue
            if ui.ask_text("The same again", hide=True) == first:
                return first, False
            ui.warn("they differ; once more")
    except ui.NoAnswer:
        raise DeployError("no password for the new user: INATRACE_DEPLOY_ADMIN_PASSWORD, or --generate-password",
                          flag="--generate-password") from None


def _register(remote, email: str, password: str, first_name: str, last_name: str) -> None:
    """Registers the user as the site's form would, from inside the backend's container:
    no DNS, CDN or secret header in the way, and the password only on stdin."""
    body = json.dumps({"email": email, "password": password, "name": first_name, "surname": last_name})
    result = remote.quiet("docker compose exec -T backend curl -s -X POST -H 'Content-Type: application/json' "
                          "--data-binary @- http://127.0.0.1:8080/api/user/register", body)
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        answer = {}
    if result.returncode or answer.get("status") != "OK":
        raise DeployError(f"registering {email} failed: {answer.get('errorMessage') or result.stdout.strip()[:200]}")


def admin(name: str, email: str, company: str | None = None, dry_run: bool = False, create: bool = False,
          password: str | None = None, generate: bool = False, first_name: str = "INATrace",
          last_name: str = "Admin") -> int:
    """Makes a registered user a system admin, active: the first admin has no one to
    activate them. With `create`, registers them first when they are not yet. With
    `company`, also makes that company (active) with them as its admin: a user without
    one is stuck at the frontend's company picker, and on a new installation there is
    none to pick. Safe to repeat: nothing is made twice."""
    if not EMAIL.match(email):
        raise DeployError(f"'{email}' is not an e-mail address")
    if company is not None and not company.strip():
        raise DeployError("--company is empty")
    if (password is not None or generate) and not create:
        raise DeployError("a password is for a new user: add --create")
    instance = load(name)
    user, firm = _sql_text(email), _sql_text(company.strip()) if company else "NULL"
    # What is there now, one tab-separated line each: the user, the company, the link.
    look = (f"SELECT 'user', id, role, status FROM User WHERE email={user}; "
            f"SELECT 'company', id, status FROM Company WHERE name={firm} ORDER BY id LIMIT 1; "
            f"SELECT 'member', cu.role FROM CompanyUser cu JOIN User u ON u.id = cu.user_id "
            f"JOIN Company c ON c.id = cu.company_id WHERE u.email={user} AND c.name={firm}; "
            f"SELECT 'companies', COUNT(*) FROM CompanyUser cu JOIN User u ON u.id = cu.user_id "
            f"WHERE u.email={user};")
    change = (f"SET @u = (SELECT id FROM User WHERE email={user}); "
              "UPDATE User SET role='SYSTEM_ADMIN', status='ACTIVE' WHERE id=@u; ")
    if company:
        change += (f"INSERT INTO Company (entityVersion, name, status) SELECT 0, {firm}, 'ACTIVE' "
                   f"FROM DUAL WHERE @u IS NOT NULL AND NOT EXISTS (SELECT 1 FROM Company WHERE name={firm}); "
                   f"SET @c = (SELECT id FROM Company WHERE name={firm} ORDER BY id LIMIT 1); "
                   "INSERT INTO CompanyUser (entityVersion, role, company_id, user_id) "
                   "SELECT 0, 'COMPANY_ADMIN', @c, @u FROM DUAL WHERE @u IS NOT NULL AND @c IS NOT NULL "
                   "AND NOT EXISTS (SELECT 1 FROM CompanyUser WHERE company_id=@c AND user_id=@u); ")
    remote = deployed(instance)
    shown_password = None
    if create:
        found = remote.quiet(_MYSQL, f"SELECT id FROM User WHERE email={user};")
        if found.returncode:
            raise DeployError(f"on {remote.destination}: {found.stdout.strip() or 'the database did not answer'}")
        if not found.stdout.strip():
            if dry_run:
                would(f"register {escape(email)} ({escape(first_name)} {escape(last_name)}), "
                      f"{'with a generated password' if generate else 'with the password given'}",
                      f"make {escape(email)} an active system admin",
                      *([f"create or join company {escape(repr(company))}, as its admin"] if company else []))
                return 0
            secret, generated = _password(password, generate)
            with ui.doing(f"registering {email}"):
                _register(remote, email, secret, first_name, last_name)
            shown_password = secret if generated else None
        else:
            ui.info(f"{email} is registered already: its password stays")
    result = remote.quiet(_MYSQL, look if dry_run else f"START TRANSACTION; {look} {change} COMMIT;")
    if result.returncode:
        raise DeployError(f"on {remote.destination}: {result.stdout.strip() or 'the database did not answer'}")
    found = {fields[0]: fields[1:] for fields in (line.split("\t") for line in result.stdout.splitlines())}
    if "user" not in found:
        ui.fail(f"no user {email}: --create registers them, or https://{instance.get('INATRACE_SITE')}/en/register")
        return 1
    _, role, status = found["user"]
    was_admin = (role, status) == ("SYSTEM_ADMIN", "ACTIVE")
    if company:
        existing, member = found.get("company"), found.get("member")
        company_plan, company_done = (
            (f"add {email} to company {company!r} as its admin", f"added to company {company!r} as its admin")
            if existing and not member else
            (f"keep company {company!r}: {email} is in it", f"company {company!r}: already in it")
            if existing else
            (f"create company {company!r}, active, with {email} as its admin",
             f"created company {company!r}, active, with {email} as its admin"))
    stuck = not company and found.get("companies", ["0"])[0] == "0"
    if dry_run:
        if stuck:
            _warn_no_company(name, email)
        would(f"{'keep' if was_admin else 'make'} {escape(email)} an active system admin "
               f"[dim](now {role} {status})[/]", *([escape(company_plan)] if company else []))
        return 0
    ui.ok(f"{email} {'was already' if was_admin else 'is now'} an active system admin")
    if company:
        ui.ok(company_done)
        ui.info("log out and in again in the browser to pick it")
    if stuck:
        _warn_no_company(name, email)
    if shown_password:
        ui.show("password", {"email": email, "password": shown_password},
                lambda: ui.out.print(f"  [bold]Password:[/] {shown_password}  [dim](shown once: keep it, or "
                                     "change it in the site)[/]"))
    return 0


def _warn_no_company(name: str, email: str) -> None:
    ui.warn(f"{email} belongs to no company, and cannot get past the login without one: "
            f"inatrace deploy admin {name} {email} --company \"<name>\"")
