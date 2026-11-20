import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inatrace import paths, ui
from inatrace.deploy import drift, instance as instance_
from inatrace.deploy.common import DeployError
from inatrace.tests.deploy_fixtures import VALID


class FakeRemote:
    """A server in a temporary home: the commands run here, with bash, in ~/inatrace."""

    destination = "admin@203.0.113.10"

    def __init__(self, home: Path) -> None:
        self.home = home
        self.dir = home / "inatrace"
        self.env = {**os.environ, "HOME": str(home)}

    def output(self, command: str) -> str | None:
        result = subprocess.run(["bash", "-c", command], env=self.env, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def quiet(self, command: str, input: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", f"cd ~/inatrace && {command}"], env=self.env, input=input,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def fetch(self, names: list[str], into: Path) -> None:
        for name in names:
            shutil.copy(self.dir / name, into / Path(name).name)


class DriftTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(ui.use_json, False)
        ui.use_json(True)  # nothing asked; events instead of drawing
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(paths, "DEPLOY_INSTANCES", tmp / "instances"))
        self.enterContext(mock.patch.object(drift, "platform", return_value=("a" * 40, False)))
        self.enterContext(mock.patch.object(drift, "platform_problem", return_value=None))
        self.instance = instance_.Instance("demo", dict(VALID))
        instance_.save(self.instance)
        self.remote = FakeRemote(tmp / "home")
        self.remote.dir.mkdir(parents=True)
        self.enterContext(mock.patch.object(drift, "deployed", return_value=self.remote))

    def deploy(self) -> None:
        """What `up` leaves there: the instance's files, and its record."""
        for name, path in instance_.server_files(self.instance).items():
            (self.remote.dir / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, self.remote.dir / name)
        drift.record(self.remote, self.instance, drift.settings_names(self.instance))

    def set_there(self, key: str, value: str) -> None:
        env = self.remote.dir / ".env"
        env.write_text(env.read_text() + f"{key}={value}\n")

    def set_here(self, key: str, value: str) -> None:
        self.instance.env_file.write_text(self.instance.env_file.read_text() + f"{key}={value}\n")

    def test_a_new_server_has_nothing_to_compare(self):
        self.assertEqual(drift.check(self.remote, self.instance), [])

    def test_the_same_settings_pass_without_a_record(self):
        self.deploy()
        (self.instance.dir / drift.RECORD).unlink()
        self.assertEqual(drift.check(self.remote, self.instance), [])

    def test_a_change_here_passes(self):
        self.deploy()
        self.set_here("INATRACE_SWAGGER", "on")
        self.assertEqual(drift.check(self.remote, self.instance), [])

    def test_a_change_there_stops_naming_the_keys_not_the_values(self):
        self.deploy()
        self.set_there("INATRACE_MAPBOX_ACCESS_TOKEN", "pk.secret-value")
        problems = drift.check(self.remote, self.instance)
        self.assertEqual(len(problems), 1)
        self.assertIn("INATRACE_MAPBOX_ACCESS_TOKEN (only there)", problems[0])
        self.assertNotIn("secret-value", problems[0])

    def test_without_a_record_a_difference_stops(self):
        self.deploy()
        (self.instance.dir / drift.RECORD).unlink()
        self.set_here("INATRACE_SWAGGER", "on")
        problems = drift.check(self.remote, self.instance)
        self.assertIn("no record", problems[0])
        self.assertIn(".env: INATRACE_SWAGGER", problems[0])

    def test_an_extra_file_there_counts(self):
        self.deploy()
        (self.remote.dir / "compose.override.yaml").write_text("services: {}\n")
        record = json.loads((self.remote.dir / drift.RECORD).read_text())
        record["files"].append("compose.override.yaml")
        (self.remote.dir / drift.RECORD).write_text(json.dumps(record))
        self.assertIn("compose.override.yaml (only there)", drift.check(self.remote, self.instance)[0])

    def test_stop(self):
        with self.assertRaises(DeployError) as raised:
            drift.stop([f"{drift.MOVED}: .env: A"], "demo", dry_run=False, overwrite=False)
        self.assertEqual(raised.exception.flag, "--overwrite")
        self.assertIn("inatrace deploy pull demo", str(raised.exception))
        with self.assertRaises(DeployError) as raised:
            drift.stop(["the server was deployed from platform commit 12345678 …"], "demo", False, False)
        self.assertNotIn("deploy pull", str(raised.exception))
        drift.stop(["it moved"], "demo", dry_run=True, overwrite=False)
        drift.stop(["it moved"], "demo", dry_run=False, overwrite=True)
        drift.stop([], "demo", dry_run=False, overwrite=False)

    def test_pull_brings_them_and_then_up_passes(self):
        self.deploy()
        self.set_there("INATRACE_SWAGGER", "on")
        self.assertEqual(drift.pull("demo", auto_approve=True), 0)
        self.assertIn("INATRACE_SWAGGER=on", self.instance.env_file.read_text())
        self.assertEqual(self.instance.env_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(drift.check(self.remote, self.instance), [])

    def test_pull_dry_run_changes_nothing(self):
        self.deploy()
        before = self.instance.env_file.read_text()
        self.set_there("INATRACE_SWAGGER", "on")
        self.assertEqual(drift.pull("demo", dry_run=True), 0)
        self.assertEqual(self.instance.env_file.read_text(), before)

    def test_pull_without_a_terminal_needs_auto_approve(self):
        self.deploy()
        self.set_there("INATRACE_SWAGGER", "on")
        with self.assertRaises(DeployError) as raised:
            drift.pull("demo")
        self.assertEqual(raised.exception.flag, "--auto-approve")

    def test_the_record_is_not_sent(self):
        self.deploy()
        self.assertTrue((self.instance.dir / drift.RECORD).is_file())
        self.assertNotIn(drift.RECORD, instance_.server_files(self.instance))


class PlatformTest(unittest.TestCase):
    def git(self, missing: bool = False, ancestor: bool = True):
        def run(*args):
            code = {"cat-file": 1 if missing else 0, "merge-base": 0 if ancestor else 1}.get(args[0], 0)
            return subprocess.CompletedProcess(args, code, "", "")
        return mock.patch.object(drift, "_git", side_effect=run)

    def test_the_same_or_an_older_commit_passes(self):
        with mock.patch.object(drift, "platform", return_value=("b" * 40, False)), self.git():
            self.assertIsNone(drift.platform_problem({"platform": "b" * 40}))
            self.assertIsNone(drift.platform_problem({"platform": "a" * 40}))
            self.assertIsNone(drift.platform_problem(None))

    def test_a_commit_this_checkout_lacks(self):
        with mock.patch.object(drift, "platform", return_value=("b" * 40, False)), self.git(missing=True):
            self.assertIn("does not have", drift.platform_problem({"platform": "a" * 40}))

    def test_a_commit_off_this_history(self):
        with mock.patch.object(drift, "platform", return_value=("b" * 40, False)), self.git(ancestor=False):
            self.assertIn("not in this checkout's history", drift.platform_problem({"platform": "a" * 40}))


class DifferencesTest(unittest.TestCase):
    def test_keys_only(self):
        here = "A=1\n# B=2\nC = 3\nx.y = old\n"
        there = "A=1\nB=2\nC = 4\n"
        self.assertEqual(drift.differences(here, there), ["B (only there)", "C", "x.y (only here)"])
        self.assertEqual(drift.differences("A=1\n", "A=1\n\n# note\n"), ["comments or spacing only"])


if __name__ == "__main__":
    unittest.main()
