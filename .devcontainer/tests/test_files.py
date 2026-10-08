import json
import tempfile
import unittest
from pathlib import Path

from common import files


class BlockTest(unittest.TestCase):
    def test_appended_and_replaced(self):
        once = files.with_block("export A=1", "B=2")
        self.assertTrue(once.startswith("export A=1\n" + files.BEGIN))
        self.assertTrue(once.endswith(files.END + "\n"))
        twice = files.with_block(once, "B=3")
        self.assertEqual(twice.count(files.BEGIN), 1)
        self.assertIn("B=3", twice)
        self.assertNotIn("B=2", twice)
        self.assertEqual(files.with_block(twice, "B=3"), twice)

    def test_first(self):
        text = files.with_block("Host x\n", "Host *", first=True)
        self.assertTrue(text.startswith(files.BEGIN))
        self.assertTrue(text.endswith("Host x\n"))
        self.assertEqual(files.with_block(text, "Host *", first=True), text)


class WriteTest(unittest.TestCase):
    def test_write_if_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "f"
            self.assertTrue(files.write_if_changed(path, "x", mode=0o600))
            self.assertFalse(files.write_if_changed(path, "x"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(files.write_if_changed(path, "y"))

    def test_json_defaults_keep_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"voice": {"enabled": False}, "theme": "dark"}))
            self.assertTrue(files.json_defaults(path, {"voice": {"enabled": True, "mode": "hold"}}))
            self.assertEqual(json.loads(path.read_text()),
                             {"voice": {"enabled": False, "mode": "hold"}, "theme": "dark"})
            self.assertFalse(files.json_defaults(path, {"voice": {"mode": "x"}}))

    def test_json_defaults_on_a_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text("{oops")
            self.assertTrue(files.json_defaults(path, {"a": 1}))
            self.assertEqual(json.loads(path.read_text()), {"a": 1})


if __name__ == "__main__":
    unittest.main()
