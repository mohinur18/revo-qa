"""
HTTP security header & cookie audit — pure network probes, no UI session needed.

These checks codify the fintech baseline: HSTS, frame protection, XSS protection,
content-type sniffing protection, referrer policy, and secure cookies.

Each test is independently parametrized so a missing header surfaces clearly in CI,
rather than one failure masking the rest.
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest
from playwright.sync_api import APIRequestContext, Playwright


@pytest.fixture(scope="module")
def api(playwright: Playwright, base_url: str) -> Iterator[APIRequestContext]:
    ctx = playwright.request.new_context(base_url=base_url)
    yield ctx
    ctx.dispose()


@pytest.fixture(scope="module")
def login_response(api: APIRequestContext):
    return api.get("/nova/login")


@pytest.mark.security
def test_login_page_is_200(login_response) -> None:
    assert login_response.status == 200, f"Got {login_response.status} from /nova/login"


@pytest.mark.security
def test_hsts_header_present(login_response) -> None:
    """Strict-Transport-Security must be set on a fintech-adjacent app over HTTPS."""
    hsts = login_response.headers.get("strict-transport-security")
    assert hsts, "Missing Strict-Transport-Security — required for HTTPS-only enforcement"
    # max-age should be at least 6 months (15768000 seconds)
    assert "max-age=" in hsts
    max_age = int(hsts.split("max-age=")[1].split(";")[0].split(",")[0])
    assert max_age >= 15_768_000, f"HSTS max-age={max_age} is too short (<6 months)"


@pytest.mark.security
def test_x_frame_options_or_csp_frame_ancestors(login_response) -> None:
    """One of these must prevent clickjacking."""
    xfo = login_response.headers.get("x-frame-options", "").upper()
    csp = login_response.headers.get("content-security-policy", "").lower()
    has_xfo = xfo in {"DENY", "SAMEORIGIN"}
    has_frame_ancestors = "frame-ancestors" in csp
    assert has_xfo or has_frame_ancestors, (
        "Neither X-Frame-Options nor CSP frame-ancestors is set — clickjacking risk"
    )


@pytest.mark.security
def test_x_content_type_options_nosniff(login_response) -> None:
    assert login_response.headers.get("x-content-type-options", "").lower() == "nosniff", (
        "Missing or incorrect X-Content-Type-Options (expected nosniff)"
    )


@pytest.mark.security
def test_referrer_policy_set(login_response) -> None:
    policy = login_response.headers.get("referrer-policy", "").lower()
    safe = {
        "no-referrer",
        "no-referrer-when-downgrade",
        "same-origin",
        "strict-origin",
        "strict-origin-when-cross-origin",
    }
    assert policy in safe, f"Referrer-Policy should be one of {safe}, got {policy!r}"


@pytest.mark.security
def test_content_security_policy_present(login_response) -> None:
    csp = login_response.headers.get("content-security-policy")
    if not csp:
        pytest.xfail("CSP not configured — recommended hardening for an admin panel")
    assert "default-src" in csp.lower() or "script-src" in csp.lower()


@pytest.mark.security
def test_server_header_does_not_leak_version(login_response) -> None:
    """Server header should not leak detailed version info (defense in depth)."""
    server = login_response.headers.get("server", "")
    leaky_tokens = ["apache/2.", "nginx/1.", "php/", "openresty/"]
    leaked = [t for t in leaky_tokens if t in server.lower()]
    if leaked:
        pytest.xfail(f"Server header leaks version: {server!r} (low severity)")


@pytest.mark.security
def test_x_powered_by_not_set(login_response) -> None:
    assert "x-powered-by" not in {k.lower() for k in login_response.headers.keys()}, (
        "X-Powered-By should be suppressed (defense in depth)"
    )


@pytest.mark.security
def test_session_cookie_flags(api: APIRequestContext, base_url: str) -> None:
    """Session cookies set by /nova/login should be Secure, HttpOnly, and SameSite."""
    resp = api.get("/nova/login")
    # headers_array is a property (list of {name, value}) — entries with same name appear separately
    cookie_headers = [h for h in resp.headers_array if h["name"].lower() == "set-cookie"]
    if not cookie_headers:
        pytest.skip("No Set-Cookie headers on initial GET /nova/login")

    issues: list[str] = []
    for h in cookie_headers:
        raw = h["value"]
        lowered = raw.lower()
        name = raw.split("=", 1)[0]
        # CSRF/XSRF tokens are intentionally readable by JS (HttpOnly would break the SPA),
        # but Secure and SameSite still apply.
        is_csrf_cookie = "xsrf" in name.lower() or "csrf" in name.lower()

        if "secure" not in lowered:
            issues.append(f"{name}: missing Secure flag (cookie over HTTPS must be Secure)")
        if not is_csrf_cookie and "httponly" not in lowered:
            issues.append(f"{name}: missing HttpOnly flag")
        if "samesite=" not in lowered:
            issues.append(f"{name}: missing SameSite attribute")

    assert not issues, "Insecure cookie flags:\n  - " + "\n  - ".join(issues)


@pytest.mark.security
def test_http_redirects_to_https(playwright: Playwright) -> None:
    """Plain-HTTP requests must redirect to HTTPS."""
    base_https = os.getenv("REVO_BASE_URL", "https://revo.avtomato.uz")
    base_http = base_https.replace("https://", "http://")

    ctx = playwright.request.new_context(ignore_https_errors=False)
    try:
        resp = ctx.get(f"{base_http}/nova/login", max_redirects=0)
        assert resp.status in {301, 302, 307, 308}, (
            f"Expected redirect to HTTPS, got status {resp.status}"
        )
        location = resp.headers.get("location", "")
        assert location.startswith("https://"), f"Redirect target not HTTPS: {location!r}"
    finally:
        ctx.dispose()
