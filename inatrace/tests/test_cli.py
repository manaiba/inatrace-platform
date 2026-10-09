import json
import unittest
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, doctor, ui


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


class OptionalValuesTest(unittest.TestCase):
    def test_a_bare_watch_gets_its_default(self):
        fill = cli.with_optional_values
        self.assertEqual(fill(["deploy", "status", "vm", "-w"]), ["deploy", "status", "vm", "-w", "2"])
        self.assertEqual(fill(["deploy", "status", "vm", "-w", "5"]), ["deploy", "status", "vm", "-w", "5"])
        self.assertEqual(fill(["deploy", "status", "vm", "--watch", "0.5"]),
                         ["deploy", "status", "vm", "--watch", "0.5"])
        self.assertEqual(fill(["deploy", "status", "-w", "vm"]), ["deploy", "status", "-w", "2", "vm"])


class JsonTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(ui.use_json, False)

    def test_taken_anywhere(self):
        move = cli.with_global_options
        self.assertEqual(move(["deploy", "status", "vm", "--json"]), ["--json", "deploy", "status", "vm"])
        self.assertEqual(move(["--json", "doctor"]), ["--json", "doctor"])
        self.assertEqual(move(["doctor"]), ["doctor"])
        # After --, it is another program's.
        self.assertEqual(move(["smoke", "--", "--json"]), ["smoke", "--", "--json"])

    def test_doctor_is_one_event(self):
        with mock.patch.object(doctor, "checks", return_value=[("docker", True), ("gateway", False)]), \
                mock.patch.object(doctor, "permissions_status", return_value=(True, "OK")):
            result = CliRunner().invoke(cli.app, ["--json", "doctor"])
        self.assertEqual(result.exit_code, 0)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([e["event"] for e in events], ["doctor"])
        self.assertEqual(events[0]["checks"][1], {"name": "gateway", "ok": False})
        self.assertTrue(any("inatrace stack up" in hint for hint in events[0]["hints"]))

    def test_a_failure_is_an_error_event(self):
        with mock.patch.object(cli.stack_, "status", side_effect=ui.StepError("no docker")):
            result = CliRunner().invoke(cli.app, ["--json", "stack", "status"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(json.loads(result.stdout), {"event": "error", "message": "no docker"})


if __name__ == "__main__":
    unittest.main()
