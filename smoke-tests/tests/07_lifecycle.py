"""Stop and start of the services that run from images. Opt-in (--lifecycle):
it interrupts them. Runs last; leaves them running."""
import requests

from common.devstack import now


def test_backend_stops_on_sigterm(backend, detail):
    """Backend stops promptly on SIGTERM"""
    took, code = backend.stop(timeout=30)
    detail(f"{took:.0f}s, exit code {code}")
    assert took < 30 and code in ("0", "143")


def test_backend_starts_again(stack, backend, request, detail):
    """Backend starts again"""
    backend.start()
    detail(f"answering after {stack.wait_backend(request.config.getoption('--boot-timeout')):.0f}s")


def test_login_after_restart(stack, credentials):
    """Login works after the restart"""
    session = stack.login(credentials["email"], credentials["password"])
    assert session.get(f"{stack.base_url}/api/user/profile", timeout=10).status_code == 200


def test_file_survives_restart(backend, uploaded):
    """Uploaded file survives the restart"""
    assert uploaded["stored"] and backend.stored_file(uploaded["stored"]) == uploaded["content"]


def test_backend_not_oom_killed(backend):
    """Backend was not killed for memory"""
    assert backend.inspect("{{.State.OOMKilled}}") == "false"


def test_frontend_stops_on_sigterm(frontend, detail):
    """Frontend stops promptly on SIGTERM"""
    took, code = frontend.stop(timeout=10)
    detail(f"{took:.0f}s, exit code {code}")
    assert took < 10 and code in ("0", "143")


def test_frontend_starts_again(stack, frontend, detail):
    """Frontend starts again, env.js generated again"""
    since = now()
    frontend.start()
    detail(f"serving after {stack.wait_frontend(60):.0f}s")
    env_js = requests.get(f"{stack.base_url}/assets/env.js", timeout=10).text
    assert f"['environmentName'] = '{frontend.env['ENVIRONMENT_NAME']}'" in env_js
    assert "[emerg]" not in frontend.logs(since)
