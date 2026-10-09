import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from inatrace import paths
from inatrace.deploy import instance as instance_
from inatrace.tests.deploy_fixtures import VALID


class ProblemsTest(unittest.TestCase):
    def check(self, **changes) -> list[str]:
        return instance_.problems({**VALID, **changes})

    def test_valid(self):
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(INATRACE_SITE="203.0.113.10", INATRACE_TLS="internal"), [])

    def test_backup_days(self):
        self.assertEqual(self.check(INATRACE_BACKUP_DAYS="7"), [])
        self.assertTrue(self.check(INATRACE_BACKUP_DAYS="0"))
        self.assertTrue(self.check(INATRACE_BACKUP_DAYS="a week"))

    def test_required(self):
        self.assertIn("INATRACE_BACKEND_VERSION is empty", self.check(INATRACE_BACKEND_VERSION=""))

    def test_staging_like_acme(self):
        self.assertEqual(self.check(INATRACE_TLS="acme-staging"), [])
        self.assertTrue(self.check(INATRACE_SITE="203.0.113.10", INATRACE_TLS="acme-staging"))

    def test_site(self):
        self.assertTrue(self.check(INATRACE_SITE="https://inatrace.example.org"))
        self.assertTrue(self.check(INATRACE_SITE="203.0.113.10"))  # acme needs a domain

    def test_cdn(self):
        self.assertTrue(self.check(INATRACE_FRONT="cdn"))
        self.assertEqual(self.check(INATRACE_FRONT="cdn", INATRACE_CDN_IP_HEADER="CF-Connecting-IP"), [])
        self.assertTrue(self.check(INATRACE_FRONT="cdn", INATRACE_CDN_IP_HEADER="CF-Connecting-IP",
                                   INATRACE_SITE="203.0.113.10", INATRACE_TLS="internal"))

    def test_secret_is_alphanumeric(self):
        # It ends up inside a Caddyfile expression.
        self.assertTrue(self.check(INATRACE_ORIGIN_SECRET='a"b'))
        self.assertEqual(self.check(INATRACE_ORIGIN_SECRET="abc123"), [])

    def test_mail(self):
        self.assertTrue(self.check(INATRACE_MAIL_ENABLED="true"))


class EnvTest(unittest.TestCase):
    def test_render_keeps_comments_and_fills_keys(self):
        example = "# about A\nA=\n#B=default\n# about C\n#C=\n"
        text = instance_.render_env(example, {"A": "1", "C": "3", "D": "4"})
        self.assertEqual(text, "# about A\nA=1\n#B=default\n# about C\nC=3\nD=4\n")

    def test_example_has_every_key_the_wizard_writes(self):
        keys = set(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", instance_.EXAMPLE.read_text(), re.M))
        # The package's code, but its CLI: its options' environment variables are not settings.
        source = "".join(p.read_text() for p in Path(instance_.__file__).parent.glob("*.py") if p.name != "cli.py")
        written = set(re.findall(r'"(INATRACE_[A-Z0-9_]*[A-Z0-9])"', source))
        self.assertLessEqual(written - {"INATRACE_BACKEND_IMAGE", "INATRACE_FRONTEND_IMAGE"} - keys, set())

    def test_compose_reads_the_example_keys(self):
        compose = (paths.DEPLOY_SERVER / "compose.yaml").read_text()
        keys = set(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", instance_.EXAMPLE.read_text(), re.M))
        cli_only = {"INATRACE_SSH", "INATRACE_BACKUP_DAYS", "INATRACE_BACKUP_SCHEDULE", "INATRACE_MONITORING",
                    "COMPOSE_PROFILES"}
        for key in keys - cli_only:
            self.assertIn("${" + key, compose, key)

    def test_secret(self):
        self.assertRegex(instance_.new_secret(), r"^[0-9a-f]{48}$")


class ServerFilesTest(unittest.TestCase):
    def test_server_dir_env_and_local_properties(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)):
            instance = instance_.Instance("demo", dict(VALID))
            instance_.save(instance)
            (instance.dir / "compose.override.yaml").write_text("services: {}\n")
            (instance.dir / "Caddyfile").write_text("# mine\n")
            (instance.dir / "backups").mkdir()
            (instance.dir / "backups" / "20261009T000000Z-db.sql.gz").write_bytes(b"x")
            files = instance_.server_files(instance)
            self.assertFalse([name for name in files if name.startswith("backups")])  # they stay here
            self.assertEqual(oct(instance.env_file.stat().st_mode & 0o777), "0o600")
            self.assertEqual(files["Caddyfile"], instance.dir / "Caddyfile")  # the instance wins
            self.assertEqual(files[instance_.LOCAL_PROPERTIES], instance.dir / instance_.LOCAL_PROPERTIES)
        for name in ("compose.yaml", "backend.properties", "backup.sh", "restore.sh",
                     "caddy/tls-acme.caddy", ".env", "compose.override.yaml"):
            self.assertIn(name, files)

    def test_every_choice_has_its_caddy_file(self):
        caddy = paths.DEPLOY_SERVER / "caddy"
        for prefix, choices in (("tls", instance_.TLS), ("front", instance_.FRONTS), ("swagger", ("on", "off"))):
            for choice in choices:
                self.assertTrue((caddy / f"{prefix}-{choice}.caddy").is_file(), f"{prefix}-{choice}")


if __name__ == "__main__":
    unittest.main()
