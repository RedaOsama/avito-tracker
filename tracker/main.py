"""One check: answer bot commands, forward matching ads from new Avito emails, save state.

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

from . import filters
from .avito_email import Ad, email_subject, parse_ads
from .config import Config, load_config
from .mail import Mailbox
from .state import load_state, save_state
from .telegram import Telegram, TelegramError

SEEN_LIMIT = 1000
SEND_PAUSE = 1.0  # seconds between Telegram messages

HELP_TEXT = (
    "<b>Avito → Telegram tracker</b>\n"
    "I read Avito's saved-search emails and send you the new ads that match your keywords.\n\n"
    "<b>Keywords</b>\n"
    "/add iphone 15 pro – ads containing all these words\n"
    "/add iphone 15 -чехол -разбит – …but not these words\n"
    "/add iphone 15 &lt;70000 – …and price up to 70 000 ₽ (&gt;N = at least N; 70k also works)\n"
    "/list – your keyword rules\n"
    "/remove 2 – delete rule #2 (/remove all deletes every rule)\n"
    "With no rules, every new ad is sent.\n\n"
    "<b>Other</b>\n"
    "/status – tracker status\n"
    "/pause, /resume – stop / restart notifications\n"
    "/test – re-check the latest Avito email\n\n"
    "Replies come on the next check (about 5–15 min)."
)


# ---------- sending ads ----------

def format_ad(ad: Ad, test: bool = False, rule: dict | None = None) -> str:
    head = "🧪 Test: " if test else "🆕 "
    lines = [f"{head}<b>{html.escape(ad.title or 'New ad')}</b>"]
    if ad.price:
        lines.append(f"💰 {html.escape(ad.price)}")
    if rule:
        lines.append(f"🔎 Rule #{rule['id']}: {filters.describe(rule)}")
    lines.append(f'🔗 <a href="{html.escape(ad.url, quote=True)}">avito.ru/{html.escape(ad.id)}</a>'
                 " (tap to open the ad)")
    return "\n".join(lines)


def send_ad(tg: Telegram, ad: Ad, test: bool = False, rule: dict | None = None) -> None:
    text = format_ad(ad, test, rule)
    if ad.image:
        try:
            tg.send_photo(ad.image, text)
            return
        except TelegramError:
            pass  # e.g. Telegram can't download the image: fall back to text
    tg.send_message(text)


def is_account_email(subject: str) -> bool:
    """Avito account emails (email change, login codes, …) aren't worth a notification."""
    s = subject.lower()
    return any(word in s for word in ("смена почты", "подтвержд", "код ", "пароль", "вход в"))


def remember(state: dict, ad_id: str) -> None:
    seen = state["seen_ads"]
    if ad_id not in seen:
        seen.append(ad_id)
    del seen[:-SEEN_LIMIT]


def forward_email(tg: Telegram, cfg: Config, state: dict, msg: Message, test: bool = False) -> int:
    """Send the new ads in ``msg`` that match the keyword rules. Returns ads sent.

    In test mode every ad in the email is shown (already-seen ones too), with
    a note saying whether it matches a rule; nothing is marked as seen.
    """
    ads = parse_ads(msg)
    if not ads:
        subject = html.escape(email_subject(msg))
        if test:
            tg.send_message(f"🧪 Test: the latest Avito email has no ads I could read.\n<b>{subject}</b>")
        elif cfg.forward_unparsed and not is_account_email(subject):
            tg.send_message(f"📩 New Avito email (no ads detected):\n<b>{subject}</b>\n"
                            "Check it in Gmail, and send /test so the reader can be fixed if needed.")
        return 0

    rules = state["rules"]
    seen = set(state["seen_ads"])

    if test:
        for ad in ads[: cfg.max_ads_per_email]:
            rule = filters.first_match(rules, ad.search_text, ad.price_value) if rules else None
            send_ad(tg, ad, test=True, rule=rule)
            if rules and rule is None:
                tg.send_message("↑ doesn't match any of your rules, so it would not be sent.")
            time.sleep(SEND_PAUSE)
        return min(len(ads), cfg.max_ads_per_email)

    to_send: list[tuple[Ad, dict | None]] = []
    for ad in ads:
        if ad.id in seen:
            continue
        remember(state, ad.id)  # non-matching ads are remembered too, so they're never re-checked
        if not rules:
            to_send.append((ad, None))
        else:
            rule = filters.first_match(rules, ad.search_text, ad.price_value)
            if rule:
                to_send.append((ad, rule))

    shown, extra = to_send[: cfg.max_ads_per_email], to_send[cfg.max_ads_per_email:]
    for ad, rule in shown:
        send_ad(tg, ad, rule=rule)
        state["stats"]["forwarded"] += 1
        state["stats"]["last_forward_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        time.sleep(SEND_PAUSE)
    if extra:
        tg.send_message(f"…and {len(extra)} more matching ads in this email. Open Avito to see them all.")
    return len(shown)


# ---------- bot commands ----------

def read_commands(tg: Telegram, cfg: Config, state: dict) -> list[tuple[str, str]]:
    """(command, arguments) sent by the owner since the last run; others are ignored."""
    commands = []
    for update in tg.get_updates(state["tg_offset"]):
        state["tg_offset"] = update["update_id"] + 1
        message = update.get("message") or {}
        if str(message.get("chat", {}).get("id")) != cfg.telegram_chat_id:
            continue
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            name, _, args = text.partition(" ")
            commands.append((name.split("@")[0].lower(), args.strip()))
    return commands


def rules_text(state: dict) -> str:
    if not state["rules"]:
        return "No keyword rules yet, so every new ad is sent.\nAdd one with e.g. /add iphone 15 pro"
    lines = ["<b>Your keyword rules</b> (an ad is sent if it matches any):"]
    lines += [f"#{r['id']}: {filters.describe(r)}" for r in state["rules"]]
    return "\n".join(lines)


def handle_command(tg: Telegram, state: dict, cmd: str, args: str) -> None:
    """Commands that don't need the mailbox. /test and /status are handled in run()."""
    if cmd in ("/start", "/help"):
        tg.send_message(HELP_TEXT)
    elif cmd == "/add":
        if not args:
            tg.send_message("Tell me what to look for, e.g.\n/add iphone 15 pro -чехол &lt;90000")
            return
        if len(state["rules"]) >= filters.MAX_RULES:
            tg.send_message(f"You already have {filters.MAX_RULES} rules. Remove some first (/list).")
            return
        try:
            rule = filters.parse_rule(args)
        except ValueError as e:
            tg.send_message(f"❌ {html.escape(str(e))}")
            return
        rule["id"] = state["next_rule_id"]
        state["next_rule_id"] += 1
        state["rules"].append(rule)
        tg.send_message(f"✅ Rule #{rule['id']} added: {filters.describe(rule)}")
    elif cmd == "/list":
        tg.send_message(rules_text(state))
    elif cmd == "/remove":
        if args.lower() == "all":
            state["rules"].clear()
            tg.send_message("🗑 All rules removed. Every new ad will be sent.")
            return
        ids = {int(x) for x in args.replace(",", " ").replace("#", " ").split() if x.isdigit()}
        before = len(state["rules"])
        state["rules"] = [r for r in state["rules"] if r["id"] not in ids]
        if len(state["rules"]) == before:
            tg.send_message("No rule with that number. Send /list to see them, then e.g. /remove 2")
        else:
            tg.send_message(f"🗑 Removed. {rules_text(state)}")
    elif cmd == "/pause":
        state["paused"] = True
        tg.send_message("⏸ Paused. Avito emails arriving now will be skipped. Send /resume to continue.")
    elif cmd == "/resume":
        state["paused"] = False
        tg.send_message("▶️ Resumed. Matching ads will be sent again.")
    elif cmd not in ("/test", "/status"):
        tg.send_message(f"Unknown command {html.escape(cmd)}. Send /help.")


def status_text(cfg: Config, state: dict) -> str:
    last = state["stats"]["last_forward_at"] or "never"
    rules = len(state["rules"])
    return (
        "<b>Tracker status</b>\n"
        f"State: {'⏸ paused' if state['paused'] else '▶️ running'}\n"
        f"Watching: {html.escape(cfg.email_address)} / {html.escape(cfg.imap_folder)}\n"
        f"Keyword rules: {rules if rules else 'none (every ad is sent)'}\n"
        f"Ads sent: {state['stats']['forwarded']} (last: {html.escape(last)})"
    )


# ---------- mailbox ----------

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
                "✅ Connected to your mailbox. New Avito emails will be checked from now on. "
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
        for cmd, args in commands:
            handle_command(tg, state, cmd, args)
        names = {cmd for cmd, _ in commands}

        checked = check_mail(tg, cfg, state, want_test="/test" in names)

        if "/status" in names:
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
