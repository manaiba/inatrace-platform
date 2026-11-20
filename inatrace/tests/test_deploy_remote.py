import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, paths
from inatrace.deploy import remote, wizard
from inatrace.tests.deploy_fixtures import OFFLINE


class ChangesTest(unittest.TestCase):
    def test_itemized(self):
        itemized = ("<f+++++++++ compose.override.yaml\n<fcsT...... .env\ncd+++++++++ caddy/\n"
                    ".f...p..... backup.sh\n*deleting   stray.txt\n*deleting   old/\n")
        changes = remote.Changes.parse(itemized)
        self.assertEqual((changes.added, changes.changed, changes.removed),
                         (["compose.override.yaml"], [".env"], ["stray.txt"]))
        self.assertTrue(changes.touching((".env",)))
        self.assertFalse(changes.touching(remote.MOUNTED["backend"]))
        self.assertFalse(remote.Changes.parse(".f...p..... backup.sh\n"))


@OFFLINE
class PrereqsTest(unittest.TestCase):
    def test_profile(self):
        self.assertEqual(remote.prereq_profile("ubuntu debian"), "debian")
        self.assertEqual(remote.prereq_profile("debian "), "debian")
        self.assertEqual(remote.prereq_profile("linuxmint ubuntu debian"), "debian")
        self.assertEqual(remote.prereq_profile("rocky rhel centos fedora"), "rhel")
        self.assertEqual(remote.prereq_profile("almalinux rhel centos fedora"), "rhel")
        self.assertEqual(remote.prereq_profile("rhel fedora"), "rhel")
        # Fedora has a Docker repository of its own, and no profile yet.
        self.assertIsNone(remote.prereq_profile("fedora "))
        self.assertIsNone(remote.prereq_profile("opensuse-tumbleweed opensuse suse"))

    def test_every_profile_has_its_script(self):
        for profile in remote.PREREQ_PROFILES:
            self.assertTrue((paths.DEPLOY_DIR / "prereqs" / f"{profile}.sh").is_file(), profile)

    def test_the_docs_run_the_script(self):
        docs = (paths.ROOT / "docs" / "deploy.md").read_text()
        for profile in remote.PREREQ_PROFILES:
            self.assertIn(f"deploy/prereqs/{profile}.sh", docs)

    def test_init_does_not_offer_to_install_while_apt_is_busy(self):
        busy = remote.Probe(True, "Ubuntu 26.04 LTS", "ubuntu debian", ready=False, busy=True)
        answers = ["admin@203.0.113.10", "", "", "", "n", "", "2.40.3", "", "2.34.0", "n", "", "y"]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(remote.Remote, "probe", return_value=busy), \
                mock.patch.object(wizard, "install_prereqs") as install, \
                mock.patch.object(wizard, "public_address", return_value="203.0.113.10"):
            result = CliRunner().invoke(cli.app, ["deploy", "init", "demo"], input="\n".join(answers) + "\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("busy", result.output)
        self.assertIn("deploy prepare demo", result.output)
        install.assert_not_called()

    def test_init_installs_only_after_confirming(self):
        missing = remote.Probe(True, "Ubuntu 26.04 LTS", "ubuntu debian", ready=False)
        answers = ["admin@203.0.113.10", "y", "", "", "", "n", "", "2.40.3", "", "2.34.0", "n", ""]
        for last, installs in (("y", True), ("n", False)):
            with tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                    mock.patch.object(remote.Remote, "probe", return_value=missing), \
                    mock.patch.object(wizard, "install_prereqs") as install, \
                    mock.patch.object(wizard, "public_address", return_value="203.0.113.10"):
                result = CliRunner().invoke(cli.app, ["deploy", "init", "demo"],
                                            input="\n".join(answers + [last]) + "\n")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("install the dependencies", result.output)  # in the summary
            self.assertEqual(install.called, installs)
            if installs:
                self.assertEqual(install.call_args.args[1], "debian")


if __name__ == "__main__":
    unittest.main()


class ComposePlanTest(unittest.TestCase):
    """The dry-run plan when an image `up` would pull is not on the server yet."""

    CONFIG = json.dumps({"services": {
        "mysql": {"image": "mysql:8.4.11"},
        "backend": {"image": "ghcr.io/agstack/inatrace-backend:3.0.0-rc.2"},
        "frontend": {"image": "ghcr.io/agstack/inatrace-frontend:3.0.0-rc.2"},
        "beszel-init": {"image": "busybox:1"},
    }})

    def plan(self, absent: list[str]):
        server = remote.Remote("admin@203.0.113.10")
        commands = []

        def output(command):
            commands.append(command)
            if command == "mktemp -d":
                return "/tmp/plan"
            if "docker image inspect" in command:
                return "\n".join(absent)
            if "ps -a --services" in command:
                return "mysql\nbackend\nfrontend"
            return ""

        def must(command, what):
            commands.append(command)
            if " config --format json" in command:
                return self.CONFIG
            return " Container inatrace-mysql-1  Running\n"

        files = {"compose.yaml": Path("compose.yaml"), ".env": Path(".env")}
        with mock.patch.object(server, "output", side_effect=output), \
                mock.patch.object(server, "must", side_effect=must), \
                mock.patch.object(remote.subprocess, "run", return_value=mock.Mock(returncode=0)):
            actions, pulls = server.compose_plan(files)
        return actions, pulls, [c for c in commands if "--dry-run up" in c]

    def test_all_there(self):
        actions, pulls, dry_runs = self.plan([])
        self.assertEqual((actions, pulls), ([], []))
        self.assertNotIn("--no-deps", dry_runs[0])

    def test_a_new_image_is_left_out_and_recreated(self):
        backend = "ghcr.io/agstack/inatrace-backend:3.0.0-rc.2"
        actions, pulls, dry_runs = self.plan([backend])
        self.assertEqual(pulls, [backend])
        self.assertEqual(actions, [("recreate", "backend")])
        self.assertIn("--no-deps mysql frontend", dry_runs[0])
        self.assertNotIn("beszel-init", dry_runs[0])

    def test_service_images(self):
        self.assertEqual(remote.service_images(self.CONFIG)["backend"],
                         "ghcr.io/agstack/inatrace-backend:3.0.0-rc.2")
        self.assertNotIn("beszel-init", remote.service_images(self.CONFIG))
