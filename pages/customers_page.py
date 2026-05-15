from __future__ import annotations

import re

from playwright.sync_api import expect

from pages.base_page import BasePage


class CustomersPage(BasePage):
    """Nova resource page for customers."""

    url_path = "/nova/resources/customers"
    URL_RE = re.compile(r"/nova/resources/customers")

    def expect_loaded(self) -> None:
        expect(self.page).to_have_url(self.URL_RE)
        candidates = self.page.locator(
            '[dusk*="resource-index"], [role="table"], main, #nova'
        )
        expect(candidates.first).to_be_visible(timeout=10_000)

    def search(self, term: str) -> None:
        self.page.locator('input[type="search"], [dusk*="search"]').first.fill(term)
