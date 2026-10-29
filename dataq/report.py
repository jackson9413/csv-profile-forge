"""Markdown and JSON export helpers."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .scoring import AXIS_HINTS, AXIS_LABELS


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def export_markdown(report: Dict[str, Any]) -> str:
    meta = report.get("meta", {})
    scores = report.get("scores", {})
    lines: List[str] = []
    lines.append("# Data quality report - %s" % meta.get("dataset_name", "dataset"))
    lines.append("")
    lines.append("- **Overall score:** %s/100 (%s)" % (
        scores.get("overall"), scores.get("verdict_label", "")))
    lines.append("- **Rows:** %s | **Columns:** %s | **Delimiter:** %s" % (
        meta.get("rows"), meta.get("columns"), meta.get("delimiter")))
    lines.append("- **Purpose:** %s" % meta.get("purpose_label"))
    lines.append("- **Generated:** %s" % meta.get("generated_at"))
    if meta.get("notes"):
        lines.append("- **Notes:** %s" % meta["notes"])
    lines.append("")
    lines.append("## Axis scores")
    lines.append("")
    for axis, label in AXIS_LABELS.items():
        value = scores.get("axes", {}).get(axis, 0)
        lines.append("- **%s:** %s/100 - %s" % (label, _fmt(value), AXIS_HINTS.get(axis, "")))
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for check in report.get("checks", []):
        mark = "PASS" if check["status"] == "pass" else "FAIL"
        lines.append("- [%s] %s" % (mark, check["label"]))
    lines.append("")
    lines.append("## Findings")
    lines.append("")
    findings = report.get("findings", [])
    if not findings:
        lines.append("No findings - the dataset passed every check.")
    for f in findings:
        lines.append("### [%s] %s" % (f["severity"].upper(), f["title"]))
        lines.append("")
        lines.append(f["detail"])
        lines.append("")
        lines.append("**Fix:** %s" % f["fix"])
        if f.get("evidence"):
            lines.append("")
            lines.append("| Row | Value |")
            lines.append("| --- | --- |")
            for item in f["evidence"]:
                lines.append("| %s | %s |" % (item.get("row"), item.get("value")))
        lines.append("")
    lines.append("## Column profiles")
    lines.append("")
    lines.append("| Column | Type | Missing | Unique | Top value | Notes |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for col in report.get("columns", []):
        top = col.get("top_values") or []
        top_val = "%s (%s)" % (top[0]["value"], top[0]["count"]) if top else ""
        notes = []
        if col.get("is_constant"):
            notes.append("constant")
        if col.get("is_id_like"):
            notes.append("id-like")
        if col.get("likely_key"):
            notes.append("key")
        if col.get("mixed_with"):
            notes.append("mixed types")
        if col.get("casing_variants"):
            notes.append("mixed casing")
        if col.get("numeric_noise"):
            notes.append("number noise")
        lines.append("| %s | %s | %s%% | %s | %s | %s |" % (
            col.get("name"), col.get("type"), col.get("missing_pct"),
            col.get("unique"), top_val, ", ".join(notes)))
    lines.append("")
    lines.append("## Cleaning plan")
    lines.append("")
    for step in report.get("cleaning_plan", []):
        auto = "auto" if step.get("auto") else "manual"
        lines.append("%d. **%s** (%s, %s) - %s" % (
            step.get("priority"), step.get("action"), step.get("target"), auto, step.get("detail")))
    lines.append("")
    return "\n".join(lines)


def export_json(report: Dict[str, Any]) -> str:
    payload = {k: v for k, v in report.items() if not k.startswith("raw_")}
    return json.dumps(payload, indent=2, sort_keys=False, default=str)
