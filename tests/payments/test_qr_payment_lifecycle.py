"""
QR payment simulation — end-to-end lifecycle tests against the mocked PSP layer.

These tests drive all requests through the BrowserContext route layer (via fetch in
page.evaluate), so they exercise the same mock pipeline the SPA would.

Coverage (representative, not exhaustive):
  1. Happy path: create + success callback → installment balance + row status update
  2. Callback for unknown payment_id → 400
  3. Duplicate pending payment for the same {installment, row} → 409
  4. Terminal-state replay is idempotent; cross-terminal transition is 409
  5. Payment creation after installment completion → 400
"""
from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page


def _post(page: Page, url: str, body: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate(
        """async ({url, body}) => {
            const r = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            return { status: r.status, body: await r.json() };
        }""",
        {"url": url, "body": body},
    )


def _get(page: Page, url: str) -> dict[str, Any]:
    return page.evaluate(
        """async (url) => {
            const r = await fetch(url, {headers: {'Accept': 'application/json'}});
            return { status: r.status, body: await r.json() };
        }""",
        url,
    )


@pytest.fixture
def ctx_page(mocked_page: Page) -> Page:
    """SPA up so we have a same-origin fetch context; the rest is API-driven."""
    mocked_page.goto("/nova/dashboards/main", wait_until="domcontentloaded")
    return mocked_page


def _create_installment(page: Page, principal: float = 6_000_000, months: int = 6) -> int:
    resp = _post(
        page,
        "/nova-api/installments",
        {"customer_id": 101, "principal": principal, "months": months},
    )
    assert resp["status"] == 201, resp
    return resp["body"]["id"]


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

@pytest.mark.payments
@pytest.mark.mocked
@pytest.mark.smoke
def test_successful_qr_payment_updates_installment_balance(ctx_page: Page) -> None:
    iid = _create_installment(ctx_page)

    # Phase 1: customer initiates a QR payment for row 1
    create_resp = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )
    assert create_resp["status"] == 201
    payment = create_resp["body"]
    assert payment["status"] == "pending"
    assert payment["qr_code"].startswith("REVOQR-")
    pid = payment["id"]

    # Installment is still untouched at this point — payment is pending, not settled
    installment = _get(ctx_page, f"/nova-api/installments/{iid}")["body"]
    assert installment["balance"] == 6_000_000
    assert installment["schedule"][0]["status"] == "pending"

    # Phase 2: PSP callback delivers a SUCCESS
    cb_resp = _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": pid, "status": "success", "transaction_id": "TX-ABCDEF-001"},
    )
    assert cb_resp["status"] == 200
    settled = cb_resp["body"]
    assert settled["status"] == "success"
    assert settled["transaction_id"] == "TX-ABCDEF-001"
    assert settled["settled_at"] is not None

    # Side-effect chain: balance reduced, row 1 marked paid, installment still active
    installment = _get(ctx_page, f"/nova-api/installments/{iid}")["body"]
    assert installment["balance"] == 5_000_000
    assert installment["schedule"][0]["status"] == "paid"
    assert installment["schedule"][0]["paid_amount"] == 1_000_000
    assert installment["status"] == "active"


# ---------------------------------------------------------------------------
# 2. Callback for unknown payment_id
# ---------------------------------------------------------------------------

@pytest.mark.payments
@pytest.mark.mocked
def test_callback_for_unknown_payment_returns_400(ctx_page: Page) -> None:
    resp = _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": 999999, "status": "success", "transaction_id": "TX-GHOST"},
    )
    assert resp["status"] == 400
    assert "not found" in resp["body"]["error"]


# ---------------------------------------------------------------------------
# 3. Duplicate pending payment for same {installment, row}
# ---------------------------------------------------------------------------

@pytest.mark.payments
@pytest.mark.mocked
def test_duplicate_pending_payment_rejected(ctx_page: Page) -> None:
    iid = _create_installment(ctx_page)

    first = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )
    assert first["status"] == 201

    second = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )
    assert second["status"] == 409
    assert "already pending" in second["body"]["error"]

    # A failed first payment must NOT lock the row — customer can scan a new QR
    pid = first["body"]["id"]
    _post(ctx_page, "/nova-api/payments/callback", {"payment_id": pid, "status": "failed"})

    retry = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )
    assert retry["status"] == 201, "retry after failed must succeed (row not locked)"


# ---------------------------------------------------------------------------
# 4. Idempotency on terminal state
# ---------------------------------------------------------------------------

@pytest.mark.payments
@pytest.mark.mocked
def test_callback_idempotency_and_terminal_immutability(ctx_page: Page) -> None:
    iid = _create_installment(ctx_page)
    pid = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )["body"]["id"]

    first = _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": pid, "status": "success", "transaction_id": "TX-001"},
    )
    assert first["status"] == 200
    assert first["body"]["status"] == "success"

    # Replay same callback → 200 no-op (PSP retry safety). Balance must NOT double-debit.
    replay = _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": pid, "status": "success", "transaction_id": "TX-001"},
    )
    assert replay["status"] == 200

    installment = _get(ctx_page, f"/nova-api/installments/{iid}")["body"]
    assert installment["balance"] == 5_000_000, "side-effect must apply exactly once"
    assert installment["schedule"][0]["paid_amount"] == 1_000_000

    # Cross-terminal transition (success → failed) must be rejected
    bad = _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": pid, "status": "failed"},
    )
    assert bad["status"] == 409
    assert "terminal" in bad["body"]["error"]


# ---------------------------------------------------------------------------
# 5. Payment creation after installment completion
# ---------------------------------------------------------------------------

@pytest.mark.payments
@pytest.mark.mocked
def test_payment_after_installment_completed_rejected(ctx_page: Page) -> None:
    # 1-month plan, single payment closes it out
    iid = _create_installment(ctx_page, principal=1_000_000, months=1)
    pid = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 1_000_000},
    )["body"]["id"]

    _post(
        ctx_page,
        "/nova-api/payments/callback",
        {"payment_id": pid, "status": "success"},
    )

    installment = _get(ctx_page, f"/nova-api/installments/{iid}")["body"]
    assert installment["status"] == "completed"
    assert installment["balance"] == 0.0

    # Now: attempt a second QR payment against the completed installment
    rejected = _post(
        ctx_page,
        "/nova-api/qr-payments",
        {"installment_id": iid, "schedule_n": 1, "amount": 100},
    )
    assert rejected["status"] == 400
    assert "completed" in rejected["body"]["error"]
