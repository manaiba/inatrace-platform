import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, paths, ui
from inatrace.deploy import instance as instance_, lifecycle, remote
from inatrace.tests.deploy_fixtures import VALID


class BackupOnUpTest(unittest.TestCase):
    def test_only_when_the_data_side_changes(self):
        self.assertFalse(lifecycle._backs_up(None, True, [], []))
        self.assertFalse(lifecycle._backs_up(None, True, [("recreate", "frontend"), ("recreate", "caddy")], ["caddy"]))
        self.assertTrue(lifecycle._backs_up(None, True, [("recreate", "backend")], []))
        self.assertTrue(lifecycle._backs_up(None, True, [("recreate", "mysql")], []))
        self.assertTrue(lifecycle._backs_up(None, True, [], ["backend"]))

    def test_flags_decide(self):
        self.assertTrue(lifecycle._backs_up(True, True, [], []))
        self.assertFalse(lifecycle._backs_up(False, True, [("recreate", "backend")], []))

    def test_never_when_not_running(self):
        self.assertFalse(lifecycle._backs_up(True, False, [("create", "mysql")], []))
        self.assertFalse(lifecycle._backs_up(None, False, [("create", "mysql")], []))

    def test_planned_actions(self):
        planned = (" Container inatrace-mysql-1  Running\n"
                   " Container inatrace-backend-1  Recreate\n"
                   " Container inatrace-backend-1  Recreated\n")
        self.assertEqual(remote.planned_actions(planned), [("recreate", "backend")])


class DownTest(unittest.TestCase):
    def run_down(self, running: str, dry_run: bool) -> tuple[int, list[str]]:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            with mock.patch.object(lifecycle, "deployed") as deployed:
                deployed.return_value.must.return_value = running
                code = lifecycle.down("demo", dry_run)
                commands = [call.args[0] for call in deployed.return_value.must.call_args_list]
        return code, commands

    def test_stops_keeping_the_volumes(self):
        code, commands = self.run_down("backend\ncaddy\n", dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(commands[1:3], ["docker compose stop caddy", "docker compose stop backend"])
        self.assertIn("docker compose down --remove-orphans", commands)
        self.assertFalse(any("-v" in c.split() or "--volumes" in c for c in commands))

    def test_dry_run_only_lists(self):
        code, commands = self.run_down("caddy\n", dry_run=True)
        self.assertEqual(code, 0)
        self.assertFalse(any("down" in c for c in commands))

    def test_nothing_running(self):
        code, commands = self.run_down("", dry_run=False)
        self.assertEqual(code, 0)
        self.assertFalse(any("down" in c for c in commands))



class DestroyTest(unittest.TestCase):
    """destroy: the server's part only, once the name is typed."""

    def setUp(self):
        self.addCleanup(ui.use_json, False)

    def run_destroy(self, *flags: str, stdin: str = "", there: bool = True):
        def output(command):
            if command.startswith("test -d"):
                return "yes" if there else None
            if command.startswith("docker ps -aq") and "volume ls -q" in command and "du -sh" in command:
                return "4\ninatrace_mysql inatrace_storage \n1.2M\n1\n2" if there else "0\n\n\n0\n0"
            if "ps -q --status running mysql" in command:
                return "abc" if there else ""
            if command.startswith("crontab -l"):
                return ""
            return "0\n0\nnone"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(lifecycle, "connect") as connect, \
                mock.patch.object(lifecycle, "missing_here", return_value=[]), \
                mock.patch.object(lifecycle, "backup_first", return_value="20261009T000000Z") as made, \
                mock.patch.object(lifecycle, "fetch"):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            remote = connect.return_value
            remote.destination = "srv"
            remote.output.side_effect = output
            result = CliRunner().invoke(cli.app, cli.with_global_options(["deploy", "destroy", "demo", *flags]),
                                        input=stdin)
        removed = [call.args[0] for call in remote.must.call_args_list]
        return result, removed, made

    def test_a_dry_run_only_says(self):
        result, removed, made = self.run_destroy("-n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("remove ~/inatrace", " ".join(result.output.split()))
        self.assertEqual(removed, [])
        made.assert_not_called()

    def test_only_the_name_confirms(self):
        result, removed, _ = self.run_destroy(stdin="yes\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(removed, [])
        result, removed, made = self.run_destroy(stdin="demo\n")
        self.assertEqual(result.exit_code, 0, result.output)
        made.assert_called_once()
        self.assertTrue(any("down --volumes --rmi all" in command for command in removed))
        self.assertTrue(any("rm -rf ~/inatrace" in command for command in removed))

    def test_without_a_terminal_auto_approve(self):
        result, removed, _ = self.run_destroy("--json")
        self.assertEqual(result.exit_code, 1)
        self.assertIn('"flag": "--auto-approve"', result.stdout)
        self.assertEqual(removed, [])

    def test_no_backup_skips_it(self):
        result, _, made = self.run_destroy("--no-backup", "--auto-approve")
        self.assertEqual(result.exit_code, 0, result.output)
        made.assert_not_called()

    def test_nothing_there(self):
        result, removed, _ = self.run_destroy("--auto-approve", there=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("nothing of demo", result.output)
        self.assertEqual(removed, [])


class RestoreOnUpTest(unittest.TestCase):
    def test_a_wrong_time_stops_before_anything(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(lifecycle, "connect") as connect, \
                mock.patch.object(lifecycle, "available", return_value=["20261009T000000Z"]):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            connect.return_value.destination = "srv"
            result = CliRunner().invoke(cli.app, ["deploy", "up", "demo", "--restore", "20250101T000000Z"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("no backup 20250101T000000Z", result.output)
        connect.return_value.sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
