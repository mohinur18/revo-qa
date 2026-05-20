"""
Central API mock router. One entrypoint: install_mocks(context).

Strategy:
  1. Specific endpoints (auth, current user, menu, lists) get deterministic stubs.
  2. Everything matching /nova/api/* or /api/* falls through to a catch-all that returns 200
     with an empty success envelope — this prevents the SPA from crashing on unknown calls.
  3. Non-API requests (HTML shell, JS/CSS bundles, images) pass through to the real server
     so we test the actual production frontend, not a fake copy.

All mock responses include a `X-Mock: revo-qa` header so they're traceable in HAR/trace dumps.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Request, Route

from mocks.state import (
    CustomerError,
    CustomerStore,
    InstallmentError,
    InstallmentStore,
    QrPaymentError,
    QrPaymentStore,
    score_scenario,
)

logger = logging.getLogger("revo-qa.mocks")

DATA_DIR = Path(__file__).parent / "data"


def _load(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _fulfill(
    route: Route,
    body: Any,
    *,
    status: int = 200,
    extra_headers: dict[str, str] | None = None,
) -> None:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Mock": "revo-qa",
        "Access-Control-Allow-Origin": "*",
    }
    if extra_headers:
        headers.update(extra_headers)
    route.fulfill(
        status=status,
        headers=headers,
        body=json.dumps(body, ensure_ascii=False),
    )


# --- Route handlers ---------------------------------------------------------

def _handle_csrf_cookie(route: Route) -> None:
    """Laravel Sanctum-style: GET /sanctum/csrf-cookie sets XSRF-TOKEN cookie."""
    route.fulfill(
        status=204,
        headers={
            "X-Mock": "revo-qa",
            "Set-Cookie": "XSRF-TOKEN=mock-xsrf-token; Path=/; SameSite=Lax",
        },
    )


def _handle_login(route: Route) -> None:
    """Stub POST /nova/login — always succeed and set a session cookie."""
    route.fulfill(
        status=302,
        headers={
            "X-Mock": "revo-qa",
            "Location": "/nova/dashboards/main",
            "Set-Cookie": (
                "avtomato_session=mock-session-id; Path=/; SameSite=Lax; Secure; HttpOnly"
            ),
        },
        body="",
    )


def _handle_logout(route: Route) -> None:
    route.fulfill(
        status=302,
        headers={
            "X-Mock": "revo-qa",
            "Location": "/nova/login",
            "Set-Cookie": "avtomato_session=; Path=/; Max-Age=0",
        },
    )


def _handle_me(route: Route) -> None:
    _fulfill(route, _load("user.json"))


def _handle_menu(route: Route) -> None:
    _fulfill(route, _load("menu.json"))


def _handle_dashboard(route: Route) -> None:
    _fulfill(route, _load("dashboard.json"))


def _handle_devices(route: Route) -> None:
    _fulfill(route, _load("devices.json"))


def _handle_catchall_api(route: Route) -> None:
    """Fallback for unmapped /nova/api/* and /api/* — return a benign empty envelope."""
    req: Request = route.request
    logger.info("MOCK[catchall] %s %s", req.method, req.url)
    _fulfill(route, {"data": [], "meta": {"total": 0}})


_NOVA_SHELL_HTML = (DATA_DIR / "nova_shell.html").read_text(encoding="utf-8")


def _handle_nova_shell(route: Route) -> None:
    """
    Replace protected Nova HTML pages with our self-contained mock shell.

    This is the unavoidable trade-off when running without real credentials:
    Laravel sessions are cryptographically signed, so we can't bypass server-side
    auth with cookie injection alone. The shell mimics Nova's structure (#nova root,
    [data-dusk] attrs, basic nav/cards/tables) so page objects keep working.

    Tests against this shell verify the *framework* (routing, mock dispatch,
    visual stability, perf) — not real Nova frontend behavior.
    """
    route.fulfill(
        status=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "X-Mock": "revo-qa",
            "Cache-Control": "no-store",
        },
        body=_NOVA_SHELL_HTML,
    )


# --- Public API -------------------------------------------------------------

ROUTE_TABLE: list[tuple[re.Pattern[str], Callable[[Route], None]]] = [
    (re.compile(r".*/sanctum/csrf-cookie$"), _handle_csrf_cookie),
    (re.compile(r".*/nova/login(\?.*)?$"), _handle_login),
    (re.compile(r".*/nova/logout(\?.*)?$"), _handle_logout),
    (re.compile(r".*/nova-api/me(\?.*)?$"), _handle_me),
    (re.compile(r".*/nova-api/menu(\?.*)?$"), _handle_menu),
    (re.compile(r".*/nova-api/dashboards/main(\?.*)?$"), _handle_dashboard),
    (re.compile(r".*/nova-api/devices(\?.*)?$"), _handle_devices),
    (re.compile(r".*/nova-api/resources/devices(\?.*)?$"), _handle_devices),
]


_INSTALLMENT_URL_RE = re.compile(
    r"/nova-api/installments(?:/(\d+)(?:/(payments|cancel))?)?(?:\?.*)?$"
)
_CUSTOMER_URL_RE = re.compile(r"/nova-api/(?:resources/)?customers(?:/(\d+))?(?:\?.*)?$")


def _read_json_body(route: Route) -> dict:
    body = route.request.post_data or "{}"
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {}


def _parse_start_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _handle_installments(route: Route, store: InstallmentStore) -> None:
    """Stateful router for /nova-api/installments/* — uses per-context InstallmentStore."""
    method = route.request.method
    url = route.request.url
    m = _INSTALLMENT_URL_RE.search(url)
    if not m:
        _fulfill(route, {"data": [], "meta": {"total": 0}})
        return

    installment_id = int(m.group(1)) if m.group(1) else None
    action = m.group(2)  # 'payments' | 'cancel' | None

    try:
        if method == "GET" and installment_id is None:
            data = store.list_all()
            _fulfill(route, {"data": data, "meta": {"total": len(data)}})
            return

        if method == "GET" and installment_id is not None:
            rec = store.get(installment_id)
            if rec is None:
                _fulfill(route, {"error": "not found"}, status=404)
                return
            _fulfill(route, rec)
            return

        if method == "POST" and installment_id is None:
            body = _read_json_body(route)
            rec = store.create(
                customer_id=int(body.get("customer_id", 0)),
                principal=float(body.get("principal", 0)),
                months=int(body.get("months", 0)),
                start=_parse_start_date(body.get("start_date")),
            )
            _fulfill(route, rec, status=201)
            return

        if method == "POST" and action == "payments" and installment_id is not None:
            body = _read_json_body(route)
            rec = store.pay(
                iid=installment_id,
                payment_n=int(body.get("n", 1)),
                amount=float(body.get("amount", 0)),
            )
            _fulfill(route, rec)
            return

        if method == "POST" and action == "cancel" and installment_id is not None:
            rec = store.cancel(installment_id)
            _fulfill(route, rec)
            return
    except InstallmentError as e:
        _fulfill(route, {"error": str(e)}, status=400)
        return

    # Unrecognized method/path under /nova-api/installments → empty list
    _fulfill(route, {"data": [], "meta": {"total": 0}})


def _handle_customers(route: Route, store: CustomerStore) -> None:
    """Stateful router for /nova-api/customers and /nova-api/resources/customers."""
    method = route.request.method
    url = route.request.url
    m = _CUSTOMER_URL_RE.search(url)
    if not m:
        _fulfill(route, {"data": [], "meta": {"total": 0}})
        return

    cid = int(m.group(1)) if m.group(1) else None
    query = urlparse(url).query
    phone = (parse_qs(query).get("phone") or [None])[0]

    try:
        if method == "GET" and cid is None:
            data = store.list_all(phone=phone)
            _fulfill(route, {"data": data, "meta": {"total": len(data)}})
            return

        if method == "GET" and cid is not None:
            rec = store.get(cid)
            if rec is None:
                _fulfill(route, {"error": "not found"}, status=404)
                return
            _fulfill(route, rec)
            return

        if method == "POST" and cid is None:
            body = _read_json_body(route)
            rec = store.create(
                full_name=body.get("full_name", ""),
                phone=body.get("phone", ""),
                passport=body.get("passport", ""),
            )
            _fulfill(route, rec, status=201)
            return

        if method in ("PATCH", "PUT") and cid is not None:
            body = _read_json_body(route)
            rec = store.update(cid, **body)
            _fulfill(route, rec)
            return
    except CustomerError as e:
        _fulfill(route, {"error": str(e)}, status=e.http_status)
        return

    _fulfill(route, {"data": [], "meta": {"total": 0}})


def _handle_scoring(route: Route) -> None:
    """POST /nova-api/scoring {scenario} → deterministic decision."""
    if route.request.method != "POST":
        _fulfill(route, {"error": "method not allowed"}, status=405)
        return
    body = _read_json_body(route)
    try:
        _fulfill(route, score_scenario(str(body.get("scenario", ""))))
    except CustomerError as e:
        _fulfill(route, {"error": str(e)}, status=e.http_status)


_QR_PAYMENT_URL_RE = re.compile(r"/nova-api/qr-payments(?:/(\d+))?(?:\?.*)?$")
_PAYMENT_CALLBACK_URL_RE = re.compile(r"/nova-api/payments/callback(?:\?.*)?$")


def _handle_qr_payments(route: Route, qr_store: QrPaymentStore) -> None:
    """Stateful router for /nova-api/qr-payments[/{id}]."""
    url = route.request.url
    method = route.request.method
    m = _QR_PAYMENT_URL_RE.search(url)
    if not m:
        _fulfill(route, {"data": [], "meta": {"total": 0}})
        return
    pid_in_url = int(m.group(1)) if m.group(1) else None

    try:
        if method == "GET" and pid_in_url is None:
            data = qr_store.list_all()
            _fulfill(route, {"data": data, "meta": {"total": len(data)}})
            return
        if method == "GET" and pid_in_url is not None:
            rec = qr_store.get(pid_in_url)
            if rec is None:
                _fulfill(route, {"error": "not found"}, status=404)
                return
            _fulfill(route, rec)
            return
        if method == "POST" and pid_in_url is None:
            body = _read_json_body(route)
            rec = qr_store.create(
                installment_id=int(body.get("installment_id", 0)),
                schedule_n=int(body.get("schedule_n", 1)),
                amount=float(body.get("amount", 0)),
            )
            _fulfill(route, rec, status=201)
            return
    except QrPaymentError as e:
        _fulfill(route, {"error": str(e)}, status=e.http_status)
        return

    _fulfill(route, {"error": "method not allowed"}, status=405)


def _handle_payment_callback(route: Route, qr_store: QrPaymentStore) -> None:
    """
    Simulates a PSP webhook for QR payments.

    In production this endpoint would verify an HMAC signature from the PSP.
    For mocks we accept any body — security is out of scope for QA simulation.
    """
    if route.request.method != "POST":
        _fulfill(route, {"error": "method not allowed"}, status=405)
        return
    body = _read_json_body(route)
    try:
        rec = qr_store.apply_callback(
            payment_id=int(body.get("payment_id", 0)),
            status=str(body.get("status", "")),
            transaction_id=body.get("transaction_id"),
        )
        _fulfill(route, rec)
    except QrPaymentError as e:
        _fulfill(route, {"error": str(e)}, status=e.http_status)


def install_mocks(context: BrowserContext) -> None:
    """Register all route mocks on a Playwright BrowserContext."""
    # Per-context state. Both stores share the same context lifetime so the side-effect
    # chain (qr_store.apply_callback → installment_store.pay) is consistent.
    installment_store = InstallmentStore()
    qr_store = QrPaymentStore(installments=installment_store)
    customer_store = CustomerStore()

    # Stateful routes — extend by appending tuples here. The dispatcher iterates this
    # list once per request and short-circuits on first prefix match. No global state.
    # Order matters: more specific prefixes must precede the prefixes they contain.
    stateful_routes: list[tuple[str, Any]] = [
        ("/nova-api/payments/callback", lambda r: _handle_payment_callback(r, qr_store)),
        ("/nova-api/qr-payments", lambda r: _handle_qr_payments(r, qr_store)),
        ("/nova-api/installments", lambda r: _handle_installments(r, installment_store)),
        ("/nova-api/scoring", _handle_scoring),
        ("/nova-api/resources/customers", lambda r: _handle_customers(r, customer_store)),
        ("/nova-api/customers", lambda r: _handle_customers(r, customer_store)),
    ]

    def dispatcher(route: Route) -> None:
        url = route.request.url
        method = route.request.method

        # POST login
        if method == "POST" and "/nova/login" in url:
            _handle_login(route)
            return

        # Stateful resources — checked before the static table so they take precedence
        for prefix, handler in stateful_routes:
            if prefix in url:
                handler(route)
                return

        for pattern, handler in ROUTE_TABLE:
            if pattern.search(url):
                handler(route)
                return

        # API catch-all
        if "/nova-api/" in url or url.endswith("/api") or "/api/" in url:
            _handle_catchall_api(route)
            return

        # Protected Nova HTML pages → serve mock shell.
        is_html_request = "text/html" in (
            route.request.headers.get("accept", "")
            or route.request.headers.get("Accept", "")
        )
        if (
            is_html_request
            and method == "GET"
            and "/nova/login" not in url
            and ("/nova/dashboards/" in url or "/nova/resources/" in url or url.rstrip("/").endswith("/nova"))
        ):
            _handle_nova_shell(route)
            return

        # Everything else (JS, CSS, images, fonts, login HTML): pass through to real server.
        route.continue_()

    # Single context-wide route handles every request — simpler and faster than many
    context.route("**/*", dispatcher)


def inject_session(context: BrowserContext, base_url: str) -> None:
    """Drop in fake session + XSRF cookies so /nova/* believes we're authenticated."""
    host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    context.add_cookies(
        [
            {
                "name": "avtomato_session",
                "value": "mock-session-id",
                "domain": host,
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "XSRF-TOKEN",
                "value": "mock-xsrf-token",
                "domain": host,
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
        ]
    )
