"""The HTTP API through the gateway: public endpoints, registration with its
confirmation e-mail, authentication."""
import re

import pytest
# Errors the backend logs on a healthy run without optional integrations
# (exchange rate API key, GeoLite database) and a known Hibernate metamodel warning.
KNOWN_ERRORS = re.compile(r"HHH015007|exchange rate data|GeoLite|maxmind")


def test_openapi(stack, detail):
    """OpenAPI document is public, or closed with Swagger off"""
    status = stack.http.get(f"{stack.base_url}/v3/api-docs", timeout=10).status_code
    detail(f"Swagger {'on' if stack.swagger else 'off'}: HTTP {status}")
    assert status == (200 if stack.swagger else 404)


def test_anonymous_is_rejected(stack):
    """Protected endpoint rejects anonymous requests"""
    assert stack.http.get(f"{stack.base_url}/api/user/profile", timeout=10).status_code == 401


def test_confirmation_email(credentials, detail):
    """Registration sends the confirmation e-mail (Mailpit)"""
    if not credentials["mail"]:
        pytest.fail("no e-mail in Mailpit within 30s; a backend from your checkout needs mail "
                    "sent to Mailpit (docs/dev-stack.md → Running from your checkout)", pytrace=False)
    detail(f"to {credentials['email']}: {credentials['mail']['Subject']}")
    assert credentials["token"], "no confirm-email link in the e-mail"


def test_email_confirmed(credentials):
    """The e-mail's link confirms the address"""
    if not credentials["token"]:
        pytest.skip("no confirmation e-mail")
    assert credentials["confirmed"] == 200


def test_login_sets_cookie(session, detail):
    """Login sets the access token cookie"""
    detail("user activated in the database (needs an admin through the API)")
    assert any("accessToken" in name for name in session.cookies.keys())


def test_authenticated_request(stack, session, credentials):
    """Authenticated request returns the user's profile"""
    response = session.get(f"{stack.base_url}/api/user/profile", timeout=10)
    assert response.status_code == 200
    assert response.json()["data"]["email"] == credentials["email"]


def test_no_unexpected_errors(stack, backend, credentials):
    """No unexpected errors in the backend log during the run"""
    errors = [line for line in backend.logs(stack.started).splitlines()
              if " ERROR " in line and not KNOWN_ERRORS.search(line)]
    assert not errors, "\n".join(line[:200] for line in errors[:5])
