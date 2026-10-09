import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from inatrace import registry


class SplitTest(unittest.TestCase):
    def test_hosts_and_docker_hub(self):
        self.assertEqual(registry.split("ghcr.io/agstack/inatrace-backend"),
                         ("ghcr.io", "agstack/inatrace-backend"))
        self.assertEqual(registry.split("localhost:5000/x/y"), ("localhost:5000", "x/y"))
        self.assertEqual(registry.split("caddy"), ("registry-1.docker.io", "library/caddy"))
        self.assertEqual(registry.split("bitnami/mysql"), ("registry-1.docker.io", "bitnami/mysql"))
        self.assertEqual(registry.split("docker.io/caddy"), ("registry-1.docker.io", "library/caddy"))


class VersionsTest(unittest.TestCase):
    def test_numeric_order_newest_first(self):
        found = ["latest", "2.9.0", "2.10.0", "v2.10.1", "2.10.0-rc1", "sha-abc", "1.0.0"]
        self.assertEqual(registry.versions(found), ["v2.10.1", "2.10.0", "2.9.0", "1.0.0"])


def _response(body: dict, link: str = ""):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(body).encode()
    response.headers = {"Link": link} if link else {}
    return response


def _unauthorized(url: str):
    headers = Message()
    headers["WWW-Authenticate"] = 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:x/y:pull"'
    return urllib.error.HTTPError(url, 401, "Unauthorized", headers, io.BytesIO(b""))


class TagsTest(unittest.TestCase):
    def test_anonymous_token_and_pages(self):
        calls = []

        def urlopen(request, timeout):
            url = request.full_url
            calls.append((url, request.get_header("Authorization")))
            if url.startswith("https://ghcr.io/token"):
                return _response({"token": "t0k"})
            if request.get_header("Authorization") is None:
                raise _unauthorized(url)
            if "last=" not in url:
                return _response({"tags": ["1.0.0"]}, '</v2/x/y/tags/list?n=1000&last=1.0.0>; rel="next"')
            return _response({"tags": ["1.1.0"]})

        with mock.patch.object(registry.urllib.request, "urlopen", side_effect=urlopen):
            self.assertEqual(registry.tags("ghcr.io/x/y"), ["1.0.0", "1.1.0"])
        self.assertIn("scope=repository%3Ax%2Fy%3Apull", calls[1][0])
        self.assertEqual(calls[-1][1], "Bearer t0k")

    def test_private_or_missing(self):
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b""))

        with mock.patch.object(registry.urllib.request, "urlopen", side_effect=urlopen), \
                self.assertRaises(registry.RegistryError) as raised:
            registry.tags("ghcr.io/x/y")
        self.assertIn("no such image, or a private one", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
