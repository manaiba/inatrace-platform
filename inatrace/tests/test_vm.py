import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inatrace import ui, vm


def a_vm(name="demo", ports=None, home=Path("/tmp/vms")):
    with mock.patch.object(vm, "VMS", home):
        return vm.Vm(name, "ubuntu-26.04", "ubuntu", 4, 2, 20, ports or {"ssh": 2022, "http": 10080, "https": 10443})


class SshConfigTest(unittest.TestCase):
    def test_block_goes_in_and_out_leaving_the_rest(self):
        mine = a_vm()
        theirs = "Host prod\n    HostName 203.0.113.10\n"
        text = theirs + "\n" + vm.ssh_block(mine)
        self.assertIn("Host demo\n    HostName 127.0.0.1\n    Port 2022\n    User ubuntu\n", text)
        self.assertIn("StrictHostKeyChecking accept-new", text)
        self.assertEqual(vm.without_block(text, "demo"), theirs)
        self.assertEqual(vm.without_block(theirs, "demo"), theirs)
        self.assertEqual(vm.without_block(vm.ssh_block(mine), "demo"), "")

    def test_a_host_of_theirs_with_the_name_is_foreign(self):
        self.assertTrue(vm.foreign_host("Host demo\n  HostName x\n", "demo"))
        self.assertTrue(vm.foreign_host("Host a demo\n", "demo"))
        self.assertFalse(vm.foreign_host("Host demo2\n", "demo"))
        self.assertFalse(vm.foreign_host(vm.ssh_block(a_vm()), "demo"))


class PortsTest(unittest.TestCase):
    def test_the_first_free_set(self):
        with mock.patch.object(vm, "_free", return_value=True):
            self.assertEqual(vm.free_ports([], {}), {"ssh": 2022, "http": 10080, "https": 10443})
            taken = [{"ssh": 2022, "http": 10080, "https": 10443}]
            self.assertEqual(vm.free_ports(taken, {}), {"ssh": 2023, "http": 10081, "https": 10444})
            self.assertEqual(vm.free_ports(taken, {"ssh": 2200})["ssh"], 2200)

    def test_skips_what_something_listens_on(self):
        with mock.patch.object(vm, "_free", side_effect=lambda port: port != 10443):
            self.assertEqual(vm.free_ports([], {})["https"], 10444)


class HostTest(unittest.TestCase):
    def test_lists_what_is_missing_with_the_package_and_installs_nothing(self):
        with mock.patch.object(vm.shutil, "which", side_effect=lambda tool: None if tool == "cloud-localds" else tool), \
                mock.patch.object(vm, "family", return_value="debian"), \
                mock.patch.object(vm.os, "access", return_value=True), \
                mock.patch.object(vm.platform, "machine", return_value="x86_64"), \
                mock.patch.object(vm, "agent_keys", return_value=["ssh-ed25519 AAAA me"]), \
                mock.patch.object(vm.subprocess, "run") as run:
            found = vm.missing()
        self.assertEqual(found, [{"what": "cloud-localds", "how": "sudo apt-get install cloud-image-utils",
                                  "package": "cloud-image-utils"}])
        run.assert_not_called()

    def test_kvm_and_the_agent(self):
        with mock.patch.object(vm.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(vm, "family", return_value=None), \
                mock.patch.object(vm.os, "access", return_value=False), \
                mock.patch.object(vm.platform, "machine", return_value="x86_64"), \
                mock.patch.object(vm, "agent_keys", return_value=[]):
            whats = [item["what"] for item in vm.missing()]
        self.assertEqual(whats, ["/dev/kvm, usable by you", "a key in your ssh agent"])


class QemuTest(unittest.TestCase):
    def test_command_forwards_the_ports_on_localhost(self):
        command = vm.qemu_command(a_vm())
        self.assertIn("-enable-kvm", command)
        self.assertIn("4G", command)
        nic = command[command.index("-nic") + 1]
        for forward in ("127.0.0.1:2022-:22", "127.0.0.1:10080-:80", "127.0.0.1:10443-:443"):
            self.assertIn(forward, nic)


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(vm, "VMS", self.home / "vms"),
                   mock.patch.object(vm, "IMAGES", self.home / "images"),
                   mock.patch.object(vm, "SSH_CONFIG", self.home / ".ssh" / "config"),
                   mock.patch.object(vm, "missing", return_value=[]),
                   mock.patch.object(vm, "agent_keys", return_value=["ssh-ed25519 AAAA me"]),
                   mock.patch.object(vm, "_free", return_value=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_dry_run_changes_nothing(self):
        with mock.patch.object(vm.subprocess, "run") as run:
            self.assertEqual(vm.create("demo", dry_run=True), 0)
        run.assert_not_called()
        self.assertFalse((self.home / "vms").exists())
        self.assertFalse(vm.SSH_CONFIG.exists())

    def test_missing_stops_before_anything(self):
        with mock.patch.object(vm, "missing", return_value=[{"what": "qemu-img", "how": "x"}]), \
                self.assertRaises(vm.VmError):
            vm.create("demo", auto_approve=True)
        self.assertFalse((self.home / "vms").exists())

    def test_create_then_destroy(self):
        image = self.home / "images" / vm.DISTROS["ubuntu-26.04"].file
        image.parent.mkdir(parents=True)
        image.write_text("an image")
        with mock.patch.object(vm, "_run") as run, mock.patch.object(vm, "_boot") as boot:
            self.assertEqual(vm.create("demo", auto_approve=True), 0)
        commands = [call.args[0][0] for call in run.call_args_list]
        self.assertEqual(commands, ["qemu-img", "cloud-localds"])  # the image was there: no download
        boot.assert_called_once()
        saved = json.loads((self.home / "vms" / "demo" / "vm.json").read_text())
        self.assertEqual(saved["ports"], {"ssh": 2022, "http": 10080, "https": 10443})
        self.assertIn("ssh-ed25519 AAAA me", (self.home / "vms" / "demo" / "user-data").read_text())
        self.assertIn("Host demo", vm.SSH_CONFIG.read_text())
        with self.assertRaises(vm.VmError):
            vm.create("demo", auto_approve=True)
        self.assertEqual(vm.destroy("demo", auto_approve=True), 0)
        self.assertFalse((self.home / "vms" / "demo").exists())
        self.assertNotIn("Host demo", vm.SSH_CONFIG.read_text())
        self.assertTrue(image.exists())

    def test_their_host_with_the_name_is_left_alone(self):
        vm.SSH_CONFIG.parent.mkdir(parents=True)
        vm.SSH_CONFIG.write_text("Host demo\n    HostName 203.0.113.10\n")
        with self.assertRaises(vm.VmError):
            vm.create("demo", auto_approve=True)

    def test_json_lists_them(self):
        lines = []
        ui.use_json()
        self.addCleanup(ui.use_json, False)
        with mock.patch.object(vm.ui, "emit", side_effect=lambda event, **fields: lines.append((event, fields))):
            vm.listing()
        self.assertEqual(lines, [("vms", {"vms": []})])


if __name__ == "__main__":
    unittest.main()
