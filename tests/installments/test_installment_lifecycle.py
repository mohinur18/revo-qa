"""
End-to-end installment lifecycle through the mocked backend.

Flow under test:
  1. POST /nova-api/installments → creates installment, returns generated schedule
  2. Schedule integrity: row count == months, sum(amounts) == principal,
     due dates monotonically increasing
  3. POST /nova-api/installments/{id}/payments — first payment
  4. After payment: balance = principal - amount, row[0].status = "paid",
     installment.status = "active" (still has remaining balance)
  5. Pay remaining → installment.status = "completed", balance = 0

All HTTP goes through the BrowserContext mock layer — we drive it via fetch() inside
page.evaluate() so the requests pass through the same route interception used by the SPA.
That means this test exercises the production mock pipeline end-to-end.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page


def _post(page: Page, url: str, body: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate(
        """async ({url, body}) => {
            const r = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
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
def page_for_api(mocked_page: Page) -> Page:
    """
    Bring the SPA up so we have a same-origin context for fetch(),
    but we drive the rest of the test through the API directly.
    """
    mocked_page.goto("/nova/dashboards/main", wait_until="domcontentloaded")
    return mocked_page


@pytest.mark.installments
@pytest.mark.mocked
@pytest.mark.smoke
def test_create_installment_generates_correct_schedule(page_for_api: Page) -> None:
    """6,000,000 UZS over 6 months → 6 rows, each 1,000,000, dates ascending."""
    resp = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 6_000_000, "months": 6},
    )
    assert resp["status"] == 201, resp
    rec = resp["body"]

    assert rec["status"] == "active"
    assert rec["balance"] == 6_000_000
    assert rec["months"] == 6
    assert len(rec["schedule"]) == 6

    amounts = [row["amount"] for row in rec["schedule"]]
    assert all(a == 1_000_000 for a in amounts), amounts
    assert sum(amounts) == rec["principal"]

    statuses = {row["status"] for row in rec["schedule"]}
    assert statuses == {"pending"}

    dates = [row["due_date"] for row in rec["schedule"]]
    assert dates == sorted(dates), "due dates must be monotonically increasing"


@pytest.mark.installments
@pytest.mark.mocked
def test_uneven_split_absorbed_by_last_payment(page_for_api: Page) -> None:
    """1,000,000 / 3 = 333_333.33; last row absorbs the rounding diff so total == principal."""
    rec = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 1_000_000, "months": 3},
    )["body"]

    amounts = [row["amount"] for row in rec["schedule"]]
    assert amounts[0] == 333_333.33
    assert amounts[1] == 333_333.33
    # Last row is principal - sum(others), so total stays exact
    assert round(sum(amounts), 2) == 1_000_000.00


@pytest.mark.installments
@pytest.mark.mocked
@pytest.mark.smoke
def test_first_payment_updates_balance_and_status(page_for_api: Page) -> None:
    create = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 6_000_000, "months": 6},
    )["body"]
    iid = create["id"]

    pay = _post(
        page_for_api,
        f"/nova-api/installments/{iid}/payments",
        {"n": 1, "amount": 1_000_000},
    )
    assert pay["status"] == 200
    rec = pay["body"]

    assert rec["balance"] == 5_000_000
    assert rec["schedule"][0]["paid_amount"] == 1_000_000
    assert rec["schedule"][0]["status"] == "paid"
    # Remaining 5 rows untouched
    assert all(row["status"] == "pending" for row in rec["schedule"][1:])
    # Installment overall still active (balance > 0)
    assert rec["status"] == "active"


@pytest.mark.installments
@pytest.mark.mocked
def test_payment_persists_across_subsequent_get(page_for_api: Page) -> None:
    """Mocks must remember state — subsequent GET reflects the prior POST."""
    create = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 6_000_000, "months": 6},
    )["body"]
    iid = create["id"]

    _post(page_for_api, f"/nova-api/installments/{iid}/payments", {"n": 1, "amount": 1_000_000})

    fetched = _get(page_for_api, f"/nova-api/installments/{iid}")["body"]
    assert fetched["balance"] == 5_000_000
    assert fetched["schedule"][0]["status"] == "paid"


@pytest.mark.installments
@pytest.mark.mocked
def test_paying_all_installments_marks_completed(page_for_api: Page) -> None:
    """Status transitions active → completed when balance hits 0."""
    create = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 3_000_000, "months": 3},
    )["body"]
    iid = create["id"]

    for n in (1, 2, 3):
        rec = _post(
            page_for_api,
            f"/nova-api/installments/{iid}/payments",
            {"n": n, "amount": 1_000_000},
        )["body"]

    assert rec["balance"] == 0.0
    assert rec["status"] == "completed"
    assert all(row["status"] == "paid" for row in rec["schedule"])


@pytest.mark.installments
@pytest.mark.mocked
def test_invalid_payment_rejected(page_for_api: Page) -> None:
    """Domain rule violations return HTTP 400 with an error message."""
    create = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 101, "principal": 1_000_000, "months": 1},
    )["body"]
    iid = create["id"]

    # Zero amount
    bad = _post(page_for_api, f"/nova-api/installments/{iid}/payments", {"n": 1, "amount": 0})
    assert bad["status"] == 400
    assert "error" in bad["body"]

    # Out-of-range payment number
    bad = _post(page_for_api, f"/nova-api/installments/{iid}/payments", {"n": 99, "amount": 100})
    assert bad["status"] == 400

    # Pay it off, then try paying again
    _post(page_for_api, f"/nova-api/installments/{iid}/payments", {"n": 1, "amount": 1_000_000})
    bad = _post(page_for_api, f"/nova-api/installments/{iid}/payments", {"n": 1, "amount": 100})
    assert bad["status"] == 400
    assert "completed" in bad["body"]["error"]


@pytest.mark.installments
@pytest.mark.mocked
def test_invalid_creation_rejected(page_for_api: Page) -> None:
    bad = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 1, "principal": 0, "months": 6},
    )
    assert bad["status"] == 400

    bad = _post(
        page_for_api,
        "/nova-api/installments",
        {"customer_id": 1, "principal": 1000, "months": 0},
    )
    assert bad["status"] == 400


@pytest.mark.installments
@pytest.mark.mocked
def test_stores_are_isolated_between_contexts(browser, browser_context_args, base_url) -> None:
    """Each BrowserContext gets a fresh store — no cross-test bleeding."""
    from mocks import install_mocks
    from mocks.router import inject_session

    def fetch_list(ctx):
        page = ctx.new_page()
        page.goto(f"{base_url}/nova/dashboards/main", wait_until="domcontentloaded")
        out = page.evaluate(
            """async () => (await fetch('/nova-api/installments')).json()"""
        )
        page.close()
        return out

    ctx_a = browser.new_context(**browser_context_args)
    install_mocks(ctx_a)
    inject_session(ctx_a, base_url)

    page = ctx_a.new_page()
    page.goto(f"{base_url}/nova/dashboards/main", wait_until="domcontentloaded")
    page.evaluate(
        """async () => fetch('/nova-api/installments', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({customer_id: 1, principal: 1000, months: 2}),
        })"""
    )
    page.close()
    list_a = fetch_list(ctx_a)
    assert list_a["meta"]["total"] == 1

    ctx_b = browser.new_context(**browser_context_args)
    install_mocks(ctx_b)
    inject_session(ctx_b, base_url)
    list_b = fetch_list(ctx_b)
    assert list_b["meta"]["total"] == 0, "fresh context must have empty store"

    ctx_a.close()
    ctx_b.close()
