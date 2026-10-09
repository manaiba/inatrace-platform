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
