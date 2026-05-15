"""
Mocked-authentication flow. No real credentials, no live login round-trip.

Usage:
    @pytest.fixture
    def mocked_page(...):  # see conftest.py
        ...

    def test_dashboard(mocked_page):
        login_as_mock_admin(mocked_page, base_url)
        # mocked_page is now at /nova/dashboards/main with stubbed data
"""
from __future__ import annotations

from playwright.sync_api import Page


def goto_dashboard(page: Page, path: str = "/nova/dashboards/main") -> None:
    """Navigate to a protected route. Mocks ensure the page renders without real auth."""
    page.goto(path, wait_until="domcontentloaded")


def assert_not_on_login(page: Page) -> None:
    assert "/nova/login" not in page.url, (
        f"Expected to be past login page, but at {page.url}. "
        "Mocked auth may be misconfigured."
    )
