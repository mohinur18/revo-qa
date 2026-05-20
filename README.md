# revo-qa

[![e2e](https://github.com/mohinur18/revo-qa/actions/workflows/e2e.yml/badge.svg)](https://github.com/mohinur18/revo-qa/actions/workflows/e2e.yml)

Production-ready E2E automation suite for **revo.avtomato.uz** (Laravel Nova admin panel of Avtomato).
**Runs in CI with zero credentials, zero external dependencies.**

## What's in here

| Area | What it does |
|---|---|
| **Auth / robustness** | Login page negative cases, XSS / SQLi / long / unicode input handling, CSRF / HTTPS / autocomplete checks |
| **Security headers** | HSTS, X-Frame-Options / CSP frame-ancestors, X-Content-Type-Options, Referrer-Policy, CSP, cookie flags, HTTP→HTTPS redirect, server-version leakage |
| **Dashboard / Devices / Customers** | Page renders under mocked auth + mocked Nova API |
| **Visual regression** | Pillow-based pixel diff with baseline per page; diff PNG on mismatch |
| **Performance budget** | TTFB / DCL / full-load measured via Navigation Timing API; configurable budgets, fail on breach |
| **Structured reporting** | `reports/report.json` aggregates everything for CI consumption |

## Stack

- Python 3.12 + [uv](https://github.com/astral-sh/uv)
- Playwright 1.59 (Chromium)
- pytest 9 with `pytest-playwright`, `pytest-xdist`, `pytest-rerunfailures`, `pytest-html`
- Pillow for visual diff
- `faker` for synthetic data; `python-dotenv` for env

## Layout

```
revo-qa/
├── conftest.py                    # fixtures + JSON reporter plugin
├── pyproject.toml                 # deps, pytest config, markers
├── pages/                         # Page Objects
│   ├── base_page.py
│   ├── login_page.py
│   ├── dashboard_page.py
│   ├── devices_page.py
│   └── customers_page.py
├── flows/                         # composable helpers (not POM, not tests)
│   ├── auth.py                    # mocked-auth bypass
│   ├── performance.py             # Navigation Timing collection + budget
│   └── visual.py                  # baseline + diff helpers
├── mocks/                         # API mock layer
│   ├── router.py                  # install_mocks(context), inject_session(context)
│   └── data/
│       ├── user.json
│       ├── menu.json
│       ├── dashboard.json
│       ├── devices.json
│       ├── customers.json
│       └── nova_shell.html        # self-contained mock HTML for protected routes
├── tests/
│   ├── auth/                      # login + robustness
│   ├── security/                  # HTTP header / cookie audit
│   ├── dashboard/                 # mocked
│   ├── devices/                   # mocked
│   ├── customers/                 # mocked list + create/search/edit/scoring UI flows
│   ├── installments/ payments/    # create/pay/cancel + QR-pay UI flows + API lifecycle
│   ├── visual/                    # screenshot diff
│   │   └── __snapshots__/         # baselines (committed)
│   └── performance/               # perf budgets
├── scripts/probe_login.py         # one-off DOM inspector
├── reports/
│   ├── report.json                # structured aggregate
│   ├── report.html
│   ├── junit.xml
│   ├── screenshots/
│   └── visual/                    # diff PNGs on mismatch
└── .github/workflows/e2e.yml      # CI: zero secrets, mock-only
```

## Setup

```powershell
cd C:\Users\asus\revo-qa
uv venv --python 3.12
uv pip install -e .
uv run python -m playwright install chromium
# .env is optional — only needed for legacy real-creds tests (all xfail by default)
```

## Running

```powershell
# everything
uv run pytest

# smoke only (what PR CI runs)
uv run pytest -m "smoke and not perf"

# one suite
uv run pytest tests/dashboard

# update visual baselines (after intentional UI change)
$env:VISUAL_UPDATE = "1"; uv run pytest tests/visual

# loosen visual threshold (default 0.3% pixel change tolerated)
$env:VISUAL_THRESHOLD_PCT = "0.5"; uv run pytest tests/visual

# tighten or loosen perf budgets
$env:PERF_LOAD_MS = "3000"; uv run pytest tests/performance

# parallel
uv run pytest -n 4

# headed + slow-mo for debugging
uv run pytest --headed --slowmo 250 tests/dashboard
```

## How the no-creds architecture works

**Two layers stop tests from touching live backend:**

1. **`install_mocks(context)`** registers a single context-wide route handler that intercepts every request and:
   - Stubs `/sanctum/csrf-cookie`, `POST /nova/login`, `/nova/logout` with deterministic responses
   - Stubs `/nova-api/me`, `/menu`, `/dashboards/main`, `/resources/devices`, `/resources/customers` from JSON fixtures in `mocks/data/`
   - Returns benign empty envelopes (`{"data": [], "meta": {"total": 0}}`) for unmatched `/nova-api/*` and `/api/*` so the SPA doesn't crash on unknown endpoints
   - **Replaces protected HTML pages** (`/nova`, `/nova/dashboards/*`, `/nova/resources/*`) with `mocks/data/nova_shell.html` — a self-contained shell with `#nova`, `[data-dusk="resource-index"]`, nav, cards, tables. Required because Laravel signs sessions; cookie injection alone won't bypass server-side auth.
   - Passes through everything else (login HTML, JS bundles, CSS, fonts, images) to the real server

2. **`inject_session(context, base_url)`** drops fake `avtomato_session` + `XSRF-TOKEN` cookies into the context so client-side JS that checks for them is satisfied.

**Honest tradeoff**: the dashboard/devices/customers tests under mocked auth run against `nova_shell.html`, not the real Nova frontend. They validate framework correctness (routing, mock dispatch, visual stability, perf) — not real Nova behavior. To test against the real frontend, drop a recorded shell into `mocks/data/nova_shell.html` from a live session, or wire real credentials and remove the HTML interception.

**The auth/robustness and security-header suites run against the REAL frontend** (no HTML interception on `/nova/login`) and surface real production defects.

## Markers

| Marker | Purpose |
|---|---|
| `smoke` | Critical path — runs on every PR |
| `auth` | Authentication & session |
| `dashboard` / `customers` / `installments` / `payments` | feature suites |
| `security` | HTTP headers, cookies, transport |
| `visual` | Screenshot diff |
| `perf` | Performance budgets |
| `mocked` | Requires the mocked-backend fixture |
| `rbac` | Role-based access (needs real role creds) |
| `slow` | Excluded from PR smoke |

## CI

`.github/workflows/e2e.yml` splits into two jobs that run in parallel where triggers overlap:

| Job | Triggers | Tests | Blocks merge? |
|---|---|---|---|
| **`gating`** | `pull_request`, `push`, `workflow_dispatch` | `tests/auth` + `tests/dashboard` + `tests/devices` + `tests/customers/test_customers_mocked.py`<br>`-m "not perf and not visual and not security"` | **Yes** — failures fail the build |
| **`observability`** | `push`, `schedule` (nightly), `workflow_dispatch` | `tests/security` + `tests/performance` + `tests/visual` | **No** — `continue-on-error: true` at job AND step level. Failures surface in artifacts and step summary but never block PRs. |

**Zero secrets** in either job — `MOCK_BACKEND=1` is hard-coded in workflow env.

**Artifacts:**
- `report-json-gating` (30d) and `report-json-observability` (30d) — one structured JSON per job
- `visual-diffs` (14d) — baselines + diffs + screenshots from the observability run
- `artifacts-gating` / `artifacts-observability` (14d) — full Playwright traces, videos, HTML reports

**Step summary** (in the GH Actions UI) is rendered by `scripts/ci_summary.py`. Includes:
- outcome counts (passed/failed/xfailed/skipped)
- failed-test list with first-line error
- performance metrics table (TTFB / DCL / Load per page)
- visual changes table (% pixel diff per snapshot)

The observability summary is clearly labeled "advisory" so reviewers know these failures aren't gating.

## Telegram notifications

`scripts/notify_telegram.py` runs at the end of each CI job (`if: always()`) and decides whether to send a Telegram message based on:

| Job | Notify when |
|---|---|
| `gating` | `${{ job.status }}` == `failure` — i.e., pytest exited non-zero and the build is breaking |
| `observability` | `defect-report.json` reports `defects.high > 0` (regardless of CI step status, since the job is `continue-on-error`) |

If neither condition holds, the script logs `No notification needed` and exits 0.

**Setup:** Add two secrets to the repo (Settings → Secrets and variables → Actions):

| Secret | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather), copy the token |
| `TELEGRAM_CHAT_ID` | Add the bot to your target channel/group, send any message, then GET `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `result[0].message.chat.id` |

If either secret is missing, the script logs a clear "skipping send" message and exits 0 — so forks and PRs from external contributors don't fail.

**Message format** (HTML, `parse_mode=HTML`):

- 🔴 / ⚠️ + title (gating fail vs N high-severity defects)
- Test counts: passed / failed / skipped / xfailed
- Defect counts: high / medium / low
- Top 5 high-severity defects with title + test ID
- Link to the CI run for full artifacts

Local dry-run (no Telegram POST, prints to stdout):

```powershell
uv run python scripts/notify_telegram.py --job observability --status failure --run-url "https://example/run/1" --dry-run
```

## Defect report

After every CI run, `scripts/defect_report.py` reads `reports/report.json` and emits:

- `reports/defect-report.md` — for QA / dev triage. Sections grouped by category (Security / Functional / Performance / Visual), each defect tagged `[HIGH] / [MEDIUM] / [LOW]` with title, test ID, impact, and a best-effort suggested fix.
- `reports/defect-report.json` — same data structured for API / dashboard consumption.

The generator is fully deterministic — same `report.json` always produces byte-identical output. Severity and fix suggestions come from a `KNOWN_DEFECTS` lookup keyed on bare test name, with category-based fallbacks (security failures → medium for triage, perf → medium, visual → low, anything else functional → medium).

Both CI jobs (`gating` and `observability`) generate the defect report on `if: always()` after pytest, upload it as part of their `reports-*` artifact, and inline it into the GitHub step summary. Observability inlines as an open `<details>` block since those failures are advisory.

To add or refine a defect entry, edit the `KNOWN_DEFECTS` dict in `scripts/defect_report.py` — keyed on the bare test name (no path, no params). Local regenerate:

```powershell
uv run python scripts/defect_report.py
```

## Structured report schema

`reports/report.json`:

```json
{
  "run": {
    "started_at": "ISO-8601",
    "ended_at": "ISO-8601",
    "duration_s": 62.32,
    "exit_status": 1,
    "base_url": "...",
    "mock_backend": true,
    "summary": {"passed": 48, "failed": 5, "xfailed": 4, "skipped": 20}
  },
  "tests": [
    {
      "id": "tests/dashboard/test_dashboard_mocked.py::test_dashboard_within_perf_budget[chromium]",
      "outcome": "passed|failed|xfailed|skipped",
      "duration_s": 0.059,
      "markers": ["dashboard", "mocked", "perf"],
      "perf": {"ttfb_ms": 4.9, "dcl_ms": 36.1, "load_ms": 36.1, "transfer_kb": 5.0},
      "visual_diff": {"pct_changed": 0.0, "passed": true, "diff_path": null},
      "screenshots": ["..."],
      "error": null
    }
  ]
}
```

## Test design principles

1. **Selector priority**: `data-testid` / `[dusk="…"]` > `getByRole` > `getByLabel` > CSS. Never XPath.
2. **No `wait_for_timeout`** — always use web-first assertions (`expect(...).to_be_visible()`).
3. **`expect(...).to_have_url(...)`** in Python accepts `str` or `re.Pattern` — **never a lambda** (that's JS-only).
4. **Mocks first, mocks always** — the suite should never depend on production data shape or availability.
5. **Visual baselines committed to git** so PR diffs surface intentional vs unintentional UI changes.
6. **Perf budgets configurable per env** so CI runners with different hardware aren't fighting the suite.

## Known limitations

- The customer / installment / QR-payment UI flows run against the mock Nova shell (`mocks/data/nova_shell.html`) wired to the stateful mock stores in `mocks/state.py`. They exercise the full create→render→mutate pipeline (forms, schedule generation, payment, cancel, scoring decision, PSP callback) but against the mock UI, not the real Nova frontend. To run them against production, record a real Nova shell + endpoints, or wire real creds and disable the HTML interception.
- Cyrillic test outputs on Windows console need `PYTHONIOENCODING=utf-8` set (see `scripts/probe_login.py`).
- The `legacy auth tests` under `tests/auth/test_login.py::test_valid_admin_login` etc. are `xfail` until real creds are configured.

## Known defects surfaced by the suite

Five real findings on `revo.avtomato.uz` as of this README:

| Severity | Test | Finding |
|---|---|---|
| High | `test_http_redirects_to_https` | `http://revo.avtomato.uz/nova/login` returns 200 OK over plain HTTP. Credentials MITM-able. |
| High | `test_hsts_header_present` | No `Strict-Transport-Security` header set. |
| High | `test_session_cookie_flags` | `avtomato_session` missing `Secure` flag. |
| Medium | `test_content_security_policy_present` | CSP contains only `upgrade-insecure-requests`; no `default-src` / `script-src`. |
| Low | `test_referrer_policy_set` | No `Referrer-Policy` header. |

These should be filed as defect tickets against the app, not muted in the suite. If you need to unblock PR merges before fixes ship, move them to a separate CI job that runs but doesn't gate.
