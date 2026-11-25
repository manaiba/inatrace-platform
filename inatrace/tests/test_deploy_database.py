import unittest
import gzip
import io
import subprocess
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import Mock

from inatrace.deploy import database
from inatrace.deploy.common import DeployError
from inatrace.deploy import instance as instances
from inatrace.deploy import lifecycle


class DatabaseSelectionTest(unittest.TestCase):
    def test_old_instances_default_to_mysql(self):
        self.assertEqual(database.engine({}), "mysql")

    def test_postgresql_can_be_selected(self):
        self.assertEqual(database.engine({"INATRACE_DATABASE": "postgresql"}), "postgresql")

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(DeployError):
            database.engine({"INATRACE_DATABASE": "postgres"})
        self.assertTrue(any("INATRACE_DATABASE" in problem for problem in
                            instances.problems({"INATRACE_DATABASE": "postgres"})))

    def test_engine_change_is_rejected(self):
        with self.assertRaises(DeployError):
            database.require_same_engine("postgresql", "mysql")
        database.require_same_engine("mysql", "mysql")

    def test_legacy_mysql_deployment_blocks_postgresql(self):
        remote = Mock()
        remote.output.return_value = ""
        with self.assertRaises(DeployError):
            lifecycle._check_database(remote, "postgresql")

    def test_missing_directory_with_mysql_volume_blocks_postgresql(self):
        remote = Mock()
        remote.output.side_effect = ["__new__", "mysql"]
        with self.assertRaises(DeployError):
            lifecycle._check_database(remote, "postgresql")

    def test_new_postgresql_instance_is_allowed(self):
        remote = Mock()
        remote.output.side_effect = ["__new__", ""]
        lifecycle._check_database(remote, "postgresql")

    def test_postgresql_17_volume_is_not_used_by_postgresql_18(self):
        remote = Mock()
        remote.output.side_effect = ["postgresql", "yes", "17"]
        with self.assertRaisesRegex(DeployError, "requires 18"):
            lifecycle._check_database(remote, "postgresql")

    def test_unverifiable_postgresql_volume_is_rejected(self):
        remote = Mock()
        remote.output.side_effect = ["postgresql", "yes", None, None]
        with self.assertRaisesRegex(DeployError, "unknown"):
            lifecycle._check_database(remote, "postgresql")

    def test_postgresql_volume_is_checked_after_down(self):
        remote = Mock()
        remote.output.side_effect = ["postgresql", "yes", None, "18"]
        lifecycle._check_database(remote, "postgresql")
        self.assertIn("readonly", remote.output.call_args.args[0])
        self.assertIn("--pull=never", remote.output.call_args.args[0])

    def test_stopped_postgresql_17_volume_is_rejected(self):
        remote = Mock()
        remote.output.side_effect = ["postgresql", "yes", None, "17"]
        with self.assertRaisesRegex(DeployError, "requires 18"):
            lifecycle._check_database(remote, "postgresql")

    def test_postgresql_18_volume_is_accepted(self):
        remote = Mock()
        remote.output.side_effect = ["postgresql", "yes", "18"]
        lifecycle._check_database(remote, "postgresql")

    def test_postgresql_selects_its_compose_and_properties(self):
        instance = instances.Instance("demo", {"INATRACE_DATABASE": "postgresql"})
        files = instances.server_files(instance)
        self.assertIn("postgresql", str(files["compose.yaml"]))
        self.assertIn("postgresql", str(files["backend.properties"]))
        self.assertIn("postgres:", files["compose.yaml"].read_text())
        self.assertIn("postgis/postgis:18-3.7", files["compose.yaml"].read_text())
        self.assertIn("postgres:/var/lib/postgresql\n", files["compose.yaml"].read_text())
        self.assertIn("CustomPostgreSQLDialect", files["backend.properties"].read_text())

    def test_existing_instances_select_mysql_files(self):
        files = instances.server_files(instances.Instance("demo", {}))
        self.assertIn("server", str(files["compose.yaml"]))
        self.assertIn("CustomMySQLDialect", files["backend.properties"].read_text())

    def test_restore_rejects_wrong_engine_before_docker(self):
        script = Path(__file__).parents[2] / "deploy/server/restore.sh"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "restore.sh").write_text(script.read_text())
            (root / ".env").write_text("INATRACE_DATABASE=postgresql\n")
            (root / "backups").mkdir()
            stamp = "20261125T120000Z"
            with gzip.open(root / "backups" / f"{stamp}-db.sql.gz", "wt") as output:
                output.write("-- INATRACE_DATABASE=mysql\nSELECT 1;\n")
            with tarfile.open(root / "backups" / f"{stamp}-storage.tar.gz", "w:gz") as archive:
                info = tarfile.TarInfo("file")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            result = subprocess.run(["bash", str(root / "restore.sh"), stamp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("backup uses mysql but this instance uses postgresql", result.stderr)
            self.assertNotIn("stopping the backend", result.stdout)
