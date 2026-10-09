import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, paths, ui
from inatrace.deploy import backups, instance as instance_
from inatrace.tests.deploy_fixtures import VALID

TWO = "20261008T000000Z-db.sql.gz 100\n20261008T000000Z-storage.tar.gz 10\n" \
      "20261009T000000Z-db.sql.gz 200\n20261009T000000Z-storage.tar.gz 20\n"


class ScheduleTest(unittest.TestCase):
    def plan(self, crontab: str, **values) -> tuple[str | None, list[str]]:
        remote = mock.Mock()
        remote.output.side_effect = lambda command: crontab if command.startswith("crontab") else "UTC"
        return backups.cron_plan(remote, instance_.Instance("demo", {**VALID, **values}))

    def test_in_words(self):
        self.assertEqual(backups.in_words("@daily"), "daily at midnight")
        self.assertEqual(backups.in_words("30 3 * * *"), "daily at 03:30")
        self.assertEqual(backups.in_words("0 */6 * * *"), "0 */6 * * *")

    def test_added_next_to_the_users_own_lines(self):
        change, lines = self.plan("0 1 * * * ~/mine.sh\n")
        self.assertIn("daily at midnight, UTC", change)
        self.assertEqual(lines[0], "0 1 * * * ~/mine.sh")
        self.assertTrue(lines[1].startswith("@daily cd ~/inatrace && ./backup.sh --scheduled"))

    def test_nothing_to_do_once_there(self):
        _, lines = self.plan("")
        change, again = self.plan("\n".join(lines))
        self.assertIsNone(change)
        self.assertEqual(again, lines)

    def test_changed_and_turned_off(self):
        _, lines = self.plan("")
        change, changed = self.plan("\n".join(lines), INATRACE_BACKUP_SCHEDULE="0 3 * * *")
        self.assertIn("daily at 03:00", change)
        self.assertEqual(len(changed), 1)
        change, off = self.plan("0 1 * * * ~/mine.sh\n" + "\n".join(lines), INATRACE_BACKUP_SCHEDULE="off")
        self.assertIn("stop", change)
        self.assertEqual(off, ["0 1 * * * ~/mine.sh"])

    def test_checked_like_the_other_settings(self):
        for good in ("@daily", "0 3 * * *", "*/30 * * * 1-5", "off"):
            self.assertEqual(instance_.problems({**VALID, "INATRACE_BACKUP_SCHEDULE": good}), [], good)
        for bad in ("@reboot", "daily", "0 3 * *", "0 3 * * * rm -rf /"):
            self.assertTrue(instance_.problems({**VALID, "INATRACE_BACKUP_SCHEDULE": bad}), bad)



class BackupCommandsTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(ui.use_json, False)

    def run_cli(self, *args: str, stdin: str = "", here: tuple[str, ...] = (), on_server: bool = True,
                reachable: bool = True) -> tuple[object, mock.Mock]:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(backups, "deployed") as deployed, mock.patch.object(backups, "wait_healthy"):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            local = Path(tmp) / "demo" / "backups"
            for stamp in here:
                local.mkdir(exist_ok=True)
                for file in backups._files(stamp):
                    (local / file).write_bytes(b"x" * 5)
            remote = deployed.return_value
            if not reachable:
                deployed.side_effect = backups.DeployError("could not log in to srv without a question")
            remote.must.side_effect = lambda command, what: TWO if command.startswith("cd backups") else ""
            remote.output.side_effect = lambda command: (
                ("" if on_server else None) if command.startswith("test -f") else
                "@daily cd ~/inatrace && ./backup.sh --scheduled " + backups.CRON_MARK + "\nUTC")
            remote.destination = "srv"
            result = CliRunner().invoke(cli.app, cli.with_global_options(["deploy", "backup", *args]), input=stdin)
            self.left_here = sorted(path.name for path in local.iterdir()) if local.is_dir() else []
        return result, remote

    def deleted(self, remote: mock.Mock) -> list[str]:
        return [call.args[0] for call in remote.must.call_args_list if call.args[0].startswith("rm ")]

    def test_list_as_data(self):
        result, _ = self.run_cli("list", "demo", "--json")
        event = next(json.loads(line) for line in result.stdout.splitlines() if '"backups"' in line)
        self.assertEqual([b["time"] for b in event["backups"]], ["20261009T000000Z", "20261008T000000Z"])
        self.assertEqual((event["schedule"], event["zone"], event["days"]), ("@daily", "UTC", 7))

    def test_delete_once_confirmed(self):
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", stdin="y\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.deleted(remote),
                         ["rm -f backups/20261008T000000Z-db.sql.gz backups/20261008T000000Z-storage.tar.gz"])

    def test_delete_asks_first(self):
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", stdin="n\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.deleted(remote), [])
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", "--json")
        self.assertEqual(result.exit_code, 1)
        self.assertIn('"flag": "--auto-approve"', result.stdout)
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", "-n")
        self.assertEqual(self.deleted(remote), [])

    def test_never_the_last_one_without_force(self):
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", "20261009T000000Z", "--auto-approve")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("--force", result.output)
        self.assertEqual(self.deleted(remote), [])
        result, remote = self.run_cli("delete", "demo", "20261008T000000Z", "20261009T000000Z", "--auto-approve",
                                      "--force")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(self.deleted(remote)), 1)

    def test_list_both_places(self):
        result, _ = self.run_cli("list", "demo", "--json", here=("20261009T000000Z", "20261001T000000Z"))
        event = next(json.loads(line) for line in result.stdout.splitlines() if '"backups"' in line)
        places = {b["time"]: (b["server"], b["here"]) for b in event["backups"]}
        self.assertEqual(places, {"20261009T000000Z": (True, True), "20261008T000000Z": (True, False),
                                  "20261001T000000Z": (False, True)})

    def test_list_without_the_server(self):
        result, _ = self.run_cli("list", "demo", here=("20261001T000000Z",), reachable=False)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("only the copies here", result.output)
        self.assertIn("20261001T000000Z", result.output)

    def test_download_what_is_missing(self):
        result, remote = self.run_cli("download", "demo", here=("20261009T000000Z",))
        self.assertEqual(result.exit_code, 0, result.output)
        names, into = remote.fetch.call_args.args
        self.assertEqual(names, ["backups/20261008T000000Z-db.sql.gz", "backups/20261008T000000Z-storage.tar.gz"])
        self.assertEqual(into.name, "backups")
        _, remote = self.run_cli("download", "demo", here=("20261009T000000Z", "20261008T000000Z"))
        remote.fetch.assert_not_called()

    def test_restore_one_only_here(self):
        result, remote = self.run_cli("restore", "demo", "20261001T000000Z", "--no-backup", here=("20261001T000000Z",),
                                      on_server=False)
        self.assertEqual(result.exit_code, 0, result.output)
        sent, into = remote.send.call_args.args
        self.assertEqual([path.name for path in sent], backups._files("20261001T000000Z"))
        self.assertEqual(into, "backups")
        self.assertIn("./restore.sh 20261001T000000Z", [call.args[0] for call in remote.must.call_args_list])
        result, remote = self.run_cli("restore", "demo", "20261001T000000Z", on_server=False)
        self.assertEqual(result.exit_code, 1)  # neither there nor here
        remote.send.assert_not_called()

    def test_delete_the_copies_here(self):
        result, remote = self.run_cli("delete", "demo", "20261001T000000Z", "--local", "--auto-approve",
                                      here=("20261001T000000Z", "20261002T000000Z"))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.left_here, backups._files("20261002T000000Z"))
        self.assertEqual(self.deleted(remote), [])

    def test_unknown_or_malformed_times(self):
        for time in ("20250101T000000Z", "latest", "../../etc"):
            result, remote = self.run_cli("delete", "demo", time, "--auto-approve")
            self.assertEqual(result.exit_code, 1, time)
            self.assertEqual(self.deleted(remote), [])


if __name__ == "__main__":
    unittest.main()
