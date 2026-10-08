import os
import subprocess
import unittest
from unittest import mock

from inatrace import doctor


class SshAgentTest(unittest.TestCase):
    def check(self, returncode: int) -> tuple[bool, list[str]]:
        done = subprocess.CompletedProcess([], returncode)
        with mock.patch.object(doctor.shell, "run", return_value=done) as run:
            usable = doctor.ssh_agent_usable()
        return usable, run.call_args.args[0]

    @mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/vscode-ssh-auth-gone.sock"})
    def test_asks_the_relay_not_the_inherited_socket(self):
        usable, cmd = self.check(0)
        self.assertTrue(usable)
        self.assertIn(f"SSH_AUTH_SOCK={doctor.SSH_AGENT_RELAY}", cmd)

    def test_reachable_but_empty_agent_is_usable(self):
        self.assertTrue(self.check(1)[0])

    def test_no_agent_or_no_ssh_add(self):
        self.assertFalse(self.check(2)[0])
        self.assertFalse(self.check(127)[0])


class NodeTest(unittest.TestCase):
    def test_no_nvm_and_no_node(self):
        # On a machine without Node at all (only the images mode in use).
        with mock.patch.object(doctor.shell, "ok", return_value=False), \
             mock.patch.object(doctor.shell, "have", return_value=False):
            self.assertFalse(doctor.node_frontend_available())

    def test_node_14_on_the_path(self):
        with mock.patch.object(doctor.shell, "ok", return_value=False), \
             mock.patch.object(doctor.shell, "have", return_value=True), \
             mock.patch.object(doctor.shell, "output", return_value="v14.21.3"):
            self.assertTrue(doctor.node_frontend_available())


if __name__ == "__main__":
    unittest.main()
