import contextlib
import io
import socket
import tempfile
import unittest
from pathlib import Path

import initialize
from common import env


def settings(**values) -> env.Settings:
    return env.Settings(values=values)


class ComposeEnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.devcontainer = root / ".devcontainer"
        self.devcontainer.mkdir()
        self.sockets = []

    def tearDown(self):
        for s in self.sockets:
            s.close()
        self.tmp.cleanup()

    def sock(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(path))
        self.sockets.append(s)

    def compose_env(self, s: env.Settings, environ: dict) -> tuple[dict, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            values = initialize.compose_env(s, self.devcontainer, environ, self.home)
        return values, err.getvalue()

    def test_bare_host_gets_placeholders(self):
        values, notes = self.compose_env(settings(), {})
        state = self.devcontainer / ".state"
        self.assertEqual(values["DC_INSTANCE"], "inatrace-platform")
        self.assertEqual(values["HOST_GITCONFIG"], str(state / "gitconfig"))
        self.assertEqual(values["HOST_SSH_AGENT_DIR"], str(state / "ssh-agent.d"))
        self.assertEqual(values["HOST_RUNTIME_DIR"], str(state / "runtime.d"))
        self.assertEqual(values["DC_PLAYWRIGHT_HEADLESS"], "true")
        self.assertEqual(values["DC_GATEWAY_PORT"], "8000")
        self.assertTrue((state / "gitconfig").is_file())
        self.assertIn("no SSH agent", notes)
        self.assertIn("no Wayland", notes)

    def test_desktop_host(self):
        (self.home / ".gitconfig").write_text("[user]\n")
        (self.home / ".ssh" / "agent").mkdir(parents=True)
        runtime = Path(self.tmp.name) / "run"
        self.sock(runtime / "wayland-1")
        self.sock(runtime / "pulse" / "native")
        values, notes = self.compose_env(
            settings(INATRACE_INSTANCE="test", INATRACE_GATEWAY_PORT="18000"),
            {"XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-1",
             "SSH_AUTH_SOCK": str(self.home / ".ssh" / "agent" / "s.x")})
        self.assertEqual(values["DC_INSTANCE"], "test")
        self.assertEqual(values["HOST_GITCONFIG"], str(self.home / ".gitconfig"))
        self.assertEqual(values["HOST_SSH_AGENT_DIR"], str(self.home / ".ssh" / "agent"))
        self.assertEqual(values["HOST_RUNTIME_DIR"], str(runtime))
        self.assertEqual(values["HOST_WAYLAND_DISPLAY"], "wayland-1")
        self.assertEqual(values["DC_PLAYWRIGHT_HEADLESS"], "false")
        self.assertEqual(values["DC_GATEWAY_PORT"], "18000")
        self.assertEqual(notes, "")

    def test_forced_headless_and_foreign_agent(self):
        runtime = Path(self.tmp.name) / "run"
        self.sock(runtime / "wayland-0")
        values, notes = self.compose_env(settings(PLAYWRIGHT_HEADLESS="true"),
                                 {"XDG_RUNTIME_DIR": str(runtime), "SSH_AUTH_SOCK": "/tmp/x"})
        self.assertEqual(values["DC_PLAYWRIGHT_HEADLESS"], "true")
        self.assertIn("outside ~/.ssh/agent", notes)

    def test_invalid_instance(self):
        with self.assertRaises(SystemExit):
            self.compose_env(settings(INATRACE_INSTANCE="Bad Name"), {})

    def test_render(self):
        text = initialize.render_env({"A": "1", "B": "x"})
        self.assertTrue(text.startswith("# Generated"))
        self.assertEqual(env.parse(text), {"A": "1", "B": "x"})


class OldLayoutTest(unittest.TestCase):
    def test_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(initialize.old_layout_notes(root), [])
            (root / "src" / "tmp").mkdir(parents=True)
            self.assertEqual(initialize.old_layout_notes(root), [])
            (root / "src" / "inatrace-backend" / ".git").mkdir(parents=True)
            (root / "src" / "gateway").mkdir()
            notes = "\n".join(initialize.old_layout_notes(root))
            self.assertIn("mv src/inatrace-backend repos/\n", notes)
            self.assertIn("mv src/tmp tmp", notes)
            self.assertNotIn("gateway", notes)


if __name__ == "__main__":
    unittest.main()
