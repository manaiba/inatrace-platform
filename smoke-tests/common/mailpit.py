"""Mailpit's API, behind the gateway at /mailpit/: the e-mails the backend sent."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("smoke")


class Mailpit:
    def __init__(self, base_url: str) -> None:
        self.api = f"{base_url}/mailpit/api/v1"

    def find(self, query: str, timeout: int = 30) -> dict | None:
        """The newest message matching a Mailpit search, waiting for it to arrive."""
        log.info("waiting for an e-mail: %s", query)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            response = requests.get(f"{self.api}/search", params={"query": query}, timeout=10)
            response.raise_for_status()
            messages = response.json()["messages"]
            if messages:
                return messages[0]
            time.sleep(1)
        return None

    def html(self, message_id: str) -> str:
        response = requests.get(f"{self.api}/message/{message_id}", timeout=10)
        response.raise_for_status()
        return response.json()["HTML"]

    def delete(self, query: str) -> None:
        requests.delete(f"{self.api}/search", params={"query": query}, timeout=10).raise_for_status()
