"""Minimal Telegram Bot API client."""
from __future__ import annotations

import time

import requests


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.chat_id = chat_id
        self._session = requests.Session()

    def _call(self, method: str, **params):
        for _ in range(3):
            try:
                r = self._session.post(self._base + method, json=params, timeout=30)
                data = r.json()
            except (requests.RequestException, ValueError) as e:
                # Never include the request URL in errors: it contains the bot token.
                raise TelegramError(f"{method}: {type(e).__name__}") from None
            if data.get("ok"):
                return data["result"]
            if r.status_code == 429:
                time.sleep(int(data.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            raise TelegramError(f"{method}: {data.get('description', r.status_code)}")
        raise TelegramError(f"{method}: rate limited")

    def send_message(self, text: str) -> None:
        self._call("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
                   disable_web_page_preview=True)

    def send_photo(self, photo_url: str, caption: str) -> None:
        self._call("sendPhoto", chat_id=self.chat_id, photo=photo_url, caption=caption[:1024],
                   parse_mode="HTML")

    def get_updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
