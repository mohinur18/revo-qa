from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

# Drives the real QR-payment UI in the mock Nova shell. The form posts to
# /nova-api/qr-payments; the "simulate" buttons post a PSP callback to
# /nova-api/payments/callback. Amount validation is enforced client-side before any POST.

PAYMENTS_URL = "/nova/resources/payments"


def _create_installment(page: Page, principal: int = 6_000_000, months: int = 6) -> int:
    """Seed an installment via the same-origin mock API so we have a target to pay."""
    return page.evaluate(
        """async ({principal, months}) => {
            const r = await fetch('/nova-api/installments', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({customer_id: 101, principal, months}),
            });
            return (await r.json()).id;
        }""",
        {"principal": principal, "months": months},
    )


def _fill_qr_form(page: Page, iid: int, schedule_n: str, amount: str) -> None:
    page.get_by_test_id("qr-installment_id").fill(str(iid))
    page.get_by_test_id("qr-schedule_n").fill(schedule_n)
    page.get_by_test_id("qr-amount").fill(amount)


def _generate(page: Page, iid: int, schedule_n: str, amount: str) -> None:
    _fill_qr_form(page, iid, schedule_n, amount)
    page.get_by_test_id("qr-generate").click()
    expect(page.get_by_test_id("qr-result")).to_be_visible()


@pytest.mark.payments
@pytest.mark.mocked
@pytest.mark.smoke
def test_qr_code_renders_for_valid_amount(mocked_page: Page) -> None:
    mocked_page.goto(PAYMENTS_URL, wait_until="domcontentloaded")
    iid = _create_installment(mocked_page)
    _generate(mocked_page, iid, "1", "1000000")

    code = mocked_page.get_by_test_id("qr-code")
    expect(code).to_be_visible()
    expect(code).to_contain_text("REVOQR-")
    expect(mocked_page.get_by_test_id("qr-status")).to_have_text("pending")


@pytest.mark.payments
@pytest.mark.mocked
def test_qr_pay_success_callback_marks_payment_paid(mocked_page: Page) -> None:
    mocked_page.goto(PAYMENTS_URL, wait_until="domcontentloaded")
    iid = _create_installment(mocked_page)
    _generate(mocked_page, iid, "1", "1000000")

    mocked_page.get_by_test_id("qr-sim-success").click()
    expect(mocked_page.get_by_test_id("qr-status")).to_have_text("success")


@pytest.mark.payments
@pytest.mark.mocked
def test_qr_pay_failure_shows_error_state(mocked_page: Page) -> None:
    mocked_page.goto(PAYMENTS_URL, wait_until="domcontentloaded")
    iid = _create_installment(mocked_page)
    _generate(mocked_page, iid, "1", "1000000")

    mocked_page.get_by_test_id("qr-sim-failed").click()
    expect(mocked_page.get_by_test_id("qr-status")).to_have_text("failed")


@pytest.mark.payments
@pytest.mark.mocked
@pytest.mark.parametrize(
    "amount,should_succeed",
    [
        ("1000", True),
        ("0", False),
        ("-100", False),
        ("999999999999", False),
    ],
)
def test_qr_amount_validation(mocked_page: Page, amount: str, should_succeed: bool) -> None:
    mocked_page.goto(PAYMENTS_URL, wait_until="domcontentloaded")
    iid = _create_installment(mocked_page)
    _fill_qr_form(mocked_page, iid, "1", amount)
    mocked_page.get_by_test_id("qr-generate").click()

    if should_succeed:
        expect(mocked_page.get_by_test_id("qr-code")).to_be_visible()
        expect(mocked_page.get_by_test_id("qr-amount-error")).to_be_hidden()
    else:
        expect(mocked_page.get_by_test_id("qr-amount-error")).to_be_visible()
        expect(mocked_page.get_by_test_id("qr-result")).to_be_hidden()


@pytest.mark.payments
@pytest.mark.mocked
def test_qr_pay_idempotent_double_submit(mocked_page: Page) -> None:
    """Two submits for the same {installment, row} must not create a second pending charge."""
    mocked_page.goto(PAYMENTS_URL, wait_until="domcontentloaded")
    iid = _create_installment(mocked_page)
    _fill_qr_form(mocked_page, iid, "1", "1000000")

    generate = mocked_page.get_by_test_id("qr-generate")
    generate.click()
    expect(mocked_page.get_by_test_id("qr-code")).to_be_visible()

    generate.click()  # second attempt → server returns 409
    error = mocked_page.get_by_test_id("qr-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text("pending")
