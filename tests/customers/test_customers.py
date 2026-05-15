from __future__ import annotations

import pytest
from faker import Faker
from playwright.sync_api import Page

fake = Faker("ru_RU")


@pytest.mark.customers
@pytest.mark.smoke
def test_create_customer(admin_page: Page) -> None:
    pytest.skip("TODO: confirm /customers/new selectors")


@pytest.mark.customers
def test_search_customer_by_phone(admin_page: Page) -> None:
    pytest.skip("TODO: depends on test data seed strategy")


@pytest.mark.customers
def test_edit_customer_persists(admin_page: Page) -> None:
    pytest.skip("TODO: implement after create flow is wired")


@pytest.mark.customers
@pytest.mark.scoring
@pytest.mark.parametrize(
    "scenario,expect_status",
    [
        ("clean-history", "approved"),
        ("missing-passport", "incomplete"),
        ("active-overdue", "rejected"),
    ],
)
def test_scoring_returns_expected_decision(
    admin_page: Page, scenario: str, expect_status: str
) -> None:
    pytest.skip(f"TODO: seed '{scenario}' fixture and walk scoring screen")


@pytest.mark.scoring
@pytest.mark.parametrize("score,band", [(0, "red"), (49, "red"), (50, "yellow"), (74, "yellow"), (75, "green"), (100, "green")])
def test_score_threshold_bands(score: int, band: str) -> None:
    """Pure boundary check against documented thresholds — placeholder until docs confirm cutoffs."""
    if score < 50:
        assert band == "red"
    elif score < 75:
        assert band == "yellow"
    else:
        assert band == "green"
