"""One check: answer bot commands, forward ads from new Avito emails, save state.

Run with:  python -m tracker.main
Logs print counts only (GitHub Actions logs are public for public repos).
"""
from __future__ import annotations

import html
import imaplib
import sys
import time
from datetime import datetime, timezone
from email.message import Message

from .avito_email import Ad, email_subject, parse_ads
from .config import Config, load_config
from .mail import Mailbox
from .state import load_state, save_state
from .telegram import Telegram, TelegramError

SEEN_LIMIT = 1000
SEND_PAUSE = 1.0  # seconds between Telegram messages

HELP_TEXT = (
    "<b>Avito → Telegram tracker</b>\n"
    "I forward new ads from Avito's saved-search emails.\n\n"
    "/status – tracker status\n"
    "/pause – stop forwarding (emails that arrive while paused are skipped)\n"
    "/resume – forward again\n"
    "/test – re-send the ads from the latest Avito email\n"
    "/help – this message\n\n"
    "Commands are answered on the next check (about 5–15 min on GitHub)."
)


def format_ad(ad: Ad, test: bool = False) -> str:
    head = "🧪 Test: " if test else "🆕 "
    lines = [f"{head}<b>{html.escape(ad.title or 'New ad')}</b>"]
    if ad.price:
        lines.append(f"💰 {html.escape(ad.price)}")
    lines.append(f'🔗 <a href="{html.escape(ad.url, quote=True)}">Open on Avito</a>')
    return "\n".join(lines)


def send_ad(tg: Telegram, ad: Ad, test: bool = False) -> None:
    text = format_ad(ad, test)
    if ad.image:
        try:
            tg.send_photo(ad.image, text)
            return
        except TelegramError:
            pass  # e.g. Telegram can't download the image: fall back to text
    tg.send_message(text)


def remember(state: dict, ad_id: str) -> None:
    seen = state["seen_ads"]
    if ad_id not in seen:
        seen.append(ad_id)
    del seen[:-SEEN_LIMIT]


def forward_email(tg: Telegram, cfg: Config, state: dict, msg: Message, test: bool = False) -> int:
    """Send the not-yet-seen ads in ``msg``; in test mode send them all. Returns ads sent."""
    ads = parse_ads(msg)
    if not ads:
        subject = html.escape(email_subject(msg))
        if test:
            tg.send_message(f"🧪 Test: the latest Avito email has no ads I could read.\n<b>{subject}</b>")
        elif cfg.forward_unparsed:
            tg.send_message(f"📩 New Avito email (no ads detected):\n<b>{subject}</b>")
        return 0

    seen = set(state["seen_ads"])
    new = ads if test else [a for a in ads if a.id not in seen]
    shown, extra = new[: cfg.max_ads_per_email], new[cfg.max_ads_per_email:]
    for ad in shown:
        send_ad(tg, ad, test)
        if not test:
            remember(state, ad.id)
            state["stats"]["forwarded"] += 1
            state["stats"]["last_forward_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        time.sleep(SEND_PAUSE)
    if extra:
        tg.send_message(f"…and {len(extra)} more new ads in this email. Open Avito to see them all.")
        if not test:
            for ad in extra:
                remember(state, ad.id)
    return len(shown)


def read_commands(tg: Telegram, cfg: Config, state: dict) -> list[str]:
    """Commands sent by the owner since the last run (others' messages are ignored)."""
    commands = []
    for update in tg.get_updates(state["tg_offset"]):
        state["tg_offset"] = update["update_id"] + 1
        message = update.get("message") or {}
        if str(message.get("chat", {}).get("id")) != cfg.telegram_chat_id:
            continue
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            commands.append(text.split()[0].split("@")[0].lower())
    return commands


def status_text(cfg: Config, state: dict) -> str:
    last = state["stats"]["last_forward_at"] or "never"
    return (
        "<b>Tracker status</b>\n"
        f"State: {'⏸ paused' if state['paused'] else '▶️ running'}\n"
        f"Watching: {html.escape(cfg.email_address)} / {html.escape(cfg.imap_folder)}\n"
        f"Emails from: {html.escape(', '.join(cfg.senders))}\n"
        f"Ads forwarded: {state['stats']['forwarded']} (last: {html.escape(last)})"
    )


def check_mail(tg: Telegram, cfg: Config, state: dict, want_test: bool) -> int:
    imap = state["imap"]
    with Mailbox(cfg.imap_host, cfg.email_address, cfg.email_password, cfg.imap_folder) as mb:
        uidvalidity, uidnext = mb.status()
        mb.select()

        uids: list[int] = []
        if imap["uidvalidity"] != uidvalidity or imap["last_uid"] is None:
            # First run (or the server re-numbered the folder): start from "now"
            # so old emails aren't all forwarded at once.
            first = imap["uidvalidity"] is None
            imap["uidvalidity"], imap["last_uid"] = uidvalidity, uidnext - 1
            tg.send_message(
                "✅ Connected to your mailbox. New Avito emails will be forwarded from now on. "
                "Send /test to try it on the latest one."
                if first else
                "ℹ️ Your mailbox was re-indexed by the provider; continuing with new emails from now on."
            )
        else:
            uids = mb.find_from(cfg.senders, imap["last_uid"] + 1)[: cfg.max_emails_per_run]

        for uid in uids:
            msg = mb.fetch(uid)
            if msg is not None and not state["paused"]:
                forward_email(tg, cfg, state, msg)
            imap["last_uid"] = uid  # only after it was forwarded successfully

        if want_test:
            latest = mb.find_from(cfg.senders, 1)
            msg = mb.fetch(latest[-1]) if latest else None
            if msg is None:
                tg.send_message("🧪 Test: no emails from Avito found in this mailbox folder yet.")
            else:
                forward_email(tg, cfg, state, msg, test=True)
    return len(uids)


def report_error(tg: Telegram, state: dict, text: str, exc: Exception) -> None:
    print(f"ERROR: {text} ({type(exc).__name__}: {exc})")
    if state.get("last_error") == text:
        return  # already told the user; don't repeat every 5 minutes
    state["last_error"] = text
    try:
        tg.send_message("⚠️ " + html.escape(text))
    except TelegramError:
        pass


def run() -> int:
    cfg = load_config()
    state = load_state(cfg.state_file)
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    try:
        commands = read_commands(tg, cfg, state)
        for cmd in commands:
            if cmd in ("/start", "/help"):
                tg.send_message(HELP_TEXT)
            elif cmd == "/pause":
                state["paused"] = True
                tg.send_message("⏸ Paused. Avito emails arriving now will be skipped. Send /resume to continue.")
            elif cmd == "/resume":
                state["paused"] = False
                tg.send_message("▶️ Resumed. New Avito ads will be forwarded again.")
            elif cmd not in ("/test", "/status"):
                tg.send_message(f"Unknown command {html.escape(cmd)}. Send /help.")

        checked = check_mail(tg, cfg, state, want_test="/test" in commands)

        if "/status" in commands:
            tg.send_message(status_text(cfg, state))
        if state.get("last_error"):
            state["last_error"] = None
            tg.send_message("✅ The tracker is working again.")
        print(f"OK: {len(commands)} command(s), {checked} new Avito email(s).")
        return 0
    except imaplib.IMAP4.error as e:
        report_error(tg, state, "Can't read your mailbox. Check EMAIL_ADDRESS / EMAIL_APP_PASSWORD "
                                "(and IMAP_FOLDER if you changed it).", e)
    except OSError as e:
        report_error(tg, state, "Network problem while reading your mailbox; will retry next run.", e)
    except TelegramError as e:
        print(f"ERROR: Telegram: {e}")  # can't report a Telegram failure via Telegram
    finally:
        save_state(cfg.state_file, state)
    return 1


if __name__ == "__main__":
    sys.exit(run())
