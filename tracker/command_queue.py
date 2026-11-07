"""Client for the Cloudflare Worker that queues Telegram commands (see worker/)."""
from __future__ import annotations

import requests


class QueueError(Exception):
    pass


class CommandQueue:
    def __init__(self, base_url: str, secret: str):
        self.base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {secret}"}

    def pending(self) -> list[dict]:
        try:
            r = requests.get(self.base + "/pending", headers=self._headers, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise QueueError(f"pending: {type(e).__name__}") from None

    def ack(self, max_update_id: int) -> None:
        try:
            r = requests.post(self.base + "/ack", headers=self._headers,
                              json={"max_update_id": max_update_id}, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            raise QueueError(f"ack: {type(e).__name__}") from None
