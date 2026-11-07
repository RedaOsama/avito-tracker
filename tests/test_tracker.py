"""Tests use a hand-made email shaped like an Avito saved-search alert.

Real Avito emails may differ; use /test in Telegram to check the parser on yours.
"""
from email.message import EmailMessage

import pytest

from tracker import filters, main
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


# Same structure as Avito's real "Новые объявления" digest (tokens shortened).
DIGEST_HTML = """
<html><body>
<a href="https://www.avito.ru/mailStat/click?redirect=https%3A%2F%2Fwww.avito.ru&token=abc"><img src="https://www.avito.st/logo.png"></a>
<table><tr><td>
  <a href=" https://www.avito.ru/autosearch/get/TOKENA">boadicea torero → Личные вещи</a>
  <table><tr><td><img src="https://10.img.avito.st/80x60/111.jpg"></td>
    <td><table><tr><td><a href=" https://www.avito.ru/autosearch/get/TOKENA?itemId=8503704966&fromEmail=autosearch.digest">Пробники, Отливанты</a></td></tr>
    <tr><td>1 550 ₽</td></tr></table></td></tr></table>
  <table><tr><td><img src="https://20.img.avito.st/80x60/222.jpg"></td>
    <td><table><tr><td><a href=" https://www.avito.ru/autosearch/get/TOKENA?itemId=8400534958&fromEmail=autosearch.digest">Freccia 1921 Profumum Roma</a></td></tr>
    <tr><td>4 000 ₽</td></tr><tr><td>Вчера 18:00 м. Арбатская</td></tr></table></td></tr></table>
  <a href=" https://www.avito.ru/autosearch/get/TOKENA">Ещё новые объявления</a>
</td></tr>
<tr><td>
  <a href=" https://www.avito.ru/autosearch/get/TOKENB">xerjoff naxos → Красота и здоровье</a>
  <table><tr><td><a href=""><img src="https://86.img.avito.st/80x60/333.jpg"></a></td>
    <td><table><tr><td><a href=" https://www.avito.ru/autosearch/get/TOKENB?itemId=8523578755&fromEmail=autosearch.digest">Xerjoff Naxos</a></td></tr>
    <tr><td>12 000 ₽</td></tr></table></td></tr></table>
  <a href=" https://www.avito.ru/autosearch/get/TOKENB">Ещё новые объявления</a>
</td></tr></table>
<a href="https://www.avito.ru/mailStat/click?redirect=https%3A%2F%2Fwww.avito.ru%2Fprofile%2Fsettings&token=x">Отписаться от рассылки</a>
</body></html>
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


def test_parses_real_avito_digest_layout():
    ads = parse_ads(make_email(DIGEST_HTML))
    assert [(a.id, a.title, a.price) for a in ads] == [
        ("8503704966", "Пробники, Отливанты", "1 550 ₽"),
        ("8400534958", "Freccia 1921 Profumum Roma", "4 000 ₽"),
        ("8523578755", "Xerjoff Naxos", "12 000 ₽"),
    ]
    naxos = ads[2]
    assert naxos.url == ("https://www.avito.ru/autosearch/get/TOKENB"
                         "?itemId=8523578755&fromEmail=autosearch.digest")
    assert naxos.image == "https://86.img.avito.st/640x480/333.jpg"  # bigger than the thumbnail


def test_message_shows_ad_link():
    ad = parse_ads(make_email(DIGEST_HTML))[2]
    text = main.format_ad(ad)
    assert "avito.ru/8523578755" in text
    assert 'href="https://www.avito.ru/autosearch/get/TOKENB?itemId=8523578755' in text


def test_redirect_wrapped_item_link():
    href = ("https://www.avito.ru/mailStat/click?redirect=https%3A%2F%2Fwww.avito.ru%2Fautosearch%2Fget%2FT"
            "%3FitemId%3D8523578755&token=x")
    ad_id, url = find_ad_in_url(href)
    assert ad_id == "8523578755"
    assert url.startswith("https://www.avito.ru/autosearch/get/T?itemId=8523578755")


def test_account_emails_are_not_announced():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    msg = make_email(html="<p>Подтвердите почту</p>")
    msg.replace_header("Subject", "📫 Смена почты для профиля")
    main.forward_email(tg, cfg, state, msg)
    assert tg.sent == []


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
    assert "1 more matching ads" in tg.sent[-1][1]
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
    assert main.read_commands(FakeTelegram(updates), make_config(), state) == [("/status", "")]
    assert state["tg_offset"] == 13


# ---------- keyword rules ----------

def test_parse_rule():
    rule = filters.parse_rule("iPhone 15 Pro -чехол -Разбит <90000 >10k")
    assert rule == {"words": ["iphone", "15", "pro"], "exclude": ["чехол", "разбит"],
                    "min_price": 10000, "max_price": 90000}


@pytest.mark.parametrize("text", ["", "-чехол", "<5000", "iphone >9000 <100"])
def test_parse_rule_rejects_bad_rules(text):
    with pytest.raises(ValueError):
        filters.parse_rule(text)


@pytest.mark.parametrize("rule, text, price, expected", [
    ("iphone 15", "Apple iPhone 15 128GB", 65000, True),
    ("iphone 15", "Apple iPhone 14", 50000, False),
    ("айфон", "Продаю айфона 13", None, True),           # part of a word matches
    ("iphone -чехол", "Чехол для iPhone 15", 500, False),
    ("iphone <70000", "iPhone 15", 89990, False),
    ("iphone <70000", "iPhone 15", None, True),          # unknown price isn't filtered out
    ("iphone >10000", "iPhone 15", 5000, False),
    ("елка", "Ёлка искусственная", 1000, True),          # ё == е
])
def test_rule_matching(rule, text, price, expected):
    assert filters.matches(filters.parse_rule(rule), text, price) is expected


def run_commands(state, *texts):
    tg = FakeTelegram()
    for text in texts:
        cmd, _, args = text.partition(" ")
        main.handle_command(tg, state, cmd, args.strip())
    return tg


def test_add_list_remove_commands():
    state = fresh_state()
    tg = run_commands(state, "/add iphone 15 <70000", "/add macbook air", "/list")
    assert [r["id"] for r in state["rules"]] == [1, 2]
    assert "Rule #1 added" in tg.sent[0][1]
    assert "#2: macbook air" in tg.sent[2][1]

    run_commands(state, "/remove 1")
    assert [r["id"] for r in state["rules"]] == [2]
    run_commands(state, "/add ps5")
    assert [r["id"] for r in state["rules"]] == [2, 3]  # ids are never reused
    run_commands(state, "/remove all")
    assert state["rules"] == []


def test_add_without_words_is_rejected():
    state = fresh_state()
    tg = run_commands(state, "/add -чехол")
    assert state["rules"] == [] and "❌" in tg.sent[0][1]


def test_only_matching_ads_are_sent():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    run_commands(state, "/add iphone pro")
    assert main.forward_email(tg, cfg, state, make_email()) == 1
    assert "iPhone 15 Pro 256GB" in tg.sent[0][1]
    assert "Rule #1" in tg.sent[0][1]
    # The non-matching ad is remembered too, so adding a rule later doesn't resend old ads.
    assert set(state["seen_ads"]) == {"4123456789", "4987654321"}


def test_price_rule_uses_ad_price():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    run_commands(state, "/add iphone <70000")
    assert main.forward_email(tg, cfg, state, make_email()) == 1
    assert "65 000 ₽" in tg.sent[0][1]


def test_test_mode_explains_non_matches():
    tg, cfg, state = FakeTelegram(), make_config(), fresh_state()
    run_commands(state, "/add iphone pro")
    main.forward_email(tg, cfg, state, make_email(), test=True)
    texts = [t for _, t in tg.sent]
    assert any("doesn't match" in t for t in texts)
    assert state["seen_ads"] == []


def test_state_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    state = fresh_state()
    state["seen_ads"].append("1")
    save_state(path, state)
    assert load_state(path)["seen_ads"] == ["1"]
    assert DEFAULT_STATE["seen_ads"] == []  # default not mutated
