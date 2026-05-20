"""
In-memory state for stateful mocks (currently: installments).

One InstallmentStore per BrowserContext means tests are isolated — each `mocked_page`
fixture gets a fresh store with no carry-over between tests.

Money: stored as floats for transport (JSON-friendly) but quantized via Decimal so
the rounding-diff line is absorbed by the LAST installment (standard practice — keeps
the schedule total exactly equal to principal).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

CENT = Decimal("0.01")
DATA_DIR = Path(__file__).parent / "data"

# Terminal states for a QR payment. Once a payment lands in any of these,
# it MUST NOT transition further (fintech invariant).
QR_PAYMENT_TERMINAL = frozenset({"success", "failed", "cancelled"})
QR_PAYMENT_VALID_CALLBACK_STATUSES = frozenset({"success", "failed", "cancelled"})


class InstallmentError(ValueError):
    """Domain errors from the installment store. Routes translate these to HTTP 400."""


class QrPaymentError(ValueError):
    """Domain errors from the QR payment store. Routes translate these to HTTP 400/404/409."""

    def __init__(self, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


class CustomerError(ValueError):
    """Domain errors from the customer store. Routes translate these to HTTP 400/404."""

    def __init__(self, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass
class InstallmentStore:
    """
    Stateful store: create installments with generated schedules, accept payments,
    update balance + status.

    State transitions for an installment record:
        active        — created, balance > 0
        completed     — balance reduced to 0 (any path)

    State transitions for a schedule row:
        pending       — paid_amount < amount
        paid          — paid_amount >= amount
        (overdue is a derived view, not stored — depends on "today")
    """

    _installments: dict[int, dict[str, Any]] = None  # type: ignore[assignment]
    _next_id: int = 5001

    def __post_init__(self) -> None:
        if self._installments is None:
            self._installments = {}

    # ---- queries -------------------------------------------------------

    def list_all(self) -> list[dict[str, Any]]:
        # Deterministic order: newest first by id
        return sorted(self._installments.values(), key=lambda r: -r["id"])

    def get(self, iid: int) -> dict[str, Any] | None:
        return self._installments.get(iid)

    # ---- commands ------------------------------------------------------

    def create(
        self,
        *,
        customer_id: int,
        principal: float,
        months: int,
        start: date | None = None,
    ) -> dict[str, Any]:
        if months < 1:
            raise InstallmentError("months must be >= 1")
        if principal <= 0:
            raise InstallmentError("principal must be > 0")

        start = start or date(2026, 5, 14)  # fixed default — deterministic across runs
        principal_d = Decimal(str(principal))
        base_monthly = (principal_d / months).quantize(CENT, rounding=ROUND_HALF_UP)

        schedule: list[dict[str, Any]] = []
        assigned = Decimal("0")
        for n in range(1, months + 1):
            # Last installment absorbs the rounding diff so schedule.sum == principal exactly
            amount = base_monthly if n < months else (principal_d - assigned)
            assigned += amount
            schedule.append(
                {
                    "n": n,
                    "due_date": (start + timedelta(days=30 * n)).isoformat(),
                    "amount": float(amount),
                    "paid_amount": 0.0,
                    "status": "pending",
                }
            )

        iid = self._next_id
        self._next_id += 1
        rec = {
            "id": iid,
            "customer_id": customer_id,
            "principal": float(principal_d),
            "months": months,
            "balance": float(principal_d),
            "status": "active",
            "schedule": schedule,
            "created_at": date(2026, 5, 14).isoformat(),
        }
        self._installments[iid] = rec
        return rec

    def pay(self, iid: int, payment_n: int, amount: float) -> dict[str, Any]:
        rec = self._installments.get(iid)
        if rec is None:
            raise InstallmentError(f"installment {iid} not found")
        if rec["status"] != "active":
            raise InstallmentError(f"installment {iid} is {rec['status']}, cannot accept payment")
        if amount <= 0:
            raise InstallmentError("payment amount must be > 0")
        if not (1 <= payment_n <= rec["months"]):
            raise InstallmentError(f"payment_n {payment_n} out of range 1..{rec['months']}")

        sched = rec["schedule"][payment_n - 1]
        sched["paid_amount"] = float(
            (Decimal(str(sched["paid_amount"])) + Decimal(str(amount))).quantize(CENT)
        )
        if sched["paid_amount"] + 1e-9 >= sched["amount"]:
            sched["status"] = "paid"

        rec["balance"] = float(
            max(Decimal("0"), Decimal(str(rec["balance"])) - Decimal(str(amount))).quantize(CENT)
        )
        if rec["balance"] <= 0.005:
            rec["balance"] = 0.0
            rec["status"] = "completed"

        return rec

    def cancel(self, iid: int) -> dict[str, Any]:
        rec = self._installments.get(iid)
        if rec is None:
            raise InstallmentError(f"installment {iid} not found")
        if rec["status"] == "completed":
            raise InstallmentError(f"installment {iid} is completed, cannot cancel")
        rec["status"] = "cancelled"
        return rec


@dataclass
class QrPaymentStore:
    """
    Stateful QR-payment ledger. Two-phase lifecycle:
        Phase 1 (create):     pending  — QR generated, awaiting customer scan + PSP callback
        Phase 2 (callback):   pending → success | failed | cancelled  (terminal)

    Collaborates with InstallmentStore: on a successful callback, applies the payment
    to the target installment row. The installment store stays unaware of QR mechanics —
    single responsibility.

    Idempotency contract (matches real PSP behavior):
      - Replaying the same callback (same payment_id, same status) is a 200 no-op.
      - Trying to change a terminal payment's status returns 409 Conflict.
      - Creating a second pending payment for the same {installment, row} returns 409.
        Failed/cancelled don't lock the row — customer can scan a new QR.
    """

    installments: InstallmentStore
    _payments: dict[int, dict[str, Any]] = field(default_factory=dict)
    _next_id: int = 9001

    # ---- queries -------------------------------------------------------

    def list_all(self) -> list[dict[str, Any]]:
        return sorted(self._payments.values(), key=lambda r: -r["id"])

    def get(self, pid: int) -> dict[str, Any] | None:
        return self._payments.get(pid)

    # ---- commands ------------------------------------------------------

    def create(
        self,
        *,
        installment_id: int,
        schedule_n: int,
        amount: float,
    ) -> dict[str, Any]:
        installment = self.installments.get(installment_id)
        if installment is None:
            raise QrPaymentError(f"installment {installment_id} not found", http_status=404)
        if installment["status"] != "active":
            raise QrPaymentError(
                f"installment {installment_id} is {installment['status']}, cannot accept payment"
            )
        if not (1 <= schedule_n <= installment["months"]):
            raise QrPaymentError(
                f"schedule_n {schedule_n} out of range 1..{installment['months']}"
            )

        row = installment["schedule"][schedule_n - 1]
        if row["status"] == "paid":
            raise QrPaymentError(f"schedule row {schedule_n} is already paid")
        if amount <= 0:
            raise QrPaymentError("amount must be > 0")

        # Idempotency: reject if another payment is already in-flight for this scope.
        for existing in self._payments.values():
            if (
                existing["installment_id"] == installment_id
                and existing["schedule_n"] == schedule_n
                and existing["status"] == "pending"
            ):
                raise QrPaymentError(
                    f"another payment {existing['id']} is already pending for row {schedule_n}",
                    http_status=409,
                )

        pid = self._next_id
        self._next_id += 1
        rec: dict[str, Any] = {
            "id": pid,
            "installment_id": installment_id,
            "schedule_n": schedule_n,
            "amount": float(amount),
            "status": "pending",
            "qr_code": f"REVOQR-{pid:08d}",  # deterministic, no randomness
            "transaction_id": None,
            "created_at": "2026-05-14T00:00:00Z",
            "settled_at": None,
        }
        self._payments[pid] = rec
        return rec

    def apply_callback(
        self,
        *,
        payment_id: int,
        status: str,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        rec = self._payments.get(payment_id)
        if rec is None:
            # Unknown payment — return 400 so the PSP knows the request is malformed
            # (vs. 404 which some PSPs interpret as "retry later").
            raise QrPaymentError(f"payment {payment_id} not found", http_status=400)
        if status not in QR_PAYMENT_VALID_CALLBACK_STATUSES:
            raise QrPaymentError(f"invalid callback status: {status!r}")

        # Idempotency: same payment, same status → no-op. PSPs retry on ambiguity;
        # returning 200 with the existing state stops the retry storm.
        if rec["status"] == status:
            return rec

        # Anything other than pending → pending is forbidden, and terminal → other-terminal
        # is forbidden. Payments are immutable once settled.
        if rec["status"] != "pending":
            raise QrPaymentError(
                f"payment {payment_id} is {rec['status']} (terminal); cannot transition to {status}",
                http_status=409,
            )

        rec["status"] = status
        rec["transaction_id"] = transaction_id
        rec["settled_at"] = "2026-05-14T00:01:00Z"

        if status == "success":
            # Side-effect: apply to the installment ledger. The installment store
            # enforces its own invariants (active status, range, etc.); if those fail
            # the payment was created against a stale view — roll it back to failed.
            try:
                self.installments.pay(
                    iid=rec["installment_id"],
                    payment_n=rec["schedule_n"],
                    amount=rec["amount"],
                )
            except InstallmentError as e:
                rec["status"] = "failed"
                rec["transaction_id"] = None
                raise QrPaymentError(
                    f"side-effect failed; payment marked failed: {e}", http_status=409
                ) from e

        return rec


# Deterministic scoring decisions keyed on a named scenario. The real engine would
# read credit-bureau + internal history; for QA we pin known scenarios to known outcomes
# so the scoring screen is testable without live data.
SCORING_SCENARIOS: dict[str, dict[str, Any]] = {
    "clean-history": {
        "decision": "approved",
        "score": 85,
        "band": "green",
        "reason": "No overdue history; passport on file; sufficient scoring",
    },
    "missing-passport": {
        "decision": "incomplete",
        "score": 0,
        "band": "red",
        "reason": "Passport data missing — application cannot be scored",
    },
    "active-overdue": {
        "decision": "rejected",
        "score": 30,
        "band": "red",
        "reason": "Customer has an active overdue installment",
    },
}


def score_scenario(scenario: str) -> dict[str, Any]:
    """Map a named scoring scenario to its deterministic decision."""
    if scenario not in SCORING_SCENARIOS:
        raise CustomerError(f"unknown scoring scenario: {scenario!r}")
    return {"scenario": scenario, **SCORING_SCENARIOS[scenario]}


def _load_customer_seed() -> list[dict[str, Any]]:
    """Single source of truth for seeded customers: mocks/data/customers.json."""
    raw = json.loads((DATA_DIR / "customers.json").read_text(encoding="utf-8"))
    return list(raw.get("data", []))


@dataclass
class CustomerStore:
    """
    Stateful customer ledger. Seeded with the same three records the static fixture used,
    so list/visual tests keep their data, while create/search/edit exercise real mutations.

    A created customer starts `active` with a neutral score; tests assert on the round-trip
    (create → appears in list, edit → persists across re-fetch) rather than on a scoring model.
    """

    _customers: dict[int, dict[str, Any]] = None  # type: ignore[assignment]
    _next_id: int = 104

    def __post_init__(self) -> None:
        if self._customers is None:
            self._customers = {c["id"]: dict(c) for c in _load_customer_seed()}
            self._next_id = max(self._customers, default=100) + 1

    # ---- queries -------------------------------------------------------

    def list_all(self, phone: str | None = None) -> list[dict[str, Any]]:
        rows = sorted(self._customers.values(), key=lambda r: r["id"])
        if phone:
            needle = phone.strip()
            rows = [r for r in rows if needle in r["phone"]]
        return rows

    def get(self, cid: int) -> dict[str, Any] | None:
        return self._customers.get(cid)

    # ---- commands ------------------------------------------------------

    def create(self, *, full_name: str, phone: str, passport: str) -> dict[str, Any]:
        full_name = (full_name or "").strip()
        phone = (phone or "").strip()
        passport = (passport or "").strip()
        if not full_name:
            raise CustomerError("full_name is required")
        if not phone:
            raise CustomerError("phone is required")
        if not passport:
            raise CustomerError("passport is required")
        if any(c["phone"] == phone for c in self._customers.values()):
            raise CustomerError(f"phone {phone} already exists", http_status=409)

        cid = self._next_id
        self._next_id += 1
        rec = {
            "id": cid,
            "full_name": full_name,
            "phone": phone,
            "passport": passport,
            "score": 70,
            "status": "active",
            "active_installments": 0,
            "created_at": "2026-05-14T00:00:00Z",
        }
        self._customers[cid] = rec
        return rec

    def update(self, cid: int, **fields: Any) -> dict[str, Any]:
        rec = self._customers.get(cid)
        if rec is None:
            raise CustomerError(f"customer {cid} not found", http_status=404)
        for key in ("full_name", "phone", "passport", "status"):
            if key in fields and fields[key] is not None:
                value = fields[key]
                rec[key] = value.strip() if isinstance(value, str) else value
        return rec

