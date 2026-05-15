"""
Lightweight performance measurement via the Navigation Timing API.

We don't pull in a Lighthouse-style harness — just the headline metrics that matter
for a CRM admin frontend: DCL, full load, TTFB. Configurable thresholds in pyproject.toml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page


@dataclass
class PerfMetrics:
    url: str
    ttfb_ms: float
    dcl_ms: float
    load_ms: float
    transfer_kb: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "ttfb_ms": round(self.ttfb_ms, 1),
            "dcl_ms": round(self.dcl_ms, 1),
            "load_ms": round(self.load_ms, 1),
            "transfer_kb": round(self.transfer_kb, 1) if self.transfer_kb is not None else None,
        }


_SCRIPT = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  if (!nav) return null;
  return {
    url: location.href,
    ttfb: nav.responseStart - nav.requestStart,
    dcl: nav.domContentLoadedEventEnd - nav.startTime,
    load: nav.loadEventEnd - nav.startTime,
    transferSize: nav.transferSize || null,
  };
}
"""


def collect(page: Page) -> PerfMetrics:
    """Call AFTER page.wait_for_load_state('load') so loadEventEnd is populated."""
    page.wait_for_load_state("load")
    data = page.evaluate(_SCRIPT)
    if data is None:
        raise RuntimeError("Navigation Timing API returned no data — page may not have loaded")
    return PerfMetrics(
        url=data["url"],
        ttfb_ms=data["ttfb"],
        dcl_ms=data["dcl"],
        load_ms=data["load"],
        transfer_kb=(data["transferSize"] / 1024) if data["transferSize"] else None,
    )


@dataclass
class PerfBudget:
    ttfb_ms: float
    dcl_ms: float
    load_ms: float

    @classmethod
    def from_env(cls) -> "PerfBudget":
        return cls(
            ttfb_ms=float(os.getenv("PERF_TTFB_MS", "1500")),
            dcl_ms=float(os.getenv("PERF_DCL_MS", "3000")),
            load_ms=float(os.getenv("PERF_LOAD_MS", "5000")),
        )

    def assert_within(self, m: PerfMetrics) -> None:
        breaches: list[str] = []
        if m.ttfb_ms > self.ttfb_ms:
            breaches.append(f"TTFB {m.ttfb_ms:.0f}ms > budget {self.ttfb_ms:.0f}ms")
        if m.dcl_ms > self.dcl_ms:
            breaches.append(f"DCL {m.dcl_ms:.0f}ms > budget {self.dcl_ms:.0f}ms")
        if m.load_ms > self.load_ms:
            breaches.append(f"Load {m.load_ms:.0f}ms > budget {self.load_ms:.0f}ms")
        assert not breaches, "Perf budget breached for " + m.url + ":\n  - " + "\n  - ".join(
            breaches
        )
