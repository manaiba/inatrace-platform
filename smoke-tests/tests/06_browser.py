"""The app in a real (headless) browser, talking to the backend."""


def open_login(page, stack):
    page.goto(f"{stack.base_url}/en/login")
    page.get_by_placeholder("Enter your username").wait_for(timeout=60_000)
    cookies = page.get_by_role("button", name="OK")
    if cookies.count():
        cookies.click()


def test_login_page(page, stack, detail):
    """Login page renders without JavaScript errors"""
    open_login(page, stack)
    detail(page.title())
    assert not page.errors, "\n".join(page.errors[:5])


def test_runtime_settings_in_app(page, stack, detail):
    """The app sees its runtime settings"""
    open_login(page, stack)
    env = page.evaluate("window.env")
    assert isinstance(env, dict), "window.env is missing (assets/env.js not loaded)"
    if stack.service("frontend"):  # the image fills them in; a checkout's env.js leaves them empty
        detail(f"environmentName={env.get('environmentName')}")
        assert env.get("environmentName")
    else:
        detail("window.env loaded (empty values in a checkout)")


def test_login(page, stack, credentials, detail):
    """Logging in through the UI reaches the app"""
    open_login(page, stack)
    page.get_by_placeholder("Enter your username").fill(credentials["email"])
    page.get_by_placeholder("Enter your password").fill(credentials["password"])
    page.get_by_role("button", name="Login").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
    detail(page.url.removeprefix(stack.base_url))
    assert not page.errors, "\n".join(page.errors[:5])
