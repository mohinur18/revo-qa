"""
Emit a GitHub Actions step-summary from reports/report.json.

Used by both CI jobs (gating + observability). Reads job_name + gating? from argv
so the summary header is contextual.

Usage:
    python scripts/ci_summary.py "Gating" true
    python scripts/ci_summary.py "Observability" false
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT = Path("reports/report.json")


def main() -> int:
    job_name = sys.argv[1] if len(sys.argv) > 1 else "E2E"
    is_gating = (len(sys.argv) > 2) and sys.argv[2].lower() == "true"

    if not REPORT.exists():
        print(f"## {job_name}\n\n_No report.json produced._")
        return 0

    r = json.loads(REPORT.read_text(encoding="utf-8"))
    run = r["run"]
    summary = run["summary"]

    lines: list[str] = []
    lines.append(f"## {job_name} results " + ("(gating)" if is_gating else "(advisory)"))
    lines.append("")
    if not is_gating:
        lines.append(
            "> Non-gating job — failures here are observable but do **not** block PR merges."
        )
        lines.append("")

    lines.append(f"**Duration:** {run['duration_s']}s · **Base URL:** {run['base_url']}")
    lines.append("")

    lines.append("| Outcome | Count |")
    lines.append("|---|---|")
    for outcome in ("passed", "failed", "xfailed", "xpassed", "skipped"):
        if outcome in summary:
            lines.append(f"| {outcome} | {summary[outcome]} |")
    lines.append("")

    failed = [t for t in r["tests"] if t["outcome"] == "failed"]
    if failed:
        lines.append("### Failed tests")
        lines.append("")
        for t in failed[:25]:
            err = (t.get("error") or {}).get("message", "")
            err_short = (err.splitlines()[0] if err else "")[:140]
            lines.append(f"- `{t['id']}`")
            if err_short:
                lines.append(f"  - {err_short}")
        if len(failed) > 25:
            lines.append(f"- _… and {len(failed) - 25} more (see report.json artifact)_")
        lines.append("")

    perf_tests = [t for t in r["tests"] if t.get("perf")]
    if perf_tests:
        lines.append("### Performance metrics")
        lines.append("")
        lines.append("| Page | TTFB | DCL | Load |")
        lines.append("|---|---|---|---|")
        for t in perf_tests[:10]:
            p = t["perf"]
            url = p.get("url", "?").split("/")[-1] or "/"
            lines.append(
                f"| {url} | {p.get('ttfb_ms','?')}ms | {p.get('dcl_ms','?')}ms | {p.get('load_ms','?')}ms |"
            )
        lines.append("")

    visual_changes = [t for t in r["tests"] if t.get("visual_diff") and t["visual_diff"].get("pct_changed", 0) > 0]
    if visual_changes:
        lines.append("### Visual changes")
        lines.append("")
        for t in visual_changes:
            v = t["visual_diff"]
            lines.append(f"- `{t['id']}` — {v['pct_changed']}% (threshold {v.get('threshold_pct','?')}%)")
        lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
