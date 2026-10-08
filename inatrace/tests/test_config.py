import tempfile
import unittest
from pathlib import Path

from inatrace import config
from inatrace.repos import Repo


class ParseTest(unittest.TestCase):
    def test_lines(self):
        text = "# comment\n\nA=1\r\nB=\"two words\"\nC='x'\nD=a=b\nnot a setting\n"
        self.assertEqual(config.parse(text),
                         {"A": "1", "B": "two words", "C": "x", "D": "a=b"})

    def test_unmatched_quotes_are_kept(self):
        self.assertEqual(config.parse("A=\"x\n"), {"A": "\"x"})


class LoadTest(unittest.TestCase):
    def load(self, text: str) -> config.Settings:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text)
            return config.load(path)

    def test_missing_file(self):
        settings = config.load(Path("/nonexistent/.env"))
        self.assertEqual(settings.values, {})

    def test_unknown_keys_are_reported(self):
        settings = self.load("INATRACE_GATEWAY_PORT=18000\nNOPE=1\n")
        self.assertEqual(settings.values["INATRACE_GATEWAY_PORT"], "18000")
        self.assertNotIn("NOPE", settings.values)
        self.assertEqual(len(settings.warnings), 1)
        self.assertIn("NOPE", settings.warnings[0])

    def test_forks(self):
        settings = self.load("INATRACE_FORK_INATRACE_BACKEND=me/inatrace-backend\n"
                             "INATRACE_FORK_INATRACE_FRONTEND=not a fork\n"
                             "INATRACE_FORK_OTHER=me/other\n")
        repos = [Repo("git@github.com:agstack/inatrace-backend.git", "inatrace-backend"),
                 Repo("git@github.com:agstack/inatrace-frontend.git", "inatrace-frontend")]
        self.assertEqual(settings.forks(repos), {"inatrace-backend": "me/inatrace-backend"})
        self.assertEqual(len(settings.warnings), 2)


if __name__ == "__main__":
    unittest.main()
