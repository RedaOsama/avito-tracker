"""Tests use a hand-made email shaped like an Avito saved-search alert.

Real Avito emails may differ; use /test in Telegram to check the parser on yours.
"""
from email.message import EmailMessage

import pytest

from tracker import main
from tracker.avito_email import find_ad_in_url, parse_ads
from tracker.config import Config
from tracker.state import DEFAULT_STATE, load_state, save_state

SAMPLE_HTML = """
<html><body><table>
<tr><td><h1>Новые объявления по вашему поиску «iPhone 15»</h1></td></tr>
<tr><td>
  <a href="https://www.avito.ru/moskva/telefony/iphone_15_128gb_4123456789?utm_source=saved_search">
    <img src="https://00.img.avito.st/image/1/abc.jpg" alt="iPhone 15 128GB"></a>
  <a href="https://www.avito.ru/moskva/telefony/iphone_15_128gb_4123456789?utm_source=saved_search">iPhone 15 128GB</a>
  <p>65 000 ₽</p><p>Москва, м. Тверская</p>
</td></tr>
<tr><td>
  <a href="https://click.avito.ru/redirect?url=https%3A%2F%2Fwww.avito.ru%2Fmoskva%2Ftelefony%2Fiphone_15_pro_256gb_4987654321%3Futm_campaign%3Dx">iPhone 15 Pro 256GB</a>
  <span>89&nbsp;990&nbsp;₽</span>
</td></tr>
<tr><td>
  <a href="https://www.avito.ru/profile/saved-searches">Настроить уведомления</a>
  <a href="https://www.avito.ru/unsubscribe?token=abc">Отписаться</a>
  <img src="https://track.avito.ru/open.gif" width="1" height="1">
</td></tr>
</table></body></html>
"""


def make_email(html=SAMPLE_HTML, text="Новые объявления"):
    msg = EmailMessage()
    msg["From"] = "Авито <noreply@avito.ru>"
    msg["Subject"] = "Новые объявления: iPhone 15"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def make_config(**kw):
    base = dict(telegram_token="t", telegram_chat_id="42", email_address="me@gmail.com",
                email_password="p", max_ads_per_email=10)
    base.update(kw)
    return Config(**base)


class FakeTelegram:
    def __init__(self, updates=()):
        self.sent = []
        self.updates = list(updates)

    def send_message(self, text):
        self.sent.append(("msg", text))

    def send_photo(self, photo, caption):
        self.sent.append(("photo", caption))

    def get_updates(self, offset):
        return [u for u in self.updates if u["update_id"] >= offset]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(main, "SEND_PAUSE", 0)


def fresh_state():
    return load_state("does-not-exist.json")


def test_parses_ads_from_html_email():
    ads = parse_ads(make_email())
    assert [a.id for a in ads] == ["4123456789", "4987654321"]

    first, second = ads
    assert first.url == "https://www.avito.ru/moskva/telefony/iphone_15_128gb_4123456789"
    assert first.title == "iPhone 15 128GB"
    assert first.price == "65 000 ₽"
    assert first.image == "https://00.img.avito.st/image/1/abc.jpg"

    assert second.url == "https://www.avito.ru/moskva/telefony/iphone_15_pro_256gb_4987654321"
    assert second.title == "iPhone 15 Pro 256GB"
    assert second.price == "89 990 ₽"
    assert second.image is None


@pytest.mark.parametrize("href", [
    "https://www.avito.ru/profile/saved-searches",
    "https://www.avito.ru/unsubscribe?token=abc",
    "https://www.avito.ru/moskva/telefony",
    "https://example.com/item_12345678",
])
def test_non_ad_links_are_ignored(href):
    assert find_ad_in_url(href) is None


def test_double_encoded_redirect():
    href = "https://t.example.com/c?u=https%253A%252F%252Fwww.avito.ru%252Fspb%252Fkvartiry%252F2-k_kvartira_3111222333"
    assert find_ad_in_url(href) == ("3111222333", "https://www.avito.ru/spb/kvartiry/2-k_kvartira_3111222333")


def test_plain_text_email():
    msg = make_email(html=None, text="Новое объявление: https://www.avito.ru/moskva/telefony/iphone_4111111111 смотрите")
    ads = parse_ads(msg)
    assert [(a.id, a.title) for a in ads] == [("4111111111", None)]


def test_forward_skips_already_seen_ads():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    assert main.forward_email(tg, cfg, state, make_email()) == 2
    assert main.forward_email(tg, cfg, state, make_email()) == 0
    assert state["stats"]["forwarded"] == 2
    assert [kind for kind, _ in tg.sent] == ["photo", "msg"]
    assert "65 000 ₽" in tg.sent[0][1]


def test_test_mode_resends_without_marking_seen():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    assert main.forward_email(tg, cfg, state, make_email(), test=True) == 2
    assert state["seen_ads"] == []
    assert tg.sent[0][1].startswith("🧪 Test:")


def test_cap_per_email_summarises_rest():
    tg, cfg, state = FakeTelegram(), make_config(max_ads_per_email=1), fresh_state()
    assert main.forward_email(tg, cfg, state, make_email()) == 1
    assert "1 more new ads" in tg.sent[-1][1]
    assert set(state["seen_ads"]) == {"4123456789", "4987654321"}


def test_email_without_ads_is_announced():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    main.forward_email(tg, cfg, state, make_email(html="<p>Скидки недели</p>"))
    assert "no ads detected" in tg.sent[0][1]


def test_commands_only_from_owner():
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "/pause"}},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/Status@MyBot"}},
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "hello"}},
    ]
    state = fresh_state()
    assert main.read_commands(FakeTelegram(updates), make_config(), state) == ["/status"]
    assert state["tg_offset"] == 13


def test_state_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    state = fresh_state()
    state["seen_ads"].append("1")
    save_state(path, state)
    assert load_state(path)["seen_ads"] == ["1"]
    assert DEFAULT_STATE["seen_ads"] == []  # default not mutated
