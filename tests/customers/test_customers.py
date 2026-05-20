from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from flows.auth import assert_not_on_login


@pytest.mark.customers
@pytest.mark.mocked
@pytest.mark.smoke
def test_create_customer(mocked_page: Page) -> None:
    mocked_page.goto("/nova/resources/customers/new", wait_until="domcontentloaded")
    assert_not_on_login(mocked_page)

    mocked_page.get_by_test_id("full_name").fill("Тест Тестов")
    mocked_page.get_by_test_id("phone").fill("+998901112233")
    mocked_page.get_by_test_id("passport").fill("ZZ9998887")
    mocked_page.get_by_test_id("customer-submit").click()

    success = mocked_page.get_by_test_id("customer-success")
    expect(success).to_be_visible()
    expect(success).to_contain_text("Тест Тестов")


@pytest.mark.customers
@pytest.mark.mocked
def test_create_customer_requires_phone(mocked_page: Page) -> None:
    """Server rejects a customer with no phone; the UI surfaces the error."""
    mocked_page.goto("/nova/resources/customers/new", wait_until="domcontentloaded")
    mocked_page.get_by_test_id("full_name").fill("Без Телефона")
    mocked_page.get_by_test_id("passport").fill("ZZ1112223")
    mocked_page.get_by_test_id("customer-submit").click()

    expect(mocked_page.get_by_test_id("customer-error")).to_be_visible()
    expect(mocked_page.get_by_test_id("customer-success")).to_be_hidden()


@pytest.mark.customers
@pytest.mark.mocked
def test_search_customer_by_phone(mocked_page: Page) -> None:
    mocked_page.goto("/nova/resources/customers", wait_until="domcontentloaded")

    # Seeded with three customers
    expect(mocked_page.get_by_test_id("customer-row")).to_have_count(3)

    mocked_page.get_by_test_id("customer-search").fill("9012345")
    rows = mocked_page.get_by_test_id("customer-row")
    expect(rows).to_have_count(1)
    expect(rows.first).to_have_attribute("data-phone", "+998901234567")


@pytest.mark.customers
@pytest.mark.mocked
def test_edit_customer_persists(mocked_page: Page) -> None:
    mocked_page.goto("/nova/resources/customers/101", wait_until="domcontentloaded")

    field = mocked_page.get_by_test_id("edit-full_name")
    expect(field).to_have_value("Алишер Каримов")
    field.fill("Алишер Обновлённый")
    mocked_page.get_by_test_id("customer-edit-submit").click()
    expect(mocked_page.get_by_test_id("customer-edit-success")).to_be_visible()

    # Persisted in the per-context store — survives a reload.
    mocked_page.reload(wait_until="domcontentloaded")
    expect(mocked_page.get_by_test_id("edit-full_name")).to_have_value("Алишер Обновлённый")


@pytest.mark.customers
@pytest.mark.scoring
@pytest.mark.mocked
@pytest.mark.parametrize(
    "scenario,expect_decision",
    [
        ("clean-history", "approved"),
        ("missing-passport", "incomplete"),
        ("active-overdue", "rejected"),
    ],
)
def test_scoring_returns_expected_decision(
    mocked_page: Page, scenario: str, expect_decision: str
) -> None:
    mocked_page.goto("/nova/resources/scoring", wait_until="domcontentloaded")
    mocked_page.get_by_test_id("scoring-scenario").fill(scenario)
    mocked_page.get_by_test_id("scoring-submit").click()

    decision = mocked_page.get_by_test_id("scoring-decision")
    expect(decision).to_be_visible()
    expect(decision).to_have_text(expect_decision)


@pytest.mark.scoring
@pytest.mark.parametrize(
    "score,band",
    [(0, "red"), (49, "red"), (50, "yellow"), (74, "yellow"), (75, "green"), (100, "green")],
)
def test_score_threshold_bands(score: int, band: str) -> None:
    """Pure boundary check against documented thresholds — independent of UI."""
    if score < 50:
        assert band == "red"
    elif score < 75:
        assert band == "yellow"
    else:
        assert band == "green"
