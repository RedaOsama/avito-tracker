"""Extract ads from Avito saved-search notification emails.

The exact email layout isn't documented, so parsing is deliberately tolerant:
an "ad" is any link (direct or wrapped in a click-tracking redirect) that points
to an Avito ad page, i.e. a URL whose path ends in ``_<numeric id>``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from urllib.parse import unquote

from bs4 import BeautifulSoup

# .../moskva/telefony/iphone_15_128gb_4123456789  ->  path, id
_AD_PATH_RE = re.compile(r"avito\.ru(/(?:[\w\-]+/)*[\w\-]*_(\d{6,}))(?![\w\-])", re.I)
# Digest form: https://www.avito.ru/autosearch/get/<token>?itemId=8523578755&fromEmail=...
_ITEM_LINK_RE = re.compile(
    r"(https?://(?:www\.|m\.)?avito\.ru/[^\s\"'<>?#]*\?(?:[^\s\"'<>#]*&)?itemId=(\d{6,})[^\s\"'<>#]*)", re.I)
# Avito photo URLs carry their size, e.g. https://86.img.avito.st/80x60/59173166588.jpg
_IMG_SIZE_RE = re.compile(r"(img\.avito\.st/)\d+x\d+/", re.I)
# Short form: avito.ru/4123456789
_AD_SHORT_RE = re.compile(r"avito\.ru/(\d{6,})(?![\w\-])", re.I)
_PRICE_RE = re.compile(r"(\d{1,3}(?:[    ]\d{3})+|\d+)\s*(?:₽|руб)", re.I)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Ad:
    id: str
    url: str
    title: str | None = None
    price: str | None = None
    image: str | None = None
    price_value: int | None = None  # rubles, for price rules
    text: str = ""                  # everything shown for the ad in the email, for keyword rules

    @property
    def search_text(self) -> str:
        return f"{self.title or ''} {self.text}"


def _item_id_link(candidate: str) -> tuple[str, str] | None:
    """Avito's saved-search digests link ads as avito.ru/autosearch/get/<token>?itemId=<id>.

    If the link is wrapped in another avito.ru redirect, use the innermost one.
    """
    starts = [m.start() for m in re.finditer(r"https?://", candidate)]
    for start in reversed(starts):
        m = _ITEM_LINK_RE.match(candidate, start)
        if m:
            return m.group(2), m.group(1)
    return None


def find_ad_in_url(href: str) -> tuple[str, str] | None:
    """Return (ad_id, url_to_open) if ``href`` points to an Avito ad, else None.

    Handles links wrapped in redirect/tracking URLs by URL-decoding repeatedly.
    """
    candidate = href.strip()
    for _ in range(4):
        found = _item_id_link(candidate)
        if found:
            return found
        m = _AD_PATH_RE.search(candidate)
        if m:
            return m.group(2), "https://www.avito.ru" + m.group(1)
        m = _AD_SHORT_RE.search(candidate)
        if m:
            return m.group(1), "https://www.avito.ru/" + m.group(1)
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return None


def email_subject(msg: Message) -> str:
    raw = msg.get("Subject", "") or ""
    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


def _body(msg: Message, subtype: str) -> str | None:
    for part in msg.walk():
        if part.get_content_type() != f"text/{subtype}":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")
    return None


def _clean(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _price_value(text: str) -> int | None:
    m = _PRICE_RE.search(text)
    return int(re.sub(r"\D", "", m.group(1))) if m else None


def _format_price(value: int | None) -> str | None:
    return None if value is None else f"{value:,}".replace(",", " ") + " ₽"


def parse_ads(msg: Message) -> list[Ad]:
    """All distinct ads linked from an email, in the order they appear."""
    html = _body(msg, "html")
    if html:
        return _parse_html(html)
    text = _body(msg, "plain") or ""
    ads: dict[str, Ad] = {}
    for token in text.split():
        found = find_ad_in_url(token)
        if found and found[0] not in ads:
            ads[found[0]] = Ad(id=found[0], url=found[1])
    return list(ads.values())


def _parse_html(html: str) -> list[Ad]:
    soup = BeautifulSoup(html, "lxml")
    anchor_ad: dict[int, str] = {}  # id(anchor tag) -> ad id
    order: list[str] = []
    urls: dict[str, str] = {}
    anchors_by_ad: dict[str, list] = {}

    for a in soup.find_all("a", href=True):
        found = find_ad_in_url(a["href"])
        if not found:
            continue
        ad_id, url = found
        anchor_ad[id(a)] = ad_id
        if ad_id not in urls:
            order.append(ad_id)
            urls[ad_id] = url
        anchors_by_ad.setdefault(ad_id, []).append(a)

    def ads_inside(el) -> set[str]:
        return {anchor_ad[id(a)] for a in el.find_all("a", href=True) if id(a) in anchor_ad}

    ads = []
    for ad_id in order:
        # Grow from the first link up to the largest element that contains only this ad:
        # that element is the ad's "card" (title, price, photo).
        card = anchors_by_ad[ad_id][0]
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]"):
            if ads_inside(card.parent) != {ad_id}:
                break
            card = card.parent

        card_text = _clean(card.get_text(" "))
        price_value = _price_value(card_text)

        texts = [_clean(a.get_text(" ")) for a in anchors_by_ad[ad_id]]
        texts += [_clean(img.get("alt")) for img in card.find_all("img")]
        texts = [_clean(_PRICE_RE.sub("", t)) for t in texts]
        texts = [t for t in texts if len(t) >= 3]
        title = max(texts, key=len)[:200] if texts else None

        image = None
        for img in card.find_all("img", src=True):
            src = img["src"]
            if not src.startswith("http"):
                continue
            if img.get("width") in ("0", "1") or img.get("height") in ("0", "1"):
                continue  # tracking pixel
            image = _IMG_SIZE_RE.sub(r"\g<1>640x480/", src)  # emails carry tiny thumbnails
            break

        ads.append(Ad(id=ad_id, url=urls[ad_id], title=title, price=_format_price(price_value),
                      image=image, price_value=price_value, text=card_text[:1000]))
    return ads
