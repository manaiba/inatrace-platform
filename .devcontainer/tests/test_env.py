import tempfile
import unittest
from pathlib import Path

from common import env


class LoadTest(unittest.TestCase):
    def load(self, text: str) -> env.Settings:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text)
            return env.load(path)

    def test_missing_file(self):
        settings = env.load(Path("/nonexistent/.env"))
        self.assertEqual(settings.values, {})
        self.assertEqual(settings.get("INATRACE_GATEWAY_PORT"), "8000")
        self.assertEqual(settings.instance, "inatrace-platform")

    def test_only_the_dev_container_keys(self):
        settings = self.load("INATRACE_GATEWAY_PORT=18000\nINATRACE_MODE=images\nNOPE=1\n")
        self.assertEqual(settings.values, {"INATRACE_GATEWAY_PORT": "18000"})

    def test_empty_value_falls_back_to_default(self):
        self.assertEqual(self.load("INATRACE_BIND_ADDRESS=\n").get("INATRACE_BIND_ADDRESS"),
                         "127.0.0.1")

    def test_token_is_stripped(self):
        self.assertEqual(self.load("INATRACE_GH_TOKEN= abc \n").token, "abc")

    def test_invalid_instance(self):
        with self.assertRaises(SystemExit):
            self.load("INATRACE_INSTANCE=Bad Name\n").instance


class ParseTest(unittest.TestCase):
    def test_lines(self):
        text = "# comment\n\nA=1\r\nB=\"two words\"\nC='x'\nD=a=b\nnot a setting\n"
        self.assertEqual(env.parse(text), {"A": "1", "B": "two words", "C": "x", "D": "a=b"})


if __name__ == "__main__":
    unittest.main()
