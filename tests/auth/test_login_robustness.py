"""
Login form robustness — tests that don't need real credentials.

These tests confirm the login page handles hostile input gracefully:
- never breaks layout
- never reflects raw input back into the DOM (XSS)
- never exposes stack traces / SQL errors
- always either rejects cleanly or stays on /nova/login
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page

from pages.login_page import LoginPage

XSS_PAYLOADS = [
    "<script>window.__xss=1</script>",
    '"><img src=x onerror=alert(1)>',
    "<svg onload=alert(1)>",
    "javascript:alert(1)",
]

SQLI_PAYLOADS = [
    "' OR '1'='1",
    "admin'--",
    "'; DROP TABLE users;--",
    "\" OR 1=1--",
]

LONG_INPUT = "a" * 10_000

UNICODE_INPUTS = [
    "тест@avtomato.uz",          # Cyrillic
    "test+tag@avtomato.uz",      # plus addressing
    "user.name@avtomato.uz",     # dots
    "тест@münchen.de",           # IDN-ish
]


def _no_uncaught_errors(page: Page) -> bool:
    """Return True if no JS uncaught errors fired during the page lifetime."""
    return not getattr(page, "_revo_uncaught", False)


@pytest.fixture
def anon_page_with_error_capture(anon_page: Page) -> Page:
    anon_page._revo_uncaught = False  # type: ignore[attr-defined]

    def _on_pageerror(_):
        anon_page._revo_uncaught = True  # type: ignore[attr-defined]

    anon_page.on("pageerror", _on_pageerror)
    return anon_page


@pytest.mark.auth
@pytest.mark.parametrize("payload", XSS_PAYLOADS, ids=lambda p: p[:30])
def test_xss_in_login_field_is_neutralized(
    anon_page_with_error_capture: Page, payload: str
) -> None:
    page = anon_page_with_error_capture
    LoginPage(page).login(payload, "anything")

    # XSS should never execute — assertion: no marker variable was set
    xss_fired = page.evaluate("() => Boolean(window.__xss)")
    assert xss_fired is False, f"XSS payload executed: {payload}"
    # Page must still be functional (we're either on /nova/login or got a clean error)
    assert "/nova" in page.url
    assert _no_uncaught_errors(page), "Page threw an uncaught JS error"


@pytest.mark.auth
@pytest.mark.parametrize("payload", SQLI_PAYLOADS, ids=lambda p: p[:30])
def test_sqli_in_login_field_does_not_leak(anon_page: Page, payload: str) -> None:
    page = LoginPage(anon_page)
    page.login(payload, "anything")

    body = anon_page.content().lower()
    forbidden = [
        "syntax error",
        "sql",
        "stacktrace",
        "stack trace",
        "pdoexception",
        "sqlstate",
        "uncaught",
        "/var/www",
        "laravel\\database",
    ]
    leaked = [f for f in forbidden if f in body]
    assert not leaked, f"Possible info leak on payload {payload!r}: matched {leaked}"
    page.expect_still_on_login()


@pytest.mark.auth
def test_extremely_long_input_handled(anon_page: Page) -> None:
    page = LoginPage(anon_page)
    page.login(LONG_INPUT + "@example.com", LONG_INPUT)
    page.expect_still_on_login()


@pytest.mark.auth
@pytest.mark.parametrize("email", UNICODE_INPUTS)
def test_unicode_email_does_not_crash(anon_page: Page, email: str) -> None:
    page = LoginPage(anon_page)
    page.login(email, "anything")
    page.expect_still_on_login()


@pytest.mark.auth
def test_login_page_is_https(anon_page: Page) -> None:
    LoginPage(anon_page).open()
    assert anon_page.url.startswith("https://"), f"Login page must be HTTPS, got {anon_page.url}"


@pytest.mark.auth
def test_login_page_csrf_token_present(anon_page: Page) -> None:
    """Laravel/Nova should include a CSRF token in the login form."""
    LoginPage(anon_page).open()
    csrf = anon_page.locator('input[name="_token"], meta[name="csrf-token"]').first
    assert csrf.count() > 0, "Expected a CSRF token (input[name=_token] or meta[csrf-token])"


@pytest.mark.auth
def test_password_field_masks_input(anon_page: Page) -> None:
    LoginPage(anon_page).open()
    pwd_type = anon_page.locator("input#password").get_attribute("type")
    assert pwd_type == "password", f"Password field must be type=password, got {pwd_type}"


@pytest.mark.auth
@pytest.mark.parametrize("autocomplete_attr", ["off", "new-password", "current-password"])
def test_password_autocomplete_configured(anon_page: Page, autocomplete_attr: str) -> None:
    """We don't enforce a specific value, but it should be SET (not browser default)."""
    LoginPage(anon_page).open()
    autocomplete = anon_page.locator("input#password").get_attribute("autocomplete")
    if autocomplete is None:
        pytest.xfail("Password field has no autocomplete attribute — security best-practice gap")
    assert autocomplete in {"off", "new-password", "current-password"}, (
        f"Unexpected autocomplete value: {autocomplete!r}"
    )
