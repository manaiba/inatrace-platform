import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inatrace import repos, ui


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()


class ParseTest(unittest.TestCase):
    def test_lines(self):
        text = """# header
            git@github.com:agstack/inatrace-backend.git
            https://github.com/agstack/inatrace.git   docs
            /some/local/repo
        """
        self.assertEqual(repos.parse(text), [
            repos.Repo("git@github.com:agstack/inatrace-backend.git", "inatrace-backend"),
            repos.Repo("https://github.com/agstack/inatrace.git", "docs"),
            repos.Repo("/some/local/repo", "repo"),
        ])

    def test_fork_key(self):
        self.assertEqual(repos.fork_key("inatrace-backend"), "INATRACE_FORK_INATRACE_BACKEND")
        self.assertEqual(repos.fork_key("a.b"), "INATRACE_FORK_A_B")

    def test_missing_list(self):
        with self.assertRaises(ui.StepError):
            repos.load(Path("/nonexistent/repos.txt"))


class SyncTest(unittest.TestCase):
    """Against local repos standing in for GitHub."""

    def setUp(self):
        # Hermetic git: no global or system config (signing, hooks, URL rewrites).
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull,
                                           "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.upstream = root / "upstream.git"
        self.fork = root / "fork.git"
        seed = root / "seed"
        git("init", "-q", "-b", "main", seed)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
            "-m", "init", cwd=seed)
        git("clone", "-q", "--bare", seed, self.upstream)
        git("clone", "-q", "--bare", seed, self.fork)
        self.dir = root / "repos"
        self.dir.mkdir()
        # fork_url() builds a GitHub SSH URL; point it at the local fork instead.
        self._fork_url = repos.fork_url
        repos.fork_url = lambda fork: str(self.fork)

    def tearDown(self):
        repos.fork_url = self._fork_url
        self.tmp.cleanup()

    def sync(self, forks=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            repos.sync([repos.Repo(str(self.upstream), "app")], self.dir, forks or {})

    def remote(self, name):
        return git("-C", self.dir / "app", "config", "--get", f"remote.{name}.url")

    def test_status_after_fetch(self):
        self.sync()
        app = self.dir / "app"
        self.assertEqual(repos.status(app), "on main → origin/main: up to date")
        # Someone pushes upstream; sync fetches but never merges.
        other = Path(self.tmp.name) / "other"
        git("clone", "-q", self.upstream, other)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
            "-m", "upstream change", cwd=other)
        git("-C", other, "push", "-q")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            repos.sync([repos.Repo(str(self.upstream), "app")], self.dir, {})
        self.assertIn("app: fetching origin", out.getvalue())
        self.assertIn("app: on main → origin/main: 1 behind", out.getvalue())

    def test_clone_then_fetch(self):
        self.sync()
        self.assertEqual(self.remote("origin"), str(self.upstream))
        self.sync()  # a second run only fetches

    def test_clone_from_fork(self):
        self.sync({"app": "me/app"})
        self.assertEqual(self.remote("origin"), str(self.fork))
        self.assertEqual(self.remote("upstream"), str(self.upstream))
        self.assertEqual(git("-C", self.dir / "app", "config", "remote.upstream.pushurl"),
                         "DISABLED")

    def test_existing_clone_switches_to_fork(self):
        self.sync()
        self.sync({"app": "me/app"})
        self.assertEqual(self.remote("origin"), str(self.fork))
        self.assertEqual(self.remote("upstream"), str(self.upstream))

    def test_foreign_origin_is_left_alone(self):
        self.sync()
        git("-C", self.dir / "app", "remote", "set-url", "origin", str(self.fork))
        other = Path(self.tmp.name) / "other.git"
        git("clone", "-q", "--bare", self.fork, other)
        repos.fork_url = lambda fork: str(other)
        self.sync({"app": "me/app"})
        self.assertEqual(self.remote("origin"), str(self.fork))

    def test_non_repo_directory_fails(self):
        (self.dir / "app").mkdir()
        with self.assertRaises(ui.StepError):
            self.sync()


if __name__ == "__main__":
    unittest.main()
