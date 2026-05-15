from __future__ import annotations

import re

from playwright.sync_api import expect

from pages.base_page import BasePage


class DevicesPage(BasePage):
    """Nova resource page for devices. Under mocks, lists data from mocks/data/devices.json."""

    url_path = "/nova/resources/devices"
    URL_RE = re.compile(r"/nova/resources/devices")

    def expect_loaded(self) -> None:
        # Defensive: under mocks the SPA may render slowly. We assert the URL and
        # that *something* navigation-shaped exists on the page.
        expect(self.page).to_have_url(self.URL_RE)
        # Common Nova attributes — at least one should exist on a rendered resource list
        candidates = self.page.locator(
            '[dusk*="resource-index"], [role="table"], main, #nova'
        )
        expect(candidates.first).to_be_visible(timeout=10_000)

    def row_for_serial(self, serial: str):
        return self.page.get_by_text(serial, exact=False)
