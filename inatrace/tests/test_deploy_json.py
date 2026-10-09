import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, paths, ui
from inatrace.deploy import checks, instance as instance_, lifecycle
from inatrace.tests.deploy_fixtures import VALID


class JsonTest(unittest.TestCase):
    """--json: one JSON event a line on stdout, nothing asked."""

    def setUp(self):
        self.addCleanup(ui.use_json, False)

    def events(self, args: list[str], **patches) -> tuple[int, list[dict]]:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            result = CliRunner().invoke(cli.app, cli.with_global_options(args))
        return result.exit_code, [json.loads(line) for line in result.stdout.splitlines()]

    def test_a_missing_answer_names_its_flag(self):
        code, events = self.events(["deploy", "init", "fresh", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["flag"], "--ssh")

    def test_steps_and_messages(self):
        ui.use_json()
        with mock.patch.object(ui, "_events", new=__import__("io").StringIO()) as stream:
            ui.section("A")
            with ui.doing("waiting [dim](a while)[/]"):
                ui.ok("fine")
            with self.assertRaises(ui.NoAnswer):
                ui.confirm("Go?", True)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([e["event"] for e in events], ["section", "start", "ok", "end"])
        self.assertEqual(events[1]["task"], "waiting (a while)")
        self.assertTrue(events[3]["ok"])

    def test_status_is_one_event(self):
        found = "\n".join(["#containers", "#site", "200 401", "#backups", "#usage"])
        with mock.patch.object(checks, "deployed") as deployed:
            deployed.return_value.must.return_value = found
            deployed.return_value.destination = "srv"
            code, events = self.events(["deploy", "status", "demo", "--json"])
        self.assertEqual(code, 1)  # no containers
        self.assertEqual([e["event"] for e in events], ["status"])
        self.assertTrue(events[0]["site"]["answers"])
        self.assertEqual(events[0]["containers"][0]["state"], "missing")

    def test_a_dry_run_plan(self):
        with mock.patch.object(lifecycle, "deployed") as deployed:
            deployed.return_value.must.return_value = "backend\n"
            deployed.return_value.destination = "srv"
            code, events = self.events(["deploy", "down", "demo", "-n", "--json"])
        self.assertEqual(code, 0)
        plan = next(e for e in events if e["event"] == "plan")
        self.assertIn("stop and remove backend", plan["actions"][0])

    def test_logs_say_which_container(self):
        def lines(command, on_line):
            on_line("backend-1  | Started in 9 s\n")
            on_line("no prefix\n")
            return 0
        with mock.patch.object(checks, "deployed") as deployed:
            deployed.return_value.lines.side_effect = lines
            code, events = self.events(["deploy", "logs", "demo", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(events[0], {"event": "log", "service": "backend", "text": "Started in 9 s"})
        self.assertEqual(events[1]["service"], None)


if __name__ == "__main__":
    unittest.main()
