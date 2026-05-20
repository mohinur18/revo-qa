from __future__ import annotations

from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect

# These drive the real installment UI in the mock Nova shell: the create form posts to
# /nova-api/installments, the schedule + pay controls post to /payments and /cancel.
# All requests flow through the same BrowserContext mock pipeline the SPA uses.

INSTALLMENTS_URL = "/nova/resources/installments"


def _create_plan(
    page: Page,
    *,
    customer_id: str = "101",
    principal: str,
    months: str,
    start_date: str | None = None,
) -> None:
    page.get_by_test_id("customer_id").fill(customer_id)
    page.get_by_test_id("principal").fill(principal)
    page.get_by_test_id("months").fill(months)
    if start_date:
        page.get_by_test_id("start_date").fill(start_date)
    page.get_by_test_id("installment-submit").click()
    expect(page.get_by_test_id("installment-detail")).to_be_visible()


@pytest.mark.installments
@pytest.mark.mocked
@pytest.mark.smoke
def test_create_installment_plan_generates_correct_schedule(mocked_page: Page) -> None:
    mocked_page.goto(INSTALLMENTS_URL, wait_until="domcontentloaded")
    _create_plan(mocked_page, principal="6000000", months="6")

    rows = mocked_page.get_by_test_id("schedule-row")
    expect(rows).to_have_count(6)
    for i in range(6):
        expect(rows.nth(i)).to_have_attribute("data-amount", "1000000")


@pytest.mark.installments
@pytest.mark.mocked
def test_record_full_payment_marks_installment_paid(mocked_page: Page) -> None:
    mocked_page.goto(INSTALLMENTS_URL, wait_until="domcontentloaded")
    _create_plan(mocked_page, principal="6000000", months="6")

    mocked_page.get_by_test_id("pay-amount-1").fill("1000000")
    mocked_page.get_by_test_id("pay-row-1").click()

    expect(mocked_page.get_by_test_id("row-status-1")).to_have_text("paid")


@pytest.mark.installments
@pytest.mark.mocked
def test_partial_payment_reduces_outstanding_balance(mocked_page: Page) -> None:
    mocked_page.goto(INSTALLMENTS_URL, wait_until="domcontentloaded")
    _create_plan(mocked_page, principal="6000000", months="6")

    mocked_page.get_by_test_id("pay-amount-1").fill("400000")
    mocked_page.get_by_test_id("pay-row-1").click()

    # 6,000,000 − 400,000 = 5,600,000 outstanding; row 1 still not fully paid.
    expect(mocked_page.get_by_test_id("installment-balance")).to_have_attribute(
        "data-balance", "5600000"
    )
    expect(mocked_page.get_by_test_id("row-status-1")).to_have_text("pending")


@pytest.mark.installments
@pytest.mark.mocked
def test_overdue_installment_triggers_status_change(mocked_page: Page) -> None:
    mocked_page.goto(INSTALLMENTS_URL, wait_until="domcontentloaded")
    # Past start date → every due date is behind "now", so the first row shows overdue.
    _create_plan(mocked_page, principal="6000000", months="6", start_date="2024-01-01")

    first = mocked_page.get_by_test_id("schedule-row").first
    expect(first).to_have_attribute("data-overdue", "true")
    expect(mocked_page.get_by_test_id("row-status-1")).to_have_text("overdue")


@pytest.mark.installments
@pytest.mark.mocked
def test_cancel_plan_blocks_further_payments(mocked_page: Page) -> None:
    mocked_page.goto(INSTALLMENTS_URL, wait_until="domcontentloaded")
    _create_plan(mocked_page, principal="6000000", months="6")

    mocked_page.get_by_test_id("cancel-plan").click()
    expect(mocked_page.get_by_test_id("installment-status")).to_have_text("cancelled")
    # Payment controls are disabled once the plan is no longer active.
    expect(mocked_page.get_by_test_id("pay-row-1")).to_be_disabled()


# --- Pure unit-like checks on schedule math — independent of UI ---


@pytest.mark.installments
@pytest.mark.parametrize(
    "principal,months,expected_monthly",
    [
        (Decimal("6000000"), 6, Decimal("1000000")),
        (Decimal("12000000"), 12, Decimal("1000000")),
        (Decimal("1000000"), 3, Decimal("333333.33")),
    ],
)
def test_schedule_math_reference(
    principal: Decimal, months: int, expected_monthly: Decimal
) -> None:
    """Sanity check the reference calculation we assert against in UI tests."""
    monthly = (principal / months).quantize(Decimal("0.01"))
    assert monthly == expected_monthly
