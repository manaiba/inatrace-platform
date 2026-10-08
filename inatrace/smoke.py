"""`inatrace smoke`: smoke-tests/ against the running dev stack.

The tests have dependencies of their own (pytest, requests, Playwright: the
`smoke` group in pyproject.toml), installed by uv on first use, as is the
Chromium build Playwright drives. Chromium's system libraries come with the dev
container's image; on your machine they are a requirement (docs/getting-started.md).
"""

import subprocess

from . import paths


def _uv(*args: str) -> list[str]:
    return ["uv", "run", "--quiet", "--locked", "--project", str(paths.ROOT), "--group", "smoke",
            *args]


def run(verbose: bool, lifecycle: bool, extra: list[str]) -> int:
    # A no-op once installed; the first time it downloads Chromium (~150 MB).
    if subprocess.run(_uv("playwright", "install", "chromium")).returncode:
        return 1
    args = ["-c", str(paths.SMOKE_TESTS / "pytest.ini"), "--rootdir", str(paths.SMOKE_TESTS)]
    if verbose:
        args += ["-v", "--log-cli-level=INFO"]
    if lifecycle:
        args.append("--lifecycle")
    # The tests' directory explicitly: pytest applies testpaths only when run from its rootdir.
    return subprocess.run(_uv("pytest", *args, *extra, str(paths.SMOKE_TESTS / "tests"))).returncode
