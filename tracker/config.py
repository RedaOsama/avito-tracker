"""Settings come from environment variables (GitHub Secrets) or a local .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass

REQUIRED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EMAIL_ADDRESS", "EMAIL_APP_PASSWORD")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    email_address: str
    email_password: str
    imap_host: str = "imap.gmail.com"
    imap_folder: str = "INBOX"
    senders: tuple[str, ...] = ("avito.ru",)
    state_file: str = "state.json"
    max_ads_per_email: int = 10
    max_emails_per_run: int = 10
    forward_unparsed: bool = True


def load_config() -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    env = {k: os.environ.get(k, "").strip() for k in os.environ}
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise SystemExit("Missing settings: " + ", ".join(missing) + " (see .env.example / README)")

    def opt(name: str, default: str) -> str:
        return env.get(name) or default

    return Config(
        telegram_token=env["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=env["TELEGRAM_CHAT_ID"],
        email_address=env["EMAIL_ADDRESS"],
        # Google shows app passwords as "abcd efgh ijkl mnop"; spaces aren't part of it.
        email_password=env["EMAIL_APP_PASSWORD"].replace(" ", ""),
        imap_host=opt("IMAP_HOST", "imap.gmail.com"),
        imap_folder=opt("IMAP_FOLDER", "INBOX"),
        senders=tuple(s.strip() for s in opt("SENDER_FILTER", "avito.ru").split(",") if s.strip()),
        state_file=opt("STATE_FILE", "state.json"),
        max_ads_per_email=int(opt("MAX_ADS_PER_EMAIL", "10")),
        max_emails_per_run=int(opt("MAX_EMAILS_PER_RUN", "10")),
        forward_unparsed=opt("FORWARD_UNPARSED", "true").lower() in ("1", "true", "yes"),
    )
