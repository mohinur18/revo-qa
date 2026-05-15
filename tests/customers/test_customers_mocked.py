from __future__ import annotations

import pytest
from playwright.sync_api import Page

from flows.auth import assert_not_on_login
from flows.performance import PerfBudget, collect
from pages.customers_page import CustomersPage


@pytest.mark.customers
@pytest.mark.mocked
@pytest.mark.smoke
def test_customers_list_loads(mocked_page: Page, record) -> None:
    CustomersPage(mocked_page).open()
    assert_not_on_login(mocked_page)
    CustomersPage(mocked_page).expect_loaded()
    mocked_page.screenshot(path="reports/screenshots/customers.png", full_page=True)
    record["screenshots"] = ["reports/screenshots/customers.png"]


@pytest.mark.customers
@pytest.mark.mocked
@pytest.mark.perf
def test_customers_within_perf_budget(mocked_page: Page, record) -> None:
    CustomersPage(mocked_page).open()
    metrics = collect(mocked_page)
    record["perf"] = metrics.as_dict()
    PerfBudget.from_env().assert_within(metrics)
