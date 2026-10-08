import unittest

from typer.testing import CliRunner

from inatrace import cli


class CliTest(unittest.TestCase):
    def invoke(self, *argv: str):
        return CliRunner().invoke(cli.app, list(argv))

    def test_help(self):
        result = self.invoke("--help")
        self.assertEqual(result.exit_code, 0)
        for command in ("doctor", "repos", "stack"):
            self.assertIn(command, result.output)

    def test_stack_modes(self):
        result = self.invoke("stack", "up", "--help")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("fullstack-dev", result.output)
        self.assertIn("Without it:", result.output)

    def test_unknown_command(self):
        self.assertEqual(self.invoke("nope").exit_code, 2)

    def test_unknown_mode(self):
        self.assertEqual(self.invoke("stack", "up", "--mode", "nope").exit_code, 2)


if __name__ == "__main__":
    unittest.main()
