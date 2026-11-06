"""Persistent tracker state, stored as JSON (committed back to the repo on GitHub Actions)."""
from __future__ import annotations

import copy
import json
import os

DEFAULT_STATE = {
    "tg_offset": 0,
    "paused": False,
    "imap": {"uidvalidity": None, "last_uid": None},
    "seen_ads": [],
    "rules": [],          # keyword rules, see filters.py
    "next_rule_id": 1,
    "stats": {"forwarded": 0, "last_forward_at": None},
    "last_error": None,
}


def load_state(path: str) -> dict:
    state = copy.deepcopy(DEFAULT_STATE)
    if not os.path.exists(path):
        return state
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key].update(value)
        else:
            state[key] = value
    return state


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
