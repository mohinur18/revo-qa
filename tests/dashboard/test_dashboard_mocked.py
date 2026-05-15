"""Dashboard renders under mocked auth + mocked Nova API. No real credentials needed."""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from flows.auth import assert_not_on_login, goto_dashboard
from flows.performance import PerfBudget, collect


@pytest.mark.dashboard
@pytest.mark.smoke
@pytest.mark.mocked
def test_dashboard_loads_with_mocked_session(mocked_page: Page, record) -> None:
    goto_dashboard(mocked_page)
    assert_not_on_login(mocked_page)
    # The Nova SPA shell should render — #nova is the root mount point
    expect(mocked_page.locator("#nova")).to_be_visible()
    expect(mocked_page.locator("main")).to_be_visible()
    record["screenshots"] = ["reports/screenshots/dashboard.png"]
    mocked_page.screenshot(path="reports/screenshots/dashboard.png", full_page=True)


@pytest.mark.dashboard
@pytest.mark.mocked
def test_mocked_api_intercepts_observed(mocked_page: Page) -> None:
    """
    Sanity-check the mock layer: any /nova-api/* response we observe must carry X-Mock.
    If a real backend call leaks through, this test catches it.
    """
    leaked: list[str] = []

    def on_response(resp):
        if "/nova-api/" in resp.url and resp.headers.get("x-mock") != "revo-qa":
            leaked.append(f"{resp.status} {resp.url}")

    mocked_page.on("response", on_response)
    goto_dashboard(mocked_page)
    mocked_page.wait_for_load_state("networkidle")

    assert not leaked, "Real backend responses leaked past the mock layer:\n  - " + "\n  - ".join(
        leaked
    )


@pytest.mark.dashboard
@pytest.mark.mocked
@pytest.mark.perf
def test_dashboard_within_perf_budget(mocked_page: Page, record) -> None:
    goto_dashboard(mocked_page)
    metrics = collect(mocked_page)
    record["perf"] = metrics.as_dict()
    PerfBudget.from_env().assert_within(metrics)
