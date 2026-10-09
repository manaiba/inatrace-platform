"""Shared fixtures, the cleanup and the final report. See docs/smoke-tests.md.

Against a deployment (`inatrace deploy smoke`) only what reads runs: the tests that
create data (WRITES: a user, an upload, a restart) are left out."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
from collections import defaultdict
from pathlib import Path

import pytest
import requests
from rich.console import Console
from rich.table import Table

from common.mailpit import Mailpit
from common.stack import Service, Stack, target

log = logging.getLogger("smoke")

# Everything the tests create is named with this prefix, so the cleanup finds it.
PREFIX = "smoke-"
EMAIL_LIKE = f"{PREFIX}%@example.com"


def pytest_addoption(parser):
    parser.addoption("--lifecycle", action="store_true",
                     help="also stop and start the services that run from images")
    parser.addoption("--boot-timeout", type=int, default=180,
                     help="seconds to wait for the backend to answer")


# Fixtures that create data, and the tests that restart services.
WRITES = {"credentials", "session", "uploaded", "mailpit"}
_target: Stack | None = None


def the_target() -> Stack:
    global _target
    if _target is None:
        _target = target()
    return _target


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--lifecycle"):
        items[:] = [i for i in items if Path(i.fspath).stem != "07_lifecycle"]
    if the_target().writes:  # the dev stack: not the deployment's own checks
        kept = [i for i in items if Path(i.fspath).stem != "08_deployment"]
    else:  # a deployment: only what reads
        kept = [i for i in items if not WRITES & set(i.fixturenames) and Path(i.fspath).stem != "07_lifecycle"]
    config.hook.pytest_deselected(items=[i for i in items if i not in kept])
    items[:] = kept


# --- the stack ------------------------------------------------------------------------------

def cleanup(stack: Stack) -> None:
    """Best effort: removes what the smoke tests create, from this run or an earlier one."""
    def attempt(what: str, step) -> None:
        try:
            step()
        except Exception as error:  # noqa: BLE001 — a failed cleanup never fails the run
            log.warning("cleanup: %s failed: %s", what, error)

    backend = stack.service("backend")
    if backend:  # uploads on the image's volume; a checkout's storage path is its own
        def files() -> None:
            for key in stack.sql(f"select storageKey from Document where name like '{PREFIX}%'").split():
                backend.exec("find", "/data/storage", "-name", key, "-type", "f", "-delete")
        attempt("uploaded files", files)
    users = f"select id from User where email like '{EMAIL_LIKE}'"
    for table in ("ConfirmationToken", "AuthenticationToken"):
        attempt(table, lambda t=table: stack.sql(
            f"delete from {t} where user_id in (select id from ({users}) u)"))
    attempt("User_AUD", lambda: stack.sql(f"delete from User_AUD where email like '{EMAIL_LIKE}'"))
    attempt("User", lambda: stack.sql(f"delete from User where email like '{EMAIL_LIKE}'"))
    attempt("Document", lambda: stack.sql(f"delete from Document where name like '{PREFIX}%'"))
    attempt("e-mails", lambda: Mailpit(stack.base_url).delete(f"to:{PREFIX}"))


@pytest.fixture(scope="session")
def stack(request) -> Stack:
    stack = the_target()
    stack.check()
    stack.wait_backend(request.config.getoption("--boot-timeout"))
    stack.started = stack.now()
    if not stack.writes:
        yield stack
        return
    cleanup(stack)
    yield stack
    cleanup(stack)


def writing(stack: Stack) -> None:
    """A guard in each fixture that creates data, should one be asked for anyway."""
    if not stack.writes:
        raise RuntimeError(f"{stack.name} is only read: nothing may be created there")


@pytest.fixture(scope="session")
def backend(stack) -> Service:
    service = stack.service("backend")
    if not service:
        pytest.skip("the backend runs from your checkout, not an image")
    return service


@pytest.fixture(scope="session")
def frontend(stack) -> Service:
    service = stack.service("frontend")
    if not service:
        pytest.skip("the frontend runs from your checkout, not an image")
    return service


# --- a user and a file ----------------------------------------------------------------------

@pytest.fixture(scope="session")
def mailpit(stack) -> Mailpit:
    writing(stack)
    return Mailpit(stack.base_url)


@pytest.fixture(scope="session")
def credentials(stack, mailpit) -> dict:
    """A user registered through the API, its e-mail confirmed with the link the backend
    sent to Mailpit, then activated in the database (activation needs an admin)."""
    writing(stack)
    user = {"email": f"{PREFIX}{secrets.token_hex(4)}@example.com", "password": "smoke-test-password"}
    response = stack.http.post(f"{stack.base_url}/api/user/register",
                             json={**user, "name": "Smoke", "surname": "Test"}, timeout=10)
    response.raise_for_status()

    mail = mailpit.find(f'to:"{user["email"]}" subject:"INATrace registration"')
    token = None
    if mail:
        found = re.search(r"confirm-email/([\w-]+)", mailpit.html(mail["ID"]))
        token = found.group(1) if found else None
    confirmed = None
    if token:
        confirmed = stack.http.post(f"{stack.base_url}/api/user/confirm_email",
                                  json={"token": token}, timeout=10).status_code
    stack.sql(f"update User set status='ACTIVE' where email='{user['email']}'")
    return {**user, "mail": mail, "token": token, "confirmed": confirmed}


@pytest.fixture(scope="session")
def session(stack, credentials) -> requests.Session:
    writing(stack)
    return stack.login(credentials["email"], credentials["password"])


@pytest.fixture(scope="session")
def uploaded(stack, session) -> dict:
    """A random file uploaded through the API; the storage tests and the restart test use it."""
    writing(stack)
    name = f"{PREFIX}{secrets.token_hex(4)}.bin"
    content = secrets.token_bytes(64 * 1024)
    response = session.post(f"{stack.base_url}/api/common/document", timeout=30,
                            files={"file": (name, content, "application/octet-stream")})
    # The API returns a per-session key kept in memory; the database has the real one.
    key = (response.json().get("data") or {}).get("storageKey") if response.ok else None
    stored = stack.sql(f"select storageKey from Document where name='{name}'") if key else None
    return {"name": name, "content": content, "key": key, "stored": stored,
            "status": response.status_code}


# --- the browser ----------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as error:
            if "error while loading shared libraries" in str(error):
                missing = re.search(r"lib[\w.+-]+\.so[\w.]*", str(error))
                pytest.fail(f"Chromium's system libraries are missing ({missing.group(0) if missing else '?'}"
                            "): docs/getting-started.md → Installing the requirements", pytrace=False)
            raise
        yield browser
        browser.close()


@pytest.fixture
def page(browser, stack):
    """A fresh browser page; `page.errors` collects JavaScript errors."""
    context = stack.browser_context(browser)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(f"pageerror: {error}"))
    page.on("console", lambda message: message.type == "error"
            and page.errors.append(f"console: {message.text}"))
    yield page
    context.close()


# --- report ---------------------------------------------------------------------------------

@pytest.fixture
def detail(record_property):
    """Adds a short note to the test's line in the report."""
    return lambda text: record_property("detail", text)


SECTIONS = {
    "01_images": "Images", "02_database": "Database", "03_api": "API",
    "04_storage": "File storage", "05_web": "Web", "06_browser": "Browser",
    "07_lifecycle": "Lifecycle", "08_deployment": "Deployment",
}
STYLE = {"passed": "[green]✔ pass[/]", "failed": "[red]✘ fail[/]", "skipped": "[yellow]↷ skip[/]",
         "error": "[red]✘ error[/]"}
results: dict[str, list[tuple[str, str, str, float]]] = defaultdict(list)
# `inatrace --json smoke`: a JSON event a line on this pipe, for each check and at the end.
EVENTS = os.fdopen(int(os.environ["SMOKE_EVENTS_FD"]), "w", buffering=1) \
    if os.environ.get("SMOKE_EVENTS_FD") else None


def emit(event: str, **fields) -> None:
    if EVENTS:
        EVENTS.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    failed_setup = report.when == "setup" and report.outcome != "passed"
    if report.when != "call" and not failed_setup:
        return
    outcome = "error" if failed_setup and report.failed else report.outcome
    detail = dict(item.user_properties).get("detail", "")
    if report.skipped:
        detail = report.longrepr[2].removeprefix("Skipped: ")
    elif report.failed:
        detail = (report.longrepr.reprcrash.message if hasattr(report.longrepr, "reprcrash")
                  else str(report.longrepr)).splitlines()[0][:150]
    name = (item.function.__doc__ or item.name).strip().splitlines()[0]
    module = Path(item.fspath).stem
    results[module].append((name, outcome, detail, report.duration))
    emit("test", area=SECTIONS.get(module, module), check=name, outcome=outcome, detail=detail,
         seconds=round(report.duration, 1))


def pytest_collectreport(report):
    if report.failed:
        emit("error", message=f"collecting {report.nodeid}: {str(report.longrepr).splitlines()[-1]}")


def pytest_sessionfinish(session, exitstatus):
    counts = defaultdict(int)
    for rows in results.values():
        for _, outcome, _, _ in rows:
            counts[outcome] += 1
    emit("report", target=the_target().name, counts=dict(counts), ok=exitstatus == 0,
         lifecycle=session.config.getoption("--lifecycle"))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not results:
        return
    console = Console(width=None if sys.stdout.isatty() else 140)
    table = Table(title=f"Smoke tests — {the_target().name}", title_justify="left",
                  header_style="bold")
    table.add_column("Area")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    table.add_column("Time", justify="right")
    counts = defaultdict(int)
    for module in sorted(results):
        rows = results[module]
        for i, (name, outcome, detail, duration) in enumerate(rows):
            counts[outcome] += 1
            table.add_row(SECTIONS.get(module, module) if i == 0 else "", name, STYLE[outcome],
                          detail, f"{duration:.1f}s", end_section=i == len(rows) - 1)
    console.print()
    console.print(table)
    summary = ", ".join(f"{n} {k}" for k, n in counts.items())
    console.print(f"[bold {'green' if exitstatus == 0 else 'red'}]{summary}[/]")
    if not the_target().writes:
        console.print("Only what reads ran: the checks that create data (a user, an upload) or "
                      "restart services do not run against a deployment.")
    elif not config.getoption("--lifecycle"):
        console.print("Lifecycle checks (stop/start of the images) not run: --lifecycle.")
