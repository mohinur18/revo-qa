from __future__ import annotations

import re

from playwright.sync_api import Page, expect


class BasePage:
    url_path: str = "/"

    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self) -> None:
        self.page.goto(self.url_path)
        self.page.wait_for_load_state("domcontentloaded")

    def expect_url_contains(self, fragment: str) -> None:
        expect(self.page).to_have_url(re.compile(re.escape(fragment)))

    def expect_url_not_contains(self, fragment: str) -> None:
        expect(self.page).not_to_have_url(re.compile(re.escape(fragment)))

    def screenshot(self, name: str, full_page: bool = True):
        from pathlib import Path
        target = Path(__file__).parent.parent / "reports" / "screenshots" / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(target), full_page=full_page)
        return target

    def toast(self, text_fragment: str):
        return self.page.get_by_role("alert").filter(has_text=text_fragment)
