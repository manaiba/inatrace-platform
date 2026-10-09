import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from inatrace import paths, ui
from inatrace.deploy import checks, instance as instance_, remote
from inatrace.tests.deploy_fixtures import VALID


class StatusTest(unittest.TestCase):
    def run_status(self, containers: list[dict], codes: str = "200 401") -> tuple[int, str]:
        found = "\n".join(["#containers", *(json.dumps(c) for c in containers), "#site", codes,
                           "#system", "Ubuntu 26.04 LTS", "29.9.0", "/dev/vda1 25000000000 5000000000 x 20% /",
                           "4000000000 3000000000", "0.15", "2",
                           "#backups", "20261009T134130Z-db.sql.gz",
                           "#usage", "backend 1000 501000 524288000", "interval 1000000000"])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "DEPLOY_INSTANCES", Path(tmp)):
            instance_.save(instance_.Instance("demo", dict(VALID)))
            with mock.patch.object(checks, "deployed") as deployed, \
                    mock.patch.object(ui, "out", ui.Console(record=True, width=120)) as out:
                deployed.return_value.must.return_value = found
                deployed.return_value.destination = "srv"
                code = checks.status("demo")
        return code, out.export_text()

    RUNNING = [{"Service": s, "State": "running", "Health": "", "Image": f"x/{s}:1", "Status": "Up 5 minutes"}
               for s in remote.SERVICES]

    def test_all_well(self):
        code, text = self.run_status(self.RUNNING)
        self.assertEqual(code, 0)
        self.assertIn("✓ answers", text)
        self.assertIn("backend 1", text)
        self.assertIn("disk 20%", text)
        self.assertIn("memory 25% of", text)
        self.assertIn("load 0.15 on 2 CPUs", text)
        self.assertIn("50.0%", text)    # 0.5 s of CPU in 1 s
        self.assertIn("500 MB", text)

    def test_a_stopped_or_missing_container_fails(self):
        containers = [c for c in self.RUNNING if c["Service"] != "mysql"]
        code, text = self.run_status(containers)
        self.assertEqual(code, 1)
        self.assertIn("missing", text)

    def test_down(self):
        code, text = self.run_status([], "000 000")
        self.assertEqual(code, 1)
        self.assertIn("down: no container runs", text)
        self.assertNotIn("missing", text)

    def test_a_site_that_does_not_answer_fails(self):
        code, text = self.run_status(self.RUNNING, "502 502")
        self.assertEqual(code, 1)
        self.assertIn("does not answer", text)



class CertificateTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 9, tzinfo=datetime.timezone.utc)
    SAID = ["*  expire date: Jan  7 18:43:07 2027 GMT", "*  issuer: C=US; O=Let's Encrypt; CN=YE1",
            "*  SSL certificate verify ok."]

    def test_fine(self):
        found = checks.certificate_of(self.SAID, "acme", self.NOW)
        self.assertEqual((found["issuer"], found["days"], found["trusted"], found["state"]),
                         ("Let's Encrypt", 90, True, "ok"))

    def test_renewing_late_then_wrong(self):
        self.assertEqual(checks.certificate_of(self.SAID, "acme", self.NOW + datetime.timedelta(days=80))["state"],
                         "renew")
        self.assertEqual(checks.certificate_of(self.SAID, "acme", self.NOW + datetime.timedelta(days=85))["state"],
                         "bad")
        untrusted = [line for line in self.SAID if "verify" not in line] + ["*  SSL certificate verify result: x"]
        self.assertEqual(checks.certificate_of(untrusted, "acme", self.NOW)["state"], "bad")

    def test_staging_and_self_signed_are_fine_untrusted(self):
        untrusted = self.SAID[:2]
        self.assertEqual(checks.certificate_of(untrusted, "acme-staging", self.NOW)["state"], "ok")
        self.assertEqual(checks.certificate_of(untrusted, "internal", self.NOW)["state"], "ok")

    def test_nothing_said(self):
        self.assertIsNone(checks.certificate_of([], "acme", self.NOW))


if __name__ == "__main__":
    unittest.main()
