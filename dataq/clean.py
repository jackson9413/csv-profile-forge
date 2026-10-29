"""Conservative auto-cleaning of a profiled dataset.

Only reversible, low-risk fixes are applied automatically:
  * byte-order mark and invisible characters removed
  * leading/trailing whitespace trimmed
  * obvious null tokens converted to real blanks in typed columns
  * exact duplicate rows dropped

Everything else stays in the cleaning plan for a human decision.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List

from .parser import SAFE_NULL_TOKENS

ZERO_WIDTHS = ("\ufeff", "\u200b", "\u200c", "\u200d")
NBSP = "\u00a0"


def _scrub(value: str) -> str:
    out = value or ""
    for ch in ZERO_WIDTHS:
        out = out.replace(ch, "")
    out = out.replace(NBSP, " ")
    return out.strip()


def build_cleaned_csv(report: Dict[str, Any]) -> str:
    """Return a cleaned CSV string built from the profiled report."""
    headers: List[str] = report.get("raw_headers") or []
    rows: List[List[str]] = report.get("raw_rows") or []
    types = [c.get("type", "text") for c in report.get("columns", [])]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([(h or "").strip() for h in headers])
    seen = set()
    for row in rows:
        cleaned = []
        for i, cell in enumerate(row):
            value = _scrub(cell)
            if i < len(types) and types[i] != "text" and value.lower() in SAFE_NULL_TOKENS:
                value = ""
            cleaned.append(value)
        key = tuple(cleaned)
        if key in seen:
            continue
        seen.add(key)
        writer.writerow(cleaned)
    return buffer.getvalue()


def cleaning_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    plan = report.get("cleaning_plan") or []
    return {
        "auto_steps": sum(1 for p in plan if p.get("auto")),
        "manual_steps": sum(1 for p in plan if not p.get("auto")),
        "total_steps": len(plan),
    }
