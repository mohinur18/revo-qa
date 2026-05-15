from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from pages.base_page import BasePage


class LoginPage(BasePage):
    """Laravel Nova login at /nova/login. UI is in Russian."""

    url_path = "/nova/login"
    URL_RE = re.compile(r"/nova/login")

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        # Precise selectors confirmed via DOM probe — ids are stable across Nova versions
        self.email_input = page.locator("input#username")
        self.password_input = page.locator("input#password")
        self.submit_button = page.locator('button[type="submit"]')
        # Nova renders validation errors inline near each field
        self.error_text = page.locator('[role="alert"], .text-red-500, .help-text-error')

    def login(self, email: str, password: str) -> None:
        self.open()
        self.email_input.fill(email)
        self.password_input.fill(password)
        self.submit_button.click()

    def expect_still_on_login(self) -> None:
        expect(self.page).to_have_url(self.URL_RE)
