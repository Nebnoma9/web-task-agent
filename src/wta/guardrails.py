"""Guardrails enforced by the harness, never by the model.

* Domain allow-list: ``goto`` targets and *every* network request the page makes are checked
  (the browser context routes all requests through :meth:`Guardrails.request_allowed`).
* Step budget: the agent loop stops at ``max_steps``.
* Payment fields: filling a payment-like field is refused unless the task explicitly opts in to
  test data, and even then only well-known public test card numbers are accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = ("localhost", "127.0.0.1")

# Publicly documented test card numbers (never valid for real charges).
TEST_CARD_NUMBERS = frozenset(
    {
        "4242424242424242",
        "4000056655665556",
        "5555555555554444",
        "2223003122003222",
        "378282246310005",
    }
)

_PAYMENT_HINT = re.compile(
    r"(card|cc[-_ ]?(num|number|exp|csc|cvc)|cvv|cvc|csc|security code|expiry|expiration|iban|"
    r"bic|swift|routing|account number|sort code)",
    re.IGNORECASE,
)


class GuardrailViolation(Exception):
    """Raised (and recorded in the trace) when an action is refused."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"[{rule}] {message}")
        self.rule = rule
        self.message = message


@dataclass(slots=True)
class FieldInfo:
    """What we know about a form field before filling it (read from the live DOM)."""

    tag: str = ""
    input_type: str = ""
    name: str = ""
    id: str = ""
    autocomplete: str = ""
    label: str = ""

    def is_payment(self) -> bool:
        if self.autocomplete.lower().startswith("cc-"):
            return True
        haystack = " ".join([self.name, self.id, self.label, self.autocomplete])
        return bool(_PAYMENT_HINT.search(haystack))

    def is_card_number(self) -> bool:
        if self.autocomplete.lower() == "cc-number":
            return True
        hay = f"{self.name} {self.id} {self.label}".lower()
        return "card" in hay and ("number" in hay or "num" in hay)

    def is_password(self) -> bool:
        return self.input_type.lower() == "password"


def host_allowed(host: str, allowed: tuple[str, ...] | list[str]) -> bool:
    host = host.lower().rstrip(".")
    for d in allowed:
        d = d.lower().lstrip(".")
        if host == d or host.endswith("." + d):
            return True
    return False


@dataclass(slots=True)
class Guardrails:
    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS
    max_steps: int = 15
    allow_test_payment_data: bool = False
    blocked_requests: list[str] = field(default_factory=list)

    def resolve_url(self, url: str, base_url: str) -> str:
        """Resolve a possibly-relative URL and enforce scheme + domain allow-list."""
        absolute = urljoin(base_url, url)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            raise GuardrailViolation("scheme", f"scheme {parsed.scheme!r} is not allowed")
        if not parsed.hostname or not host_allowed(parsed.hostname, self.allowed_domains):
            raise GuardrailViolation(
                "domain",
                f"{parsed.hostname!r} is not in the allow-list {list(self.allowed_domains)}",
            )
        return absolute

    def request_allowed(self, url: str) -> bool:
        """Used by the browser route handler for every request (scripts, XHR, images...)."""
        parsed = urlparse(url)
        if parsed.scheme in ("data", "blob", "about"):
            return True
        ok = bool(parsed.hostname) and host_allowed(parsed.hostname or "", self.allowed_domains)
        if not ok:
            self.blocked_requests.append(url)
        return ok

    def check_step(self, step: int) -> None:
        if step > self.max_steps:
            raise GuardrailViolation("max_steps", f"step budget of {self.max_steps} exhausted")

    def check_fill(self, info: FieldInfo, value: str) -> None:
        """Refuse payment-like input unless the task allows test data, and then only test cards."""
        if not info.is_payment():
            return
        if not self.allow_test_payment_data:
            raise GuardrailViolation(
                "payment",
                "refusing to fill a payment field: this task does not allow test payment data",
            )
        if info.is_card_number():
            digits = re.sub(r"[\s-]", "", value)
            if digits not in TEST_CARD_NUMBERS:
                raise GuardrailViolation(
                    "payment", "only well-known public test card numbers may be entered"
                )
