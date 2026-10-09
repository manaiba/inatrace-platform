import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, doctor, paths, permissions


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sub").mkdir()
        for name, mode in (("loose", 0o666), ("tight", 0o644), ("script", 0o777), ("sub/deep", 0o664)):
            (self.root / name).write_text("x")
            os.chmod(self.root / name, mode)
        os.chmod(self.root / "sub", 0o777)
        os.chmod(self.root, 0o755)
        # A link to a loose file outside: chmod would follow it, so it is left alone.
        self.outside = tempfile.NamedTemporaryFile(delete=False)
        os.chmod(self.outside.name, 0o666)
        (self.root / "link").symlink_to(self.outside.name)

    def tearDown(self):
        self.tmp.cleanup()
        os.unlink(self.outside.name)

    def mode(self, name: str) -> int:
        return (self.root / name).stat().st_mode & 0o777

    def test_writable(self):
        found = {Path(p).relative_to(self.root).as_posix() for p in permissions.writable(self.root)}
        self.assertEqual(found, {"loose", "script", "sub", "sub/deep"})

    def test_fix_removes_only_group_and_other_write(self):
        fixed, failed = permissions.fix(self.root)
        self.assertEqual((fixed, failed), (4, []))
        self.assertEqual([self.mode(n) for n in ("loose", "tight", "script", "sub", "sub/deep")],
                         [0o644, 0o644, 0o755, 0o755, 0o644])
        self.assertEqual(os.stat(self.outside.name).st_mode & 0o777, 0o666)
        self.assertEqual(permissions.writable(self.root), [])

    def test_umask(self):
        old = os.umask(0)
        try:
            self.assertTrue(permissions.umask_lets_others_write())
            os.umask(0o022)
            self.assertFalse(permissions.umask_lets_others_write())
            self.assertEqual(permissions.umask(), 0o022)
        finally:
            os.umask(old)

    def test_doctor(self):
        with mock.patch.object(paths, "ROOT", self.root), \
                mock.patch.object(permissions, "umask", return_value=0o022):
            tight, state = doctor.permissions_status()
            self.assertFalse(tight)
            self.assertIn("4 paths writable by others", state)
            permissions.fix(self.root)
            self.assertEqual(doctor.permissions_status(), (True, "OK"))
        with mock.patch.object(paths, "ROOT", self.root), \
                mock.patch.object(permissions, "umask", return_value=0):
            self.assertEqual(doctor.permissions_status(), (False, "umask 0000: new ones too"))

    def test_command(self):
        with mock.patch.object(paths, "ROOT", self.root), \
                mock.patch.object(permissions, "umask", return_value=0o022):
            result = CliRunner().invoke(cli.app, ["fix-permissions"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("fix-permissions: 4 files and directories", result.output)
        self.assertEqual(self.mode("loose"), 0o644)


if __name__ == "__main__":
    unittest.main()
