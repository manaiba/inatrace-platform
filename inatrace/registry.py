"""The versions an image has in its registry, through the OCI distribution API that
GHCR, Docker Hub and the others share: list the tags, with an anonymous token when
the registry asks for one (its 401 says where to get it). Public images only."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
MAX_PAGES = 20


class RegistryError(Exception):
    """The tags could not be listed; the message says why."""


def split(image: str) -> tuple[str, str]:
    """(registry host, repository) of an image name without tag, Docker's way: no host
    means Docker Hub, and a single name there is an official image (library/)."""
    first, _, rest = image.partition("/")
    if first == "docker.io":
        image = rest
    elif rest and ("." in first or ":" in first or first == "localhost"):
        return first, rest
    return "registry-1.docker.io", image if "/" in image else f"library/{image}"


def _get(url: str, token: str | None, timeout: float) -> tuple[dict, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response), response.headers.get("Link", "")


def _token(challenge: str, timeout: float) -> str:
    """An anonymous pull token from the realm a 401's WWW-Authenticate names."""
    if not challenge.lower().startswith("bearer "):
        raise RegistryError("the registry wants a login")
    fields = dict(_CHALLENGE.findall(challenge))
    realm = fields.pop("realm", "")
    if not realm:
        raise RegistryError("the registry gave no token address")
    answer, _ = _get(f"{realm}?{urllib.parse.urlencode(fields)}", None, timeout)
    token = answer.get("token") or answer.get("access_token")
    if not token:
        raise RegistryError("the registry gave no token")
    return token


def tags(image: str, timeout: float = 10) -> list[str]:
    host, repository = split(image)
    url = f"https://{host}/v2/{repository}/tags/list?n=1000"
    token = None
    found: list[str] = []
    try:
        for _ in range(MAX_PAGES):
            try:
                answer, link = _get(url, token, timeout)
            except urllib.error.HTTPError as error:
                if error.code != 401 or token:
                    raise
                token = _token(error.headers.get("WWW-Authenticate", ""), timeout)
                answer, link = _get(url, token, timeout)
            found += answer.get("tags") or []
            following = _NEXT.search(link)
            if not following:
                break
            url = urllib.parse.urljoin(url, following.group(1))
    except urllib.error.HTTPError as error:
        why = " (no such image, or a private one)" if error.code in (401, 403, 404) else ""
        raise RegistryError(f"{host} answered {error.code} for {repository}{why}") from error
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise RegistryError(f"could not ask {host}: {error}") from error
    return found


def versions(found: list[str]) -> list[str]:
    """The X.Y.Z tags (a leading v allowed), newest first."""
    numbered = [(tuple(int(part) for part in match.groups()), tag)
                for tag in found if (match := _VERSION.match(tag))]
    return [tag for _, tag in sorted(numbered, reverse=True)]
