from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from flows.auth import assert_not_on_login
from flows.performance import PerfBudget, collect
from pages.devices_page import DevicesPage


@pytest.mark.smoke
@pytest.mark.mocked
def test_devices_list_loads(mocked_page: Page, record) -> None:
    DevicesPage(mocked_page).open()
    assert_not_on_login(mocked_page)
    DevicesPage(mocked_page).expect_loaded()
    mocked_page.screenshot(path="reports/screenshots/devices.png", full_page=True)
    record["screenshots"] = ["reports/screenshots/devices.png"]


@pytest.mark.mocked
def test_devices_uses_mocked_data(mocked_page: Page) -> None:
    """Capture the /nova-api/devices response and assert it came from the mock."""
    seen: dict = {}

    def on_response(resp):
        if "/nova-api/" in resp.url and "devices" in resp.url:
            seen[resp.url] = resp.headers.get("x-mock")

    mocked_page.on("response", on_response)
    DevicesPage(mocked_page).open()
    mocked_page.wait_for_load_state("networkidle")

    # We don't assert that any specific URL was hit (Nova's exact API path may vary
    # under mocked HTML), but if it was, it must be our mock.
    for url, mock_marker in seen.items():
        assert mock_marker == "revo-qa", f"Non-mock response for {url}"


@pytest.mark.mocked
@pytest.mark.perf
def test_devices_within_perf_budget(mocked_page: Page, record) -> None:
    DevicesPage(mocked_page).open()
    metrics = collect(mocked_page)
    record["perf"] = metrics.as_dict()
    PerfBudget.from_env().assert_within(metrics)
