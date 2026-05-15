"""One-off: dump the login form structure so we can write precise selectors."""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://revo.avtomato.uz/nova/login", wait_until="networkidle")

        print(f"\n=== URL: {page.url} ===\n")
        print("=== TITLE ===")
        print(page.title())

        print("\n=== INPUTS ===")
        for inp in page.locator("input").all():
            print(
                {
                    "name": inp.get_attribute("name"),
                    "type": inp.get_attribute("type"),
                    "id": inp.get_attribute("id"),
                    "placeholder": inp.get_attribute("placeholder"),
                    "aria-label": inp.get_attribute("aria-label"),
                }
            )

        print("\n=== LABELS ===")
        for lbl in page.locator("label").all():
            print({"for": lbl.get_attribute("for"), "text": (lbl.text_content() or "").strip()})

        print("\n=== BUTTONS ===")
        for btn in page.locator("button").all():
            print(
                {
                    "type": btn.get_attribute("type"),
                    "text": (btn.text_content() or "").strip(),
                    "aria-label": btn.get_attribute("aria-label"),
                }
            )

        print("\n=== FORM ACTION/METHOD ===")
        for form in page.locator("form").all():
            print({"action": form.get_attribute("action"), "method": form.get_attribute("method")})

        browser.close()


if __name__ == "__main__":
    main()
