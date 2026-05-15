from __future__ import annotations

import os
import re

import pytest
from playwright.sync_api import Page, expect

from pages.dashboard_page import DashboardPage
from pages.login_page import LoginPage

LOGIN_URL_RE = re.compile(r"/nova/login")


@pytest.mark.auth
@pytest.mark.smoke
@pytest.mark.xfail(
    reason="No real admin credentials available; placeholder creds expected to fail. "
    "Will auto-pass once REVO_ADMIN_LOGIN points at a real account.",
    strict=False,
)
def test_valid_admin_login(anon_page: Page) -> None:
    LoginPage(anon_page).login(
        os.environ["REVO_ADMIN_LOGIN"],
        os.environ["REVO_ADMIN_PASSWORD"],
    )
    DashboardPage(anon_page).expect_loaded()


@pytest.mark.auth
@pytest.mark.parametrize(
    "login,password",
    [
        ("not_a_user@example.com", "wrong-pass"),
        ("admin@example.com", ""),
        ("", "irrelevant"),
    ],
    ids=["wrong-creds", "missing-password", "missing-login"],
)
def test_invalid_login_rejected(anon_page: Page, login: str, password: str) -> None:
    page = LoginPage(anon_page)
    page.login(login, password)
    page.expect_still_on_login()


@pytest.mark.auth
@pytest.mark.xfail(reason="Requires real admin session via admin_page fixture", strict=False)
def test_logout_clears_session(admin_page: Page) -> None:
    DashboardPage(admin_page).open()
    DashboardPage(admin_page).logout()
    expect(admin_page).to_have_url(LOGIN_URL_RE)

    admin_page.goto("/nova")
    expect(admin_page).to_have_url(LOGIN_URL_RE)


@pytest.mark.auth
@pytest.mark.smoke
@pytest.mark.xfail(reason="Requires real admin session via admin_page fixture", strict=False)
def test_session_persists_across_reload(admin_page: Page) -> None:
    DashboardPage(admin_page).open()
    admin_page.reload()
    DashboardPage(admin_page).expect_loaded()


@pytest.mark.auth
@pytest.mark.rbac
@pytest.mark.skip(reason="Enable once non-admin role creds are wired into .env")
def test_operator_cannot_access_admin_routes(browser, browser_context_args) -> None:
    """Operator role should be 403/redirected on admin-only routes."""
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    LoginPage(page).login(
        os.environ["REVO_OPERATOR_LOGIN"],
        os.environ["REVO_OPERATOR_PASSWORD"],
    )
    page.goto("/nova/resources/users")
    expect(page).not_to_have_url(re.compile(r"/nova/resources/users"))
    ctx.close()
