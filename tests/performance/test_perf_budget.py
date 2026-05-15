"""
Standalone performance budget tests. Per-page budgets configured via env:

  PERF_TTFB_MS   default 1500
  PERF_DCL_MS    default 3000
  PERF_LOAD_MS   default 5000

Metrics also land in reports/report.json under each test's `perf` field.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page

from flows.performance import PerfBudget, collect
from pages.login_page import LoginPage


@pytest.mark.perf
def test_login_page_perf(anon_page: Page, record) -> None:
    LoginPage(anon_page).open()
    metrics = collect(anon_page)
    record["perf"] = metrics.as_dict()
    PerfBudget.from_env().assert_within(metrics)


@pytest.mark.perf
@pytest.mark.mocked
def test_dashboard_perf(mocked_page: Page, record) -> None:
    mocked_page.goto("/nova/dashboards/main", wait_until="load")
    metrics = collect(mocked_page)
    record["perf"] = metrics.as_dict()
    PerfBudget.from_env().assert_within(metrics)
