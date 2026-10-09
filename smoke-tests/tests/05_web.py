"""The site through the gateway."""
import re


def test_index(stack, detail):
    """Serves index.html"""
    response = stack.http.get(f"{stack.base_url}/", timeout=10)
    detail(f"HTTP {response.status_code}, {len(response.content)} bytes")
    assert response.status_code == 200 and "<app-root" in response.text


def test_deep_link(stack):
    """Deep links fall back to index.html"""
    # As a browser asks for a page: `ng serve` falls back only for text/html.
    response = stack.http.get(f"{stack.base_url}/en/login", headers={"Accept": "text/html"}, timeout=10)
    assert response.status_code == 200 and "<app-root" in response.text


def test_bundles(stack, detail):
    """JavaScript bundles referenced by index.html load"""
    index = stack.http.get(f"{stack.base_url}/", timeout=10).text
    scripts = re.findall(r'src="([^"]+\.js)"', index)
    statuses = {script: stack.http.get(f"{stack.base_url}/{script.lstrip('/')}", timeout=30).status_code
                for script in scripts}
    detail(f"{len(scripts)} scripts")
    assert scripts and all(code == 200 for code in statuses.values()), statuses


def test_runtime_settings(stack, frontend, detail):
    """env.js is generated from the image's environment"""
    env = frontend.env
    env_js = stack.http.get(f"{stack.base_url}/assets/env.js", timeout=10).text
    detail(f"ENVIRONMENT_NAME={env['ENVIRONMENT_NAME']}, APP_BASE_URL={env['APP_BASE_URL']}")
    assert f"['environmentName'] = '{env['ENVIRONMENT_NAME']}'" in env_js
    assert f"['appBaseUrl'] = '{env['APP_BASE_URL']}'" in env_js
    assert "${" not in env_js, "unsubstituted placeholders left in env.js"


def test_nginx_as_non_root(frontend, detail):
    """nginx runs as a non-root user"""
    users = frontend.exec("ps", "-o", "user=").stdout.split()
    detail(", ".join(sorted(set(users))))
    assert users and "root" not in users


def test_no_nginx_errors(stack, frontend):
    """No errors in the frontend log during the run"""
    errors = [line for line in frontend.logs(stack.started).splitlines()
              if "[emerg]" in line or "[error]" in line]
    assert not errors, "\n".join(errors[:5])
