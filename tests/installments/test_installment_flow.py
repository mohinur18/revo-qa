from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from playwright.sync_api import Page

# NOTE: Page objects below are scaffolds — selectors must be confirmed against the live UI
# the first time we walk through the flow with real test credentials.


@pytest.mark.installments
@pytest.mark.smoke
def test_create_installment_plan_generates_correct_schedule(admin_page: Page) -> None:
    """
    Given a customer with sufficient scoring,
    when an admin creates a 6-month installment for 6,000,000 UZS,
    the system should generate 6 monthly payments of 1,000,000 UZS each,
    starting one month from today.
    """
    pytest.skip("TODO: implement once selectors for /installments/new are captured")


@pytest.mark.installments
def test_record_full_payment_marks_installment_paid(admin_page: Page) -> None:
    pytest.skip("TODO: implement after schedule fixture exists")


@pytest.mark.installments
def test_partial_payment_reduces_outstanding_balance(admin_page: Page) -> None:
    pytest.skip("TODO: implement after schedule fixture exists")


@pytest.mark.installments
def test_overdue_installment_triggers_status_change(admin_page: Page) -> None:
    pytest.skip("TODO: implement once date-faking / backend hooks are available")


@pytest.mark.installments
def test_cancel_plan_blocks_further_payments(admin_page: Page) -> None:
    pytest.skip("TODO: implement after cancellation UI is confirmed")


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
    """Sanity check the reference calculation we'll assert against in UI tests."""
    monthly = (principal / months).quantize(Decimal("0.01"))
    assert monthly == expected_monthly
