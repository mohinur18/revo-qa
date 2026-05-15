from __future__ import annotations

import re

from playwright.sync_api import expect

from pages.base_page import BasePage


class DashboardPage(BasePage):
    """Nova dashboard root — anywhere under /nova that isn't /nova/login."""

    url_path = "/nova"
    LOGGED_IN_URL_RE = re.compile(r"/nova(?!/login)")

    def expect_loaded(self) -> None:
        expect(self.page).to_have_url(self.LOGGED_IN_URL_RE)

    def logout(self) -> None:
        # Nova user menu in top-right, then "Выйти" (Logout)
        self.page.locator('[dusk="user-menu-button"]').or_(
            self.page.get_by_role("button").filter(has_text="@")
        ).first.click()
        self.page.get_by_role("menuitem", name="Выйти").or_(
            self.page.get_by_text("Выйти", exact=True)
        ).first.click()
