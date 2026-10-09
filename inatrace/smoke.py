"""`inatrace smoke`: smoke-tests/ against the running dev stack.

The tests have dependencies of their own (pytest, requests, Playwright: the
`smoke` group in pyproject.toml), installed by uv on first use, as is the
Chromium build Playwright drives. Chromium's system libraries come with the dev
container's image; on your machine they are a requirement (docs/getting-started.md).
"""

import json
import os
import subprocess
import sys

from . import paths, ui


def _uv(*args: str) -> list[str]:
    return ["uv", "run", "--quiet", "--locked", "--project", str(paths.ROOT), "--group", "smoke",
            *args]


def run(verbose: bool, lifecycle: bool, extra: list[str], deployment: dict | None = None) -> int:
    """Against the dev stack, or a deployment: its details (smoke-tests/common/stack.py)."""
    env = {**os.environ, **({"SMOKE_DEPLOYMENT": json.dumps(deployment)} if deployment else {})}
    # A no-op once installed; the first time it downloads Chromium (~150 MB).
    out = sys.stderr if ui.json_mode() else None
    if subprocess.run(_uv("playwright", "install", "chromium"), stdout=out).returncode:
        raise ui.StepError("could not install Chromium for Playwright")
    args = ["-c", str(paths.SMOKE_TESTS / "pytest.ini"), "--rootdir", str(paths.SMOKE_TESTS)]
    if verbose:
        args += ["-v", "--log-cli-level=INFO"]
    if lifecycle:
        args.append("--lifecycle")
    # The tests' directory explicitly: pytest applies testpaths only when run from its rootdir.
    command = _uv("pytest", *args, *extra, str(paths.SMOKE_TESTS / "tests"))
    if not ui.json_mode():
        return subprocess.run(command, env=env).returncode
    # conftest.py writes its events to the pipe SMOKE_EVENTS_FD names; pytest's own
    # output goes to stderr.
    read, write = os.pipe()
    process = subprocess.Popen(command, stdout=sys.stderr, pass_fds=(write,),
                               env={**env, "SMOKE_EVENTS_FD": str(write)})
    os.close(write)
    try:
        with os.fdopen(read) as events:
            for line in events:
                sys.stdout.write(line)
                sys.stdout.flush()
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
