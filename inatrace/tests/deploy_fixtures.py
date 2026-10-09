"""What the deploy tests share: valid settings, a ready server, no registry."""

from unittest import mock

from inatrace import registry
from inatrace.deploy import remote

VALID = {
    "INATRACE_SSH": "admin@203.0.113.10",
    "INATRACE_SITE": "inatrace.example.org",
    "INATRACE_BACKEND_VERSION": "2.40.3",
    "INATRACE_FRONTEND_VERSION": "2.34.0",
    "INATRACE_DB_PASSWORD": "a", "INATRACE_DB_ROOT_PASSWORD": "b", "INATRACE_JWT_KEY": "c",
}


READY = remote.Probe(True, "Ubuntu 26.04 LTS", "ubuntu debian", ready=True)


# No wizard test reaches a real registry: listing fails, and the wizard asks for the version.
OFFLINE = mock.patch.object(registry, "tags", new=mock.Mock(side_effect=registry.RegistryError("offline")))
