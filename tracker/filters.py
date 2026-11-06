"""Keyword rules: which ads are worth a notification.

A rule is written like a search:  iphone 15 pro -чехол <90000
  * plain words  – all must appear (case-insensitive, part of a word is enough:
                   "айфон" also matches "айфона")
  * -word        – the ad must NOT contain it
  * <N / >N      – price at most / at least N rubles
An ad is forwarded if it matches ANY rule. With no rules, every ad is forwarded.
"""
from __future__ import annotations

import html
import re

MAX_RULES = 50


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def parse_rule(text: str) -> dict:
    """Turn "iphone 15 -чехол <90000" into a rule dict. Raises ValueError if empty/invalid."""
    words, exclude = [], []
    min_price = max_price = None
    for token in normalize(text).split():
        number = re.fullmatch(r"([<>])=?(\d[\d_.,]*)(k|к)?", token)
        if number:
            value = int(re.sub(r"\D", "", number.group(2))) * (1000 if number.group(3) else 1)
            if number.group(1) == "<":
                max_price = value
            else:
                min_price = value
        elif token.startswith("-") and len(token) > 1:
            exclude.append(token[1:])
        elif token != "-":
            words.append(token)
    if not words:
        raise ValueError("A rule needs at least one word to look for.")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("Minimum price is higher than maximum price.")
    return {"words": words, "exclude": exclude, "min_price": min_price, "max_price": max_price}


def matches(rule: dict, text: str, price: int | None) -> bool:
    haystack = normalize(text)
    if not all(w in haystack for w in rule["words"]):
        return False
    if any(w in haystack for w in rule["exclude"]):
        return False
    if price is not None:
        if rule.get("max_price") is not None and price > rule["max_price"]:
            return False
        if rule.get("min_price") is not None and price < rule["min_price"]:
            return False
    return True


def first_match(rules: list[dict], text: str, price: int | None) -> dict | None:
    return next((r for r in rules if matches(r, text, price)), None)


def describe(rule: dict) -> str:
    """Human-readable, HTML-escaped description of a rule."""
    parts = [html.escape(" ".join(rule["words"]))]
    if rule["exclude"]:
        parts.append("without " + html.escape(", ".join(rule["exclude"])))
    if rule.get("min_price") is not None:
        parts.append(f"from {rule['min_price']:,} ₽".replace(",", " "))
    if rule.get("max_price") is not None:
        parts.append(f"up to {rule['max_price']:,} ₽".replace(",", " "))
    return " · ".join(parts)
