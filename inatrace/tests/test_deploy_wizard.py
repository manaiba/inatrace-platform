import tempfile
import unittest
from pathlib import Path
from unittest import mock

from typer.testing import CliRunner

from inatrace import cli, config, paths, registry
from inatrace.deploy import remote, wizard
from inatrace.tests.deploy_fixtures import OFFLINE, READY


@OFFLINE
class WizardTest(unittest.TestCase):
    def run_wizard(self, answers: list[str], tmp: str):
        with mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(remote.Remote, "probe", return_value=READY), \
                mock.patch.object(wizard, "public_address", return_value="203.0.113.10"):
            result = CliRunner().invoke(cli.app, ["deploy", "init", "demo"], input="\n".join(answers) + "\n")
            env = Path(tmp) / "demo" / ".env"
            return result, config.parse(env.read_text()) if env.exists() else {}

    def test_by_ip_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, values = self.run_wizard(
                ["admin@203.0.113.10", "", "", "", "n", "", "2.40.3", "", "2.34.0", "n", "", "y"], tmp)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(values["INATRACE_SITE"], "203.0.113.10")  # the detected one
            self.assertEqual(values["INATRACE_TLS"], "internal")
            self.assertEqual(values["INATRACE_FRONT"], "direct")
            self.assertEqual(values["INATRACE_BACKEND_IMAGE"], "ghcr.io/agstack/inatrace-backend")
            self.assertEqual(values["INATRACE_MAIL_ENABLED"], "false")
            self.assertEqual(values["INATRACE_MONITORING"], "on")  # a new instance's default
            self.assertEqual(len(values["INATRACE_JWT_KEY"]), 48)

            # Again: the answers so far are the defaults, the secrets stay.
            again, kept = self.run_wizard(["", "", "", "", "n", "", "", "", "", "n", "", "y"], tmp)
            self.assertEqual(again.exit_code, 0, again.output)
            self.assertEqual(kept, values)

    def test_cloudflare_with_secret_and_mail(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, values = self.run_wizard(
                ["admin@203.0.113.10", "inatrace.example.org", "cdn", "cloudflare", "y", "n",
                 "", "2.40.3", "", "2.34.0",
                 "y", "smtp.example.org", "465", "mailer", "pw", "", "n", "y"], tmp)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(values["INATRACE_TLS"], "acme")
            self.assertEqual(values["INATRACE_CDN_IP_HEADER"], "CF-Connecting-IP")
            self.assertRegex(values["INATRACE_ORIGIN_SECRET"], r"^[0-9a-f]{48}$")
            self.assertEqual(values["INATRACE_MAIL_SSL"], "true")
            self.assertEqual(values["INATRACE_MAIL_FROM"], "inatrace@inatrace.example.org")
            self.assertEqual(values["INATRACE_MONITORING"], "off")

    def test_nothing_happens_without_confirming(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, values = self.run_wizard(
                ["admin@203.0.113.10", "", "", "", "n", "", "2.40.3", "", "2.34.0", "n", "", "n"], tmp)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(values, {})
            self.assertIn("nothing was changed", result.output)

    FLAGS = ["--ssh", "admin@203.0.113.10", "--ip", "203.0.113.10", "--front", "direct",
             "--no-swagger", "--backend-image", "ghcr.io/x/backend", "--backend-version", "2.40.3",
             "--frontend-image", "ghcr.io/x/frontend", "--frontend-version", "2.34.0", "--no-mail",
             "--no-monitoring"]

    def init_with(self, flags: list[str], tmp: str, stdin: str = ""):
        with mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)), \
                mock.patch.object(remote.Remote, "probe", return_value=READY):
            result = CliRunner().invoke(cli.app, ["deploy", "init", "demo", *flags], input=stdin)
        env = Path(tmp) / "demo" / ".env"
        return result, config.parse(env.read_text()) if env.exists() else {}

    def test_a_known_cdn_needs_no_header(self):
        flags = [f for f in self.FLAGS if f not in ("--ip", "203.0.113.10")]
        flags = [*flags, "--domain", "inatrace.example.org", "--front", "cdn", "--cdn", "cloudflare",
                 "--origin-secret", "--auto-approve"]
        flags.remove("direct")
        flags.remove("--front")
        with tempfile.TemporaryDirectory() as tmp:
            result, values = self.init_with(flags, tmp)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(values["INATRACE_CDN_IP_HEADER"], "CF-Connecting-IP")
            self.assertRegex(values["INATRACE_ORIGIN_SECRET"], r"^[0-9a-f]{48}$")

    def test_restore_offered_only_with_backups_here(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.init_with([*self.FLAGS, "--auto-approve"], tmp)
            self.assertNotIn("Restore one", result.output)
            local = Path(tmp) / "demo" / "backups"
            local.mkdir()
            for stamp in ("20261008T000000Z", "20261009T000000Z"):
                (local / f"{stamp}-db.sql.gz").write_bytes(b"x")
            result, _ = self.init_with([*self.FLAGS, "--auto-approve"], tmp)
            self.assertEqual(result.exit_code, 1)  # no terminal: the question needs its flag
            self.assertIn("--restore", result.output)
            result, _ = self.init_with([*self.FLAGS, "--restore", "newest", "--auto-approve"], tmp)
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("inatrace deploy up demo --restore 20261009T000000Z", " ".join(result.output.split()))
            result, _ = self.init_with([*self.FLAGS, "--restore", "none", "--auto-approve"], tmp)
            self.assertNotIn("--restore 2026", result.output)

    def test_every_answer_as_a_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, values = self.init_with([*self.FLAGS, "--auto-approve"], tmp)  # nothing on stdin
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual((values["INATRACE_SITE"], values["INATRACE_TLS"]), ("203.0.113.10", "internal"))
            self.assertEqual(values["INATRACE_BACKEND_IMAGE"], "ghcr.io/x/backend")
            self.assertIn("(--backend-version)", result.output)

    def test_a_missing_answer_names_its_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            flags = [f for f in self.FLAGS if f not in ("--no-mail",)]
            result, values = self.init_with([*flags, "--auto-approve"], tmp)
            self.assertEqual(result.exit_code, 1)
            self.assertIn("--mail or --no-mail", " ".join(result.output.split()))  # rich wraps lines
            self.assertEqual(values, {})

    def test_a_flag_is_checked_like_an_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            flags = ["--ip" if f == "--ip" else f for f in self.FLAGS]
            flags[flags.index("--ip") + 1] = "not-an-ip"
            result, _ = self.init_with([*flags, "--auto-approve"], tmp)
            self.assertEqual(result.exit_code, 1)
            self.assertIn("--ip 'not-an-ip': an IP address", " ".join(result.output.split()))

    def test_bad_name(self):
        result = CliRunner().invoke(cli.app, ["deploy", "init", "Not OK"])
        self.assertEqual(result.exit_code, 1)


class VersionSelectorTest(unittest.TestCase):
    TAGS = ["latest", "main", *[f"2.{minor}.0" for minor in range(30, 42)]]

    def choose(self, answers: list[str], current: str = "") -> tuple[str, str]:
        from typer.testing import CliRunner as Runner
        import typer
        app = typer.Typer()
        chosen = {}

        @app.command()
        def run() -> None:
            chosen["v"] = wizard._ask_version(wizard.Asker(), "backend-version", "Backend",
                                              "ghcr.io/x/backend", current)

        with mock.patch.object(registry, "tags", return_value=self.TAGS):
            result = Runner().invoke(app, [], input="\n".join(answers) + "\n")
        self.assertEqual(result.exit_code, 0, result.output)
        return chosen["v"], result.output

    def test_five_newest_then_more(self):
        version, output = self.choose(["m", "7"])
        self.assertEqual(version, "2.35.0")  # 7th newest of 2.41.0 down to 2.30.0
        self.assertIn("2.41.0  newest", output)
        self.assertNotIn("latest", output.split("version")[0])
        first_page = output.split("or m for more")[0]
        self.assertNotIn("2.36.0", first_page)

    def test_a_typed_tag(self):
        version, output = self.choose(["main"])
        self.assertEqual(version, "main")
        self.assertIn("taken as is", output)

    def test_a_flag_lists_nothing(self):
        with mock.patch.object(registry, "tags") as tags:
            chosen = wizard._ask_version(wizard.Asker({"backend-version": "2.40.3"}), "backend-version",
                                         "Backend", "ghcr.io/x/backend", "")
        self.assertEqual(chosen, "2.40.3")
        tags.assert_not_called()

    def test_current_is_the_default_and_marked(self):
        version, output = self.choose([""], current="2.39.0")
        self.assertEqual(version, "2.39.0")
        self.assertIn("2.39.0  current", output)


if __name__ == "__main__":
    unittest.main()
