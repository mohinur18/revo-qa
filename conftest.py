from __future__ import annotations

import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from mocks import install_mocks
from mocks.router import inject_session

load_dotenv()

logger = logging.getLogger("revo-qa")

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
VISUAL_DIR = REPORTS_DIR / "visual"
AUTH_DIR = ROOT / ".auth"

for d in (REPORTS_DIR, SCREENSHOTS_DIR, VISUAL_DIR, AUTH_DIR):
    d.mkdir(parents=True, exist_ok=True)


# --- Core fixtures ---------------------------------------------------------


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("REVO_BASE_URL", "https://revo.avtomato.uz").rstrip("/")


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return os.getenv("REVO_API_BASE_URL", "https://revo.avtomato.uz/api").rstrip("/")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url):
    return {
        **browser_context_args,
        "base_url": base_url,
        "viewport": {"width": 1440, "height": 900},
        "locale": "ru-RU",
        "timezone_id": "Asia/Tashkent",
        "ignore_https_errors": False,
    }


# --- Mocked-backend fixtures (no real auth, no real backend) ---------------


@pytest.fixture
def mocked_context(
    browser: Browser, browser_context_args, base_url: str
) -> Iterator[BrowserContext]:
    ctx = browser.new_context(**browser_context_args)
    install_mocks(ctx)
    inject_session(ctx, base_url)
    yield ctx
    ctx.close()


@pytest.fixture
def mocked_page(mocked_context: BrowserContext) -> Iterator[Page]:
    page = mocked_context.new_page()
    page.set_default_timeout(int(os.getenv("DEFAULT_TIMEOUT_MS", "15000")))
    page.set_default_navigation_timeout(int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000")))
    yield page
    page.close()


@pytest.fixture
def anon_page(browser: Browser, browser_context_args) -> Iterator[Page]:
    """Unauthenticated, unmocked page — for login-page tests and security probes."""
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    yield page
    ctx.close()


# --- Legacy real-auth fixtures (kept for when real creds exist) -----------


@pytest.fixture(scope="session")
def admin_storage_state(playwright: Playwright, browser: Browser, base_url) -> Path:
    """Log in once per session as admin. Only used by xfail tests when real creds exist."""
    from pages.login_page import LoginPage

    state_file = AUTH_DIR / "admin.json"
    if state_file.exists() and os.getenv("REUSE_AUTH", "1") == "1":
        return state_file

    login = os.getenv("REVO_ADMIN_LOGIN")
    password = os.getenv("REVO_ADMIN_PASSWORD")
    if not login or not password:
        pytest.skip("Real admin creds not configured — set REVO_ADMIN_LOGIN/PASSWORD to enable")

    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    LoginPage(page).login(login, password)
    page.wait_for_load_state("networkidle")
    context.storage_state(path=str(state_file))
    context.close()
    return state_file


@pytest.fixture
def admin_context(
    browser: Browser, browser_context_args, admin_storage_state: Path
) -> Iterator[BrowserContext]:
    ctx = browser.new_context(**{**browser_context_args, "storage_state": str(admin_storage_state)})
    yield ctx
    ctx.close()


@pytest.fixture
def admin_page(admin_context: BrowserContext) -> Iterator[Page]:
    page = admin_context.new_page()
    page.set_default_timeout(int(os.getenv("DEFAULT_TIMEOUT_MS", "15000")))
    page.set_default_navigation_timeout(int(os.getenv("NAVIGATION_TIMEOUT_MS", "30000")))
    yield page
    page.close()


# --- JSON reporter plugin --------------------------------------------------
#
# Aggregates per-test outcome, error info, perf metrics, and screenshot paths into
# reports/report.json so CI can consume a single structured artifact.

_RESULTS: list[dict[str, Any]] = []
_RUN_STARTED_AT: float = 0.0


@pytest.fixture
def record(request: pytest.FixtureRequest) -> dict[str, Any]:
    """
    Per-test scratch dict. Tests use this to record perf metrics, screenshots, etc.
    Anything you put here ends up in reports/report.json under that test's entry.

    Example:
        def test_x(record, mocked_page):
            record["perf"] = collect(mocked_page).as_dict()
    """
    bucket: dict[str, Any] = {}
    request.node._revo_record = bucket  # type: ignore[attr-defined]
    return bucket


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call):
    outcome = yield
    rep: pytest.TestReport = outcome.get_result()
    if rep.when != "call" and rep.when != "setup":
        return
    # Only collect once per test — prefer the 'call' phase
    if rep.when == "setup" and rep.passed:
        return

    record_bucket = getattr(item, "_revo_record", {}) or {}

    entry: dict[str, Any] = {
        "id": rep.nodeid,
        "outcome": rep.outcome,
        "duration_s": round(rep.duration, 3),
        "phase": rep.when,
        "markers": sorted(m.name for m in item.iter_markers()),
        "perf": record_bucket.get("perf"),
        "screenshots": record_bucket.get("screenshots", []),
        "visual_diff": record_bucket.get("visual_diff"),
        "error": None,
    }

    if rep.failed:
        excinfo = call.excinfo
        if excinfo is not None:
            entry["error"] = {
                "type": excinfo.typename,
                "message": str(excinfo.value),
                "traceback": "".join(traceback.format_tb(excinfo.tb))[-2000:],
            }
        else:
            entry["error"] = {"type": "Unknown", "message": str(rep.longrepr)[:2000]}

    if hasattr(rep, "wasxfail"):
        entry["outcome"] = "xfailed" if rep.skipped else "xpassed"
        entry["xfail_reason"] = rep.wasxfail

    _RESULTS.append(entry)


@pytest.hookimpl
def pytest_sessionstart(session: pytest.Session) -> None:
    global _RUN_STARTED_AT
    _RUN_STARTED_AT = time.time()
    _RESULTS.clear()


@pytest.hookimpl
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    summary: dict[str, int] = {}
    for r in _RESULTS:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1

    ended = time.time()
    report = {
        "run": {
            "started_at": datetime.fromtimestamp(_RUN_STARTED_AT, tz=timezone.utc).isoformat(),
            "ended_at": datetime.fromtimestamp(ended, tz=timezone.utc).isoformat(),
            "duration_s": round(ended - _RUN_STARTED_AT, 2),
            "exit_status": exitstatus,
            "base_url": os.getenv("REVO_BASE_URL", "https://revo.avtomato.uz"),
            "mock_backend": os.getenv("MOCK_BACKEND", "1") == "1",
            "summary": summary,
        },
        "tests": _RESULTS,
    }
    out = REPORTS_DIR / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[revo-qa] structured report written: {out}")
