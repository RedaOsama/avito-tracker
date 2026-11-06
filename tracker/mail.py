"""Read-only IMAP access: find and fetch emails by sender, without marking them read."""
from __future__ import annotations

import email
import imaplib
import re
from email import policy
from email.message import Message


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailbox:
    def __init__(self, host: str, user: str, password: str, folder: str = "INBOX"):
        self.host, self.user, self.password, self.folder = host, user, password, folder
        self.conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "Mailbox":
        self.conn = imaplib.IMAP4_SSL(self.host, timeout=30)
        self.conn.login(self.user, self.password)
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass

    def status(self) -> tuple[int, int]:
        """(UIDVALIDITY, UIDNEXT) of the folder."""
        typ, data = self.conn.status(_quote(self.folder), "(UIDNEXT UIDVALIDITY)")
        if typ != "OK":
            raise imaplib.IMAP4.error(f"Cannot open folder {self.folder!r}")
        raw = b" ".join(d for d in data if isinstance(d, bytes))
        uidnext = re.search(rb"UIDNEXT (\d+)", raw)
        uidvalidity = re.search(rb"UIDVALIDITY (\d+)", raw)
        if not (uidnext and uidvalidity):
            raise imaplib.IMAP4.error("Unexpected STATUS response")
        return int(uidvalidity.group(1)), int(uidnext.group(1))

    def select(self) -> None:
        typ, _ = self.conn.select(_quote(self.folder), readonly=True)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"Cannot select folder {self.folder!r}")

    def find_from(self, senders: tuple[str, ...], min_uid: int) -> list[int]:
        """UIDs >= min_uid of emails whose From contains any of ``senders``, ascending."""
        uids: set[int] = set()
        for sender in senders:
            typ, data = self.conn.uid("SEARCH", "UID", f"{max(min_uid, 1)}:*", "FROM", _quote(sender))
            if typ != "OK":
                raise imaplib.IMAP4.error("IMAP search failed")
            uids.update(int(x) for x in (data[0] or b"").split())
        # "N:*" always includes the newest message even when its UID < N, so filter.
        return sorted(u for u in uids if u >= min_uid)

    def fetch(self, uid: int) -> Message | None:
        typ, data = self.conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                return email.message_from_bytes(item[1], policy=policy.default)
        return None
