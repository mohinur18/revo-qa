from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, Route


@pytest.mark.payments
@pytest.mark.smoke
def test_qr_code_renders_for_valid_amount(admin_page: Page) -> None:
    pytest.skip("TODO: confirm /payments/qr route and QR canvas selector")


@pytest.mark.payments
def test_qr_pay_success_callback_marks_payment_paid(admin_page: Page) -> None:
    """
    Intercept the PSP webhook simulation endpoint and assert the UI transitions
    from 'awaiting' to 'paid' within polling window.
    """

    def mock_status(route: Route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"status": "PAID", "transactionId": "test-tx-001"}),
        )

    admin_page.route(re.compile(r".*/api/payments/qr/.*/status"), mock_status)
    pytest.skip("TODO: wire route mock to real flow once endpoints are captured")


@pytest.mark.payments
def test_qr_pay_failure_shows_error_state(admin_page: Page) -> None:
    pytest.skip("TODO: implement after failure UI is confirmed")


@pytest.mark.payments
@pytest.mark.parametrize(
    "amount,should_succeed",
    [
        ("1000", True),
        ("0", False),
        ("-100", False),
        ("999999999999", False),
    ],
)
def test_qr_amount_validation(admin_page: Page, amount: str, should_succeed: bool) -> None:
    pytest.skip("TODO: implement against real amount input")


@pytest.mark.payments
def test_qr_pay_idempotent_double_submit(admin_page: Page) -> None:
    """Two rapid submits with same idempotency key must not create duplicate charges."""
    pytest.skip("TODO: implement once we know which header carries the idempotency key")
