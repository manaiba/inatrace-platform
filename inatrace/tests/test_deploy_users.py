import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, paths
from inatrace.deploy import common, instance as instance_, users
from inatrace.tests.deploy_fixtures import VALID


class AdminTest(unittest.TestCase):
    def test_rejects_what_is_not_an_email(self):
        with self.assertRaises(common.DeployError):
            users.admin("demo", "x'; DROP TABLE User; --")

    def test_sql_text_escapes(self):
        self.assertEqual(users._sql_text("Manaíba"), "'Manaíba'")
        self.assertEqual(users._sql_text("O'Neil \\ x"), "'O''Neil \\\\ x'")

    def run_admin(self, answer: str, company: str | None, dry_run: bool):
        done = subprocess.CompletedProcess([], 0, stdout=answer)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            with mock.patch.object(users, "deployed") as deployed:
                deployed.return_value.quiet.return_value = done
                code = users.admin("demo", "a@example.org", company, dry_run)
                sql = deployed.return_value.quiet.call_args.args[1]
        return code, sql

    def test_company_is_made_once(self):
        code, sql = self.run_admin("user\t1\tUSER\tUNCONFIRMED\n", "Manaíba", dry_run=False)
        self.assertEqual(code, 0)
        self.assertIn("INSERT INTO Company", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM Company WHERE name='Manaíba')", sql)
        self.assertIn("NOT EXISTS (SELECT 1 FROM CompanyUser", sql)

    def test_dry_run_only_reads(self):
        code, sql = self.run_admin("user\t1\tUSER\tUNCONFIRMED\n", "Manaíba", dry_run=True)
        self.assertEqual(code, 0)
        self.assertNotIn("INSERT", sql)
        self.assertNotIn("UPDATE", sql)

    def test_warns_when_the_user_has_no_company(self):
        with mock.patch.object(users, "_warn_no_company") as warn:
            self.run_admin("user\t1\tUSER\tUNCONFIRMED\ncompanies\t0\n", None, dry_run=False)
            warn.assert_called_once()
            warn.reset_mock()
            self.run_admin("user\t1\tUSER\tUNCONFIRMED\ncompanies\t1\n", None, dry_run=False)
            warn.assert_not_called()

    def test_no_such_user(self):
        code, _ = self.run_admin("", None, dry_run=False)
        self.assertEqual(code, 1)


class CreateTest(unittest.TestCase):
    """--create: registers the user first, from inside the backend's container."""

    def run_create(self, *flags: str, exists: bool = False, env: dict | None = None):
        def quiet(command, body=None):
            if "register" in command:
                return subprocess.CompletedProcess([], 0, stdout='{"status":"OK"}')
            if body and body.startswith("SELECT id FROM User"):
                return subprocess.CompletedProcess([], 0, stdout="7\n" if exists else "")
            return subprocess.CompletedProcess([], 0, stdout="user\t7\tUSER\tUNCONFIRMED\ncompanies\t1\n")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(users, "deployed") as deployed:
            instance_.save(instance_.Instance("demo", dict(VALID)))
            deployed.return_value.quiet.side_effect = quiet
            result = CliRunner().invoke(cli.app, ["deploy", "admin", "demo", "new@example.org", *flags], env=env or {})
        calls = deployed.return_value.quiet.call_args_list
        registered = [call for call in calls if "register" in call.args[0]]
        return result, registered

    def test_registers_with_the_password_on_stdin(self):
        result, registered = self.run_create("--create", env={"INATRACE_DEPLOY_ADMIN_PASSWORD": "a-long-secret"})
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(len(registered), 1)
        self.assertNotIn("a-long-secret", registered[0].args[0])
        self.assertEqual(json.loads(registered[0].args[1])["password"], "a-long-secret")
        self.assertIn("is now an active system admin", result.output)

    def test_a_generated_password_is_shown_once(self):
        result, registered = self.run_create("--create", "--generate-password")
        secret = json.loads(registered[0].args[1])["password"]
        self.assertEqual(len(secret), 20)
        self.assertEqual(result.output.count(secret), 1)

    def test_nothing_to_register_when_there(self):
        result, registered = self.run_create("--create", "--generate-password", exists=True)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(registered, [])
        self.assertIn("password stays", result.output)

    def test_no_password_without_a_terminal(self):
        result, registered = self.run_create("--create")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("--generate-password", result.output)
        self.assertEqual(registered, [])

    def test_too_short_or_without_create(self):
        result, registered = self.run_create("--create", env={"INATRACE_DEPLOY_ADMIN_PASSWORD": "short"})
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(registered, [])
        result, _ = self.run_create("--generate-password")
        self.assertIn("add --create", result.output)

    def test_dry_run_registers_nothing(self):
        result, registered = self.run_create("--create", "--generate-password", "-n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("register new@example.org", result.output)
        self.assertEqual(registered, [])


if __name__ == "__main__":
    unittest.main()
