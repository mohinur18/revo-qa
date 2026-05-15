"""
Generate a human-readable defect report from reports/report.json.

Emits:
  - reports/defect-report.md   (for QA/dev review)
  - reports/defect-report.json (for API/dashboard consumption)

Determinism:
  - Defects sorted by (severity_rank, category, test_id) — stable across runs
  - generated_at is taken from the source report.json's run.ended_at, so the same
    input always produces the same output
  - No randomness, no UUIDs, no current-time calls

Categorization rules (deterministic, code-driven):
  - Security tests (path: tests/security/) → severity from KNOWN_DEFECTS table
  - Performance tests (path: tests/performance/ or marker: perf) → medium
  - Visual tests (path: tests/visual/ or marker: visual) → low
  - Anything else that failed → functional / medium
  - xfailed and skipped tests are NOT defects
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPORT = Path("reports/report.json")
OUT_MD = Path("reports/defect-report.md")
OUT_JSON = Path("reports/defect-report.json")

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# ---------------------------------------------------------------------------
# Known-defect lookup. Keyed on the *bare* test name (no path, no params).
# Each entry: (severity, title, impact, suggested_fix).
# When a test isn't in this table, fallbacks below kick in based on category.
# ---------------------------------------------------------------------------

KNOWN_DEFECTS: dict[str, tuple[str, str, str, str]] = {
    "test_hsts_header_present": (
        "high",
        "Missing HSTS header",
        "Security — MITM risk via protocol-downgrade attack on credentials/sessions",
        "Add `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` to all responses. "
        "In nginx: `add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;`. "
        "In Laravel: middleware or trustproxies + force-https-after-deploy.",
    ),
    "test_http_redirects_to_https": (
        "high",
        "Plain HTTP is served instead of redirecting to HTTPS",
        "Security — credentials submitted over HTTP are MITM-able; combined with missing HSTS, "
        "an active attacker can keep users on HTTP indefinitely",
        "Configure the edge (nginx/Cloudflare) to 301-redirect all HTTP → HTTPS. "
        "Example nginx: `server { listen 80; server_name revo.avtomato.uz; return 301 https://$host$request_uri; }`.",
    ),
    "test_session_cookie_flags": (
        "high",
        "Session cookie missing Secure flag",
        "Security — `avtomato_session` can be sent over HTTP on downgrade and leak the session",
        "In Laravel `config/session.php` set `'secure' => env('SESSION_SECURE_COOKIE', true)` and ship "
        "`SESSION_SECURE_COOKIE=true` in production .env. Also set `'same_site' => 'lax'` and "
        "ensure XSRF-TOKEN inherits the Secure flag.",
    ),
    "test_content_security_policy_present": (
        "medium",
        "No effective Content-Security-Policy",
        "Security — current CSP only declares `upgrade-insecure-requests`. No directive constrains "
        "script/style sources, so the browser can't help defend against XSS.",
        "Add a baseline CSP, then tighten: "
        "`Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self';`. Iterate via Report-Only mode first.",
    ),
    "test_referrer_policy_set": (
        "low",
        "No Referrer-Policy header",
        "Security/Privacy — full URLs (including query strings) may leak to third-party domains in Referer headers",
        "Add `Referrer-Policy: strict-origin-when-cross-origin` at the edge or Laravel middleware.",
    ),
    "test_server_header_does_not_leak_version": (
        "low",
        "Server header leaks nginx version",
        "Security — information disclosure; helps an attacker map known CVEs to the deployed version",
        "In nginx config add `server_tokens off;` at the http or server level. "
        "Optionally remove the header entirely with `more_clear_headers Server;` (nginx-extras / headers-more module).",
    ),
    "test_x_powered_by_not_set": (
        "low",
        "X-Powered-By header present",
        "Security — discloses framework / PHP version",
        "Set `expose_php = Off` in php.ini; remove via web server config if any layer adds it back.",
    ),
}


def _bare_test_name(test_id: str) -> str:
    """Strip path + params: 'tests/x/test_y.py::test_z[chromium-foo]' → 'test_z'."""
    if "::" in test_id:
        test_id = test_id.split("::", 1)[1]
    test_id = test_id.split("[", 1)[0]
    return test_id


def _category(test_id: str, markers: list[str]) -> str:
    if "tests/security/" in test_id or "security" in markers:
        return "security"
    if "tests/performance/" in test_id or "perf" in markers:
        return "performance"
    if "tests/visual/" in test_id or "visual" in markers:
        return "visual"
    return "functional"


def _classify(test: dict[str, Any]) -> dict[str, Any]:
    """Map a failed test → (severity, title, impact, fix)."""
    bare = _bare_test_name(test["id"])
    markers: list[str] = test.get("markers") or []
    category = _category(test["id"], markers)

    if bare in KNOWN_DEFECTS:
        severity, title, impact, fix = KNOWN_DEFECTS[bare]
    elif category == "performance":
        severity = "medium"
        title = f"Performance budget breached: {bare}"
        impact = "Performance — page exceeded configured TTFB/DCL/Load budget"
        fix = (
            "Inspect the perf entry in report.json. Common fixes: reduce JS bundle size, "
            "enable Brotli compression, add HTTP/2 server push for critical CSS, lazy-load below-the-fold assets. "
            "If the budget itself is wrong, tune PERF_*_MS env vars in the workflow."
        )
    elif category == "visual":
        severity = "low"
        title = f"Visual regression: {bare}"
        impact = "UX — rendered output differs from committed baseline beyond tolerance"
        fix = (
            "Open `reports/visual/<name>-diff.png` from the CI artifact. "
            "If the change is intentional, refresh baseline locally with "
            "`VISUAL_UPDATE=1 pytest tests/visual` and commit the new snapshot."
        )
    elif category == "security":
        # Unknown security test — flag at medium for triage
        severity = "medium"
        title = f"Security check failed: {bare}"
        impact = "Security — see error message; classify and add to KNOWN_DEFECTS table"
        fix = "Investigate failure, add entry to `scripts/defect_report.py` KNOWN_DEFECTS once classified."
    else:
        severity = "medium"
        title = f"Functional failure: {bare}"
        impact = "Functional — user-facing flow broken or assertion mismatch"
        err_msg = (test.get("error") or {}).get("message", "")
        if "selector" in err_msg.lower() or "locator" in err_msg.lower():
            fix = (
                "Selector mismatch — confirm the DOM hasn't changed. Run `scripts/probe_login.py` "
                "(or a similar probe) against the live page and update the locator in `pages/`."
            )
        elif "mock" in err_msg.lower() or "401" in err_msg or "302" in err_msg or "login" in err_msg.lower():
            fix = (
                "Mocked auth/route may have regressed. Check `mocks/router.py` route table covers "
                "the endpoint, and that `inject_session()` runs before navigation."
            )
        else:
            fix = "Inspect the error message and stack trace in the report.json artifact; reproduce locally with `pytest <test_id> -v --tb=long`."

    return {
        "severity": severity,
        "category": category,
        "title": title,
        "impact": impact,
        "fix": fix,
    }


def _slug(text: str) -> str:
    """Deterministic kebab-case slug for defect IDs."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:80]


def build_defects(report: dict[str, Any]) -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    for t in report.get("tests", []):
        if t.get("outcome") != "failed":
            continue
        meta = _classify(t)
        err = t.get("error") or {}
        defects.append(
            {
                "id": _slug(meta["title"]),
                "severity": meta["severity"],
                "category": meta["category"],
                "title": meta["title"],
                "test": t["id"],
                "impact": meta["impact"],
                "fix": meta["fix"],
                "error": (err.get("message") or "").splitlines()[0][:300] if err else "",
                "duration_s": t.get("duration_s"),
            }
        )
    # Deterministic sort: severity → category → test id
    defects.sort(key=lambda d: (SEVERITY_RANK[d["severity"]], d["category"], d["test"]))
    return defects


def severity_counts(defects: list[dict[str, Any]]) -> dict[str, int]:
    out = {"high": 0, "medium": 0, "low": 0}
    for d in defects:
        out[d["severity"]] += 1
    return out


def render_md(report: dict[str, Any], defects: list[dict[str, Any]]) -> str:
    run = report.get("run", {})
    summary = run.get("summary", {})
    sev = severity_counts(defects)

    lines: list[str] = []
    lines.append("# Defect Report")
    lines.append("")
    lines.append(f"_Generated from `reports/report.json` (run ended {run.get('ended_at', '?')})._")
    lines.append("")
    lines.append(f"- **Base URL:** {run.get('base_url', '?')}")
    lines.append(f"- **Run duration:** {run.get('duration_s', '?')}s")
    lines.append(f"- **Mock backend:** {run.get('mock_backend', '?')}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    total = sum(summary.values())
    lines.append(f"| Total tests | {total} |")
    for k in ("passed", "failed", "xfailed", "xpassed", "skipped"):
        if k in summary:
            lines.append(f"| {k.capitalize()} | {summary[k]} |")
    lines.append(f"| **Defects (failed → triaged)** | **{len(defects)}** |")
    lines.append(f"| → High | {sev['high']} |")
    lines.append(f"| → Medium | {sev['medium']} |")
    lines.append(f"| → Low | {sev['low']} |")
    lines.append("")

    if not defects:
        lines.append("## No defects 🎉")
        lines.append("")
        lines.append("All tests in this run passed (or are accepted as `xfail`).")
        return "\n".join(lines) + "\n"

    # Group by category for the human report
    by_cat: dict[str, list[dict[str, Any]]] = {
        "security": [],
        "functional": [],
        "performance": [],
        "visual": [],
    }
    for d in defects:
        by_cat[d["category"]].append(d)

    cat_titles = {
        "security": "Security issues",
        "functional": "Functional failures",
        "performance": "Performance issues",
        "visual": "Visual regressions",
    }

    lines.append("## Defects by category")
    lines.append("")

    for cat, title in cat_titles.items():
        items = by_cat[cat]
        if not items:
            continue
        lines.append(f"### {title} ({len(items)})")
        lines.append("")
        for d in items:
            lines.append(f"#### [{d['severity'].upper()}] {d['title']}")
            lines.append("")
            lines.append(f"- **Test:** `{d['test']}`")
            lines.append(f"- **Impact:** {d['impact']}")
            lines.append(f"- **Suggested fix:** {d['fix']}")
            if d.get("error"):
                lines.append(f"- **Error:** `{d['error']}`")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Severities and fix suggestions are best-effort heuristics keyed off test names. "
        "Triage before filing tickets; the source of truth is `reports/report.json`._"
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if not REPORT.exists():
        print(f"[defect-report] {REPORT} not found — nothing to do", file=sys.stderr)
        return 0

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    defects = build_defects(report)

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_md(report, defects), encoding="utf-8")

    out_json = {
        "generated_at": report.get("run", {}).get("ended_at"),
        "source": str(REPORT),
        "base_url": report.get("run", {}).get("base_url"),
        "summary": {
            "tests": report.get("run", {}).get("summary", {}),
            "defects": {
                "total": len(defects),
                **severity_counts(defects),
            },
        },
        "defects": defects,
    }
    OUT_JSON.write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[defect-report] wrote {OUT_MD} and {OUT_JSON} — "
        f"{len(defects)} defects (high={severity_counts(defects)['high']}, "
        f"medium={severity_counts(defects)['medium']}, "
        f"low={severity_counts(defects)['low']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
