import unittest
from unittest import mock

from inatrace import paths, smoke


class RunTest(unittest.TestCase):
    def run_smoke(self, *args, returncodes=(0, 0)) -> tuple[int, list[list[str]]]:
        results = [mock.Mock(returncode=code) for code in returncodes]
        with mock.patch.object(smoke.subprocess, "run", side_effect=results) as run:
            code = smoke.run(*args)
        return code, [call.args[0] for call in run.call_args_list]

    def test_installs_the_browser_then_runs_the_tests_dir(self):
        code, (install, pytest) = self.run_smoke(False, False, [])
        self.assertEqual(code, 0)
        self.assertEqual(install[-3:], ["playwright", "install", "chromium"])
        self.assertIn("--group", pytest)
        # From any working directory: the tests' directory is always given.
        self.assertEqual(pytest[-1], str(paths.SMOKE_TESTS / "tests"))
        self.assertNotIn("--lifecycle", pytest)

    def test_options_and_extra_args(self):
        _, (_, pytest) = self.run_smoke(True, True, ["-k", "api"])
        self.assertIn("--lifecycle", pytest)
        self.assertIn("--log-cli-level=INFO", pytest)
        self.assertEqual(pytest[-3:-1], ["-k", "api"])

    def test_stops_when_the_browser_cannot_be_installed(self):
        code, calls = self.run_smoke(False, False, [], returncodes=(1,))
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
