"""
Send a Telegram notification based on test + defect reports.

When this script decides to notify:
  - gating job fails (any failed test) → always notify
  - observability job finds 1+ high-severity defect → notify
  - Otherwise → silent (returns 0 without posting)

Inputs:
  - reports/report.json
  - reports/defect-report.json
  - env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  - argv: --job gating|observability --status success|failure --run-url URL [--dry-run]

If env credentials are missing → exits 0 with a log line (so CI on forks doesn't fail).
Telegram messages are capped at 4000 chars to stay under the 4096 limit.

Zero deps: stdlib only (urllib).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Console UTF-8 — needed for emoji on Windows; harmless elsewhere
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REPORT = Path("reports/report.json")
DEFECT_REPORT = Path("reports/defect-report.json")

TG_MAX = 4000  # leave 96 chars of headroom under the 4096 hard limit


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _telegram_send(token: str, chat_id: str, text: str) -> int:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def _build_message(
    job: str,
    status: str,
    run_url: str,
    report: dict[str, Any],
    defects_doc: dict[str, Any],
) -> tuple[str, bool]:
    """Return (message_text, should_send)."""
    summary = report.get("run", {}).get("summary", {}) or {}
    base_url = report.get("run", {}).get("base_url", "?")
    defect_counts = (defects_doc.get("summary") or {}).get("defects") or {}
    high = int(defect_counts.get("high", 0))
    medium = int(defect_counts.get("medium", 0))
    low = int(defect_counts.get("low", 0))
    failed_count = int(summary.get("failed", 0))

    has_high = high > 0

    # Notification gate:
    #   - Gating: trust the CI step's --status (pytest exit code surfaces here).
    #   - Observability: continue-on-error masks pytest exit code, so use defect counts.
    if job == "gating":
        should_send = status == "failure"
    elif job == "observability":
        should_send = has_high
    else:
        should_send = status == "failure" or has_high

    if not should_send:
        return "", False

    if job == "gating":
        emoji = "🔴"
        title = f"Gating job FAILED — {failed_count} test(s) failing"
    elif has_high:
        emoji = "⚠️"
        title = f"{high} high-severity defect(s) detected"
    else:
        emoji = "⚠️"
        title = "CI alert"

    e = html.escape

    lines: list[str] = []
    lines.append(f"{emoji} <b>{e(title)}</b>")
    lines.append(f"<i>revo-qa · {e(job)}</i> · <code>{e(base_url)}</code>")
    lines.append("")

    lines.append(
        "📊 <b>Tests:</b> "
        f"✅ {summary.get('passed', 0)} · "
        f"❌ {failed_count} · "
        f"⏭ {summary.get('skipped', 0)} · "
        f"✋ {summary.get('xfailed', 0)}"
    )
    if high or medium or low:
        lines.append(
            "🛡 <b>Defects:</b> "
            f"🔴 {high} high · "
            f"🟡 {medium} medium · "
            f"⚪ {low} low"
        )

    high_defects = [d for d in defects_doc.get("defects", []) if d.get("severity") == "high"]
    if high_defects:
        lines.append("")
        lines.append("<b>High-severity:</b>")
        for d in high_defects[:5]:
            lines.append(f"• {e(d.get('title', '?'))}")
            lines.append(f"  <code>{e(d.get('test', '?'))}</code>")
        if len(high_defects) > 5:
            lines.append(f"  <i>… and {len(high_defects) - 5} more (see report)</i>")

    lines.append("")
    lines.append(f'🔗 <a href="{e(run_url)}">View CI run &amp; download report</a>')

    text = "\n".join(lines)
    if len(text) > TG_MAX:
        text = text[: TG_MAX - 20] + "\n…<i>(truncated)</i>"
    return text, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, choices=["gating", "observability"])
    ap.add_argument("--status", required=True, choices=["success", "failure", "cancelled"])
    ap.add_argument("--run-url", default=os.environ.get("CI_RUN_URL", ""))
    ap.add_argument("--dry-run", action="store_true", help="Print to stdout, don't POST")
    args = ap.parse_args()

    report = _load(REPORT)
    defects = _load(DEFECT_REPORT)

    text, should_send = _build_message(args.job, args.status, args.run_url, report, defects)

    if not should_send:
        print(f"[notify-telegram] No notification needed for job={args.job} status={args.status}.")
        return 0

    if args.dry_run:
        print("[notify-telegram] DRY RUN — would send:")
        print("-" * 60)
        print(text)
        print("-" * 60)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(
            "[notify-telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping send. "
            "(This is fine for forks / local runs.)"
        )
        return 0

    try:
        status_code = _telegram_send(token, chat_id, text)
        print(f"[notify-telegram] Sent ({status_code}, {len(text)} chars).")
        return 0
    except urllib.error.HTTPError as e:
        print(
            f"[notify-telegram] HTTP {e.code} from Telegram API: {e.read().decode('utf-8', 'replace')}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as e:
        print(f"[notify-telegram] Network error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
