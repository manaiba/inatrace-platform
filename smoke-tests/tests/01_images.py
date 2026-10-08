"""What the images contain, checked without touching the running services.
Only for the parts that run from an image; the others are skipped."""
import pytest


# --- backend ---

def test_backend_non_root(backend, detail):
    """Backend runs as a non-root user"""
    uid = backend.image.shell("id -u").stdout.strip()
    detail(f"{backend.image.name}: user {backend.image.config.get('User') or 'root'}, uid {uid}")
    assert uid not in ("", "0")


def test_backend_has_no_configuration(backend):
    """Backend image contains no application.properties"""
    found = backend.image.shell("find / -xdev -name application.properties 2>/dev/null").stdout.split()
    assert not found, f"configuration baked into the image: {found}"


def test_backend_runtime_files(backend):
    """Backend ships fonts/ and import/countries.csv"""
    assert backend.image.shell("test -d /app/fonts && test -s /app/import/countries.csv").returncode == 0


def test_backend_storage_owner(backend):
    """/data/storage is owned by the app user"""
    owner = backend.image.shell("stat -c %U /data/storage").stdout.strip()
    assert owner == backend.image.config.get("User"), f"/data/storage owned by {owner or 'nobody (missing)'}"


def test_backend_version(backend, detail):
    """Backend embeds its version (build-info)"""
    line = backend.image.shell("grep ^build.version= /app/META-INF/build-info.properties").stdout
    version = line.strip().partition("=")[2]
    detail(version)
    expected = backend.expected_version
    assert version == expected if expected else version


def test_backend_labels(backend, detail):
    """Backend OCI labels: version, revision, source"""
    image = backend.image
    # CI sets revision; base image labels (e.g. Ubuntu's version) do not count.
    if not image.label("revision"):
        pytest.skip("no CI labels (image not built by CI)")
    detail(f"{image.label('version')} @ {image.label('revision')[:7]}, {image.label('source')}")
    assert image.label("source")
    expected = backend.expected_version
    assert image.label("version") == expected if expected else True


# --- frontend ---

def test_frontend_non_root(frontend, detail):
    """Frontend runs as a non-root user"""
    uid = frontend.image.shell("id -u").stdout.strip()
    detail(f"{frontend.image.name}: user {frontend.image.config.get('User') or 'root'}, uid {uid}")
    assert uid not in ("", "0")


def test_frontend_port(frontend, detail):
    """Frontend exposes port 8080"""
    ports = sorted((frontend.image.config.get("ExposedPorts") or {}).keys())
    detail(", ".join(ports))
    assert "8080/tcp" in ports


def test_frontend_env_template(frontend):
    """Frontend ships the runtime settings template"""
    assert frontend.image.shell("test -s /app/assets/env.template.js").returncode == 0


def test_frontend_version(frontend, detail):
    """Frontend embeds its version"""
    expected = frontend.expected_version
    if not expected:
        pytest.skip(f"the tag ({frontend.image.tag}) is not a version")
    found = frontend.image.shell(f"grep -l '{expected}' /app/main-es2015.*.js").stdout.strip()
    detail(expected)
    assert found, f"{expected} not found in the main bundle"


def test_frontend_labels(frontend, detail):
    """Frontend OCI labels: version, revision, source"""
    image = frontend.image
    # Our CI points source at an INATrace repository; the nginx base image has its own labels.
    if "inatrace" not in image.label("source"):
        pytest.skip(f"no CI labels (image not built by CI; source: {image.label('source') or 'none'})")
    detail(f"{image.label('version')} @ {image.label('revision')[:7]}, {image.label('source')}")
    assert image.label("revision")
    expected = frontend.expected_version
    assert image.label("version") == expected if expected else True
