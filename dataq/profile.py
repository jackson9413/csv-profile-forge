"""Column profiling, cross-column checks, scoring, and cleaning-plan generation."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .parser import (
    NULL_TOKENS,
    NBSP,
    SAFE_NULL_TOKENS,
    SMART_QUOTES,
    ZERO_WIDTH,
    infer_column_type,
    parse_date,
    parse_number,
    parse_table,
)
from .scoring import (
    AXIS_HINTS,
    AXIS_LABELS,
    PURPOSE_LABELS,
    PURPOSE_WEIGHTS,
    apply_axis_penalties,
    score_dataset,
)

KEY_PATTERN = re.compile(r"(^|_)(id|ids|key|uuid|guid|code|ref|sku|email|username)$")
QTY_PATTERN = re.compile(r"(^|_)(qty|quantity|units|count)$")
PRICE_PATTERN = re.compile(r"(^|_)(price|unit_price|amount|cost|rate)$")
TOTAL_PATTERN = re.compile(r"(^|_)(total|amount_total|grand_total|subtotal|line_total)$")
PCT_PATTERN = re.compile(r"(pct|percent|percentage|discount_pct|tax_rate)")
START_PATTERN = re.compile(r"(^|_)(start|begin|opened|created|from)_?(date|at|time)?$")
END_PATTERN = re.compile(r"(^|_)(end|finish|closed|to|completed)_?(date|at|time)?$")

EVIDENCE_LIMIT = 8


def snake_case(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", (name or "").strip()).strip("_")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower() or "column"


def looks_like_key(name: str) -> bool:
    return bool(KEY_PATTERN.search((name or "").strip().lower()))


def _finding(
    fid: str,
    title: str,
    severity: str,
    axis: str,
    detail: str,
    fix: str,
    evidence: Optional[List[Dict[str, Any]]] = None,
    affected: int = 0,
    affected_rows: int = 0,
) -> Dict[str, Any]:
    return {
        "id": fid,
        "title": title,
        "severity": severity,
        "axis": axis,
        "detail": detail,
        "fix": fix,
        "evidence": (evidence or [])[:EVIDENCE_LIMIT],
        "affected": affected,
        "affected_rows": affected_rows,
    }


def _evidence(row_numbers: List[int], values: List[str], limit: int = EVIDENCE_LIMIT) -> List[Dict[str, Any]]:
    out = []
    for row_no, value in list(zip(row_numbers, values))[:limit]:
        out.append({"row": row_no, "value": value})
    return out


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lower = int(k)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = k - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def _profile_column(
    name: str,
    index: int,
    raw_values: List[str],
    row_numbers: List[int],
) -> Dict[str, Any]:
    stripped = [(v or "").strip() for v in raw_values]
    missing_set = set()
    blank_rows: List[int] = []
    placeholder_rows: List[int] = []
    for i, s in enumerate(stripped):
        if s == "":
            missing_set.add(i)
            blank_rows.append(row_numbers[i])
        elif s.lower() in NULL_TOKENS:
            missing_set.add(i)
            placeholder_rows.append(row_numbers[i])
    value_idx = [i for i in range(len(stripped)) if i not in missing_set]
    values = [stripped[i] for i in value_idx]
    value_rows = [row_numbers[i] for i in value_idx]
    counter = Counter(values)
    unique = len(counter)
    type_info = infer_column_type(values)

    whitespace_rows = [
        row_numbers[i] for i, v in enumerate(raw_values)
        if (v or "") != (v or "").strip() and (v or "").strip() != ""
    ]
    double_space_rows = [
        row_numbers[i] for i in value_idx if "  " in stripped[i]
    ]
    unicode_rows = [
        row_numbers[i] for i, v in enumerate(raw_values)
        if any(ch in (v or "") for ch in (NBSP, ZERO_WIDTH, SMART_QUOTES))
    ]

    groups: Dict[str, set] = {}
    for s in counter:
        groups.setdefault(s.lower(), set()).add(s)
    casing_variants = {k: sorted(v) for k, v in groups.items() if len(v) > 1}

    numeric_values: List[float] = []
    numeric_rows: List[int] = []
    numeric_fail_rows: List[int] = []
    numeric_fail_values: List[str] = []
    numeric_noise: Dict[str, int] = {}
    for i in value_idx:
        num, noise = parse_number(stripped[i])
        if num is None:
            numeric_fail_rows.append(row_numbers[i])
            numeric_fail_values.append(stripped[i])
        else:
            numeric_values.append(float(num))
            numeric_rows.append(row_numbers[i])
            for flag in noise:
                numeric_noise[flag] = numeric_noise.get(flag, 0) + 1

    stats: Dict[str, Any] = {}
    outliers: List[Dict[str, Any]] = []
    if numeric_values:
        ordered = sorted(numeric_values)
        q1 = _percentile(ordered, 0.25)
        q3 = _percentile(ordered, 0.75)
        iqr = q3 - q1
        low_fence = q1 - 1.5 * iqr
        high_fence = q3 + 1.5 * iqr
        for i in value_idx:
            num, _ = parse_number(stripped[i])
            if num is None:
                continue
            if iqr > 0 and (float(num) < low_fence or float(num) > high_fence):
                outliers.append({"row": row_numbers[i], "value": stripped[i]})
        stats = {
            "count": len(numeric_values),
            "min": ordered[0],
            "max": ordered[-1],
            "mean": round(statistics.fmean(numeric_values), 4),
            "median": round(statistics.median(numeric_values), 4),
            "stdev": round(statistics.stdev(numeric_values), 4) if len(numeric_values) > 1 else 0.0,
            "q1": round(q1, 4),
            "q3": round(q3, 4),
            "zero_count": sum(1 for v in numeric_values if v == 0),
            "negative_count": sum(1 for v in numeric_values if v < 0),
            "outlier_count": len(outliers),
        }

    parsed_dates: List[datetime] = []
    date_fail_rows: List[int] = []
    date_fail_values: List[str] = []
    date_rows: List[int] = []
    date_values: List[str] = []
    date_rows_by_family: Dict[str, List[int]] = {}
    for i in value_idx:
        parsed, family, _amb = parse_date(stripped[i])
        if parsed is None:
            date_fail_rows.append(row_numbers[i])
            date_fail_values.append(stripped[i])
        else:
            parsed_dates.append(parsed)
            date_rows.append(row_numbers[i])
            date_values.append(stripped[i])
            date_rows_by_family.setdefault(family or "iso", []).append(row_numbers[i])
    date_stats: Dict[str, Any] = {}
    if parsed_dates:
        date_stats = {
            "count": len(parsed_dates),
            "min": min(parsed_dates).date().isoformat(),
            "max": max(parsed_dates).date().isoformat(),
            "formats": type_info.get("date_families", {}),
            "ambiguous": type_info.get("ambiguous_dates", 0),
        }

    invalid_format_rows: List[int] = []
    invalid_format_values: List[str] = []
    ftype = type_info["type"]
    if ftype in ("email", "url", "uuid", "phone"):
        from .parser import EMAIL_RE, PHONE_RE, URL_RE, UUID_RE

        checks = {
            "email": EMAIL_RE,
            "url": URL_RE,
            "uuid": UUID_RE,
            "phone": PHONE_RE,
        }
        regex = checks[ftype]
        for i in value_idx:
            if not regex.match(stripped[i]):
                invalid_format_rows.append(row_numbers[i])
                invalid_format_values.append(stripped[i])

    lengths = [len(s) for s in values]
    string_stats = {
        "min_len": min(lengths) if lengths else 0,
        "max_len": max(lengths) if lengths else 0,
        "avg_len": round(statistics.fmean(lengths), 1) if lengths else 0,
    }

    duplicate_values = {v: c for v, c in counter.items() if c > 1}
    likely_key = looks_like_key(name)

    return {
        "name": name,
        "index": index,
        "type": ftype,
        "type_confidence": type_info["confidence"],
        "type_counts": type_info["counts"],
        "mixed_with": type_info["mixed_with"],
        "rows": len(raw_values),
        "non_empty": len(values),
        "missing": len(missing_set),
        "missing_pct": round(100.0 * len(missing_set) / max(1, len(raw_values)), 1),
        "blank_count": len(blank_rows),
        "placeholder_count": len(placeholder_rows),
        "unique": unique,
        "unique_pct": round(100.0 * unique / max(1, len(values)), 1),
        "top_values": [{"value": v, "count": c} for v, c in counter.most_common(5)],
        "stats": stats,
        "date_stats": date_stats,
        "string_stats": string_stats,
        "numeric_noise": numeric_noise,
        "numeric_fail_rows": numeric_fail_rows,
        "numeric_fail_values": numeric_fail_values,
        "date_fail_rows": date_fail_rows,
        "date_fail_values": date_fail_values,
        "invalid_format_rows": invalid_format_rows,
        "invalid_format_values": invalid_format_values,
        "whitespace_rows": whitespace_rows,
        "double_space_rows": double_space_rows,
        "unicode_rows": unicode_rows,
        "casing_variants": casing_variants,
        "duplicate_values": duplicate_values,
        "outliers": outliers,
        "likely_key": likely_key,
        "is_constant": unique == 1 and len(values) > 0,
        "is_empty": len(values) == 0,
        "is_id_like": unique == len(values) and len(values) >= 5 and ftype in ("text", "uuid", "integer"),
        "_values": values,
        "_value_rows": value_rows,
        "_value_by_row": {row_numbers[i]: stripped[i] for i in value_idx},
        "_date_rows_by_family": date_rows_by_family,
    }


def _column_findings(col: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = col["name"]
    findings: List[Dict[str, Any]] = []

    if col["is_empty"]:
        findings.append(_finding(
            "empty_column", "Column %r has no values at all" % name, "high", "completeness",
            "Every one of the %d cells in this column is blank." % col["rows"],
            "Drop the column, or pull the values in from the source export.",
            affected=col["rows"], affected_rows=col["rows"],
        ))
    elif col["missing"]:
        sev = "high" if col["missing_pct"] >= 30 else ("medium" if col["missing_pct"] >= 10 else "low")
        detail = "%d of %d cells are blank or a placeholder (%s%%)." % (
            col["missing"], col["rows"], col["missing_pct"])
        if col["placeholder_count"]:
            detail += " %d use a null-like token (N/A, none, -, null) rather than a real blank." % col["placeholder_count"]
        findings.append(_finding(
            "missing_values", "Missing values in %r" % name, sev, "completeness", detail,
            "Decide per column: drop the row, impute a default, or quarantine the record. "
            "Replace placeholder tokens with real blanks so counts stop lying.",
            affected=col["missing"], affected_rows=col["missing"],
        ))

    if col["whitespace_rows"]:
        findings.append(_finding(
            "whitespace_padding", "Leading/trailing spaces in %r" % name, "low", "consistency",
            "%d cells have padding around the value, so \"Austin\" and \"Austin \" group as two "
            "different categories." % len(col["whitespace_rows"]),
            "Trim whitespace on every cell before grouping or joining.",
            affected=len(col["whitespace_rows"]), affected_rows=len(col["whitespace_rows"]),
        ))

    if col["casing_variants"]:
        sample = list(col["casing_variants"].items())[:4]
        detail = "; ".join(
            "%s" % " vs ".join(variants) for _key, variants in sample
        )
        findings.append(_finding(
            "inconsistent_casing", "Mixed casing in %r" % name, "medium", "consistency",
            "The same value appears in different cases (%s)." % detail,
            "Pick one casing convention (usually lowercase or title case) and normalise the column.",
            affected=sum(len(v) for v in col["casing_variants"].values()),
            affected_rows=len(col["casing_variants"]),
        ))

    if col["unicode_rows"]:
        findings.append(_finding(
            "unicode_noise", "Invisible characters in %r" % name, "low", "consistency",
            "%d cells contain non-breaking spaces, zero-width characters or smart quotes that "
            "break exact matching." % len(col["unicode_rows"]),
            "Strip zero-width characters and replace non-breaking spaces with plain spaces.",
            affected=len(col["unicode_rows"]), affected_rows=len(col["unicode_rows"]),
        ))

    if len(col["date_stats"].get("formats") or {}) > 1:
        formats = ", ".join(
            "%s (%d)" % (k, v) for k, v in sorted(col["date_stats"]["formats"].items())
        )
        by_family = col.get("_date_rows_by_family") or {}
        dominant_family = max(
            (col["date_stats"]["formats"] or {}).items(), key=lambda kv: kv[1]
        )[0] if col["date_stats"].get("formats") else ""
        odd_rows: List[int] = []
        for family, rows in by_family.items():
            if family != dominant_family:
                odd_rows.extend(rows)
        odd_rows = sorted(set(odd_rows))
        value_by_row = col.get("_value_by_row") or {}
        odd_values = [value_by_row.get(r, "") for r in odd_rows]
        findings.append(_finding(
            "mixed_date_formats", "Mixed date formats in %r" % name, "medium", "consistency",
            "Dates in one column use several formats: %s. %d value(s) do not match the dominant "
            "%s format." % (formats, len(odd_rows), dominant_family),
            "Parse every date and rewrite the column as ISO YYYY-MM-DD.",
            evidence=_evidence(odd_rows, odd_values),
            affected=len(odd_rows),
            affected_rows=len(odd_rows),
        ))

    if col["date_stats"].get("ambiguous"):
        findings.append(_finding(
            "ambiguous_dates", "Ambiguous day/month order in %r" % name, "medium", "validity",
            "%d dates could be read as either DD/MM or MM/DD, so two systems can sort them "
            "differently." % col["date_stats"]["ambiguous"],
            "Confirm the source locale and rewrite the column as ISO YYYY-MM-DD.",
            affected=col["date_stats"]["ambiguous"], affected_rows=col["date_stats"]["ambiguous"],
        ))

    if col["date_fail_rows"] and col["type"] in ("date", "mixed") and len(col["date_fail_values"]) <= len(col["_values"]):
        findings.append(_finding(
            "invalid_dates", "Unparseable dates in %r" % name, "medium", "validity",
            "%d values look like dates but do not parse." % len(col["date_fail_rows"]),
            "Fix the broken values at source, then restandardise the whole column to ISO dates.",
            evidence=_evidence(col["date_fail_rows"], col["date_fail_values"]),
            affected=len(col["date_fail_rows"]), affected_rows=len(col["date_fail_rows"]),
        ))

    if col["numeric_noise"]:
        noise_desc = ", ".join(
            "%s (%d)" % (k.replace("_", " "), v) for k, v in sorted(col["numeric_noise"].items())
        )
        findings.append(_finding(
            "numeric_noise", "Number formatting noise in %r" % name, "medium", "validity",
            "Values carry formatting that has to be stripped before they become numbers: %s."
            % noise_desc,
            "Strip the symbols in the import step and store the column as a real number.",
            affected=sum(col["numeric_noise"].values()),
            affected_rows=sum(col["numeric_noise"].values()),
        ))

    if col["numeric_fail_rows"] and col["type"] in ("integer", "number", "currency", "percent"):
        findings.append(_finding(
            "invalid_numbers", "Values that will not parse as numbers in %r" % name, "high", "validity",
            "%d values look numeric but cannot be parsed (European decimal commas, stray letters, "
            "or empty strings)." % len(col["numeric_fail_rows"]),
            "Clean the offending values at source and re-export; do not coerce them to zero silently.",
            evidence=_evidence(col["numeric_fail_rows"], col["numeric_fail_values"]),
            affected=len(col["numeric_fail_rows"]), affected_rows=len(col["numeric_fail_rows"]),
        ))

    if col["invalid_format_rows"]:
        findings.append(_finding(
            "invalid_format", "Invalid %s values in %r" % (col["type"], name), "medium", "validity",
            "%d of %d values are not valid %s values." % (
                len(col["invalid_format_rows"]), max(1, col["non_empty"]), col["type"]),
            "Validate at capture time and quarantine the bad rows instead of importing them.",
            evidence=_evidence(col["invalid_format_rows"], col["invalid_format_values"]),
            affected=len(col["invalid_format_rows"]), affected_rows=len(col["invalid_format_rows"]),
        ))

    if col["mixed_with"]:
        findings.append(_finding(
            "mixed_types", "Mixed value types in %r" % name, "high", "consistency",
            "The column mixes %s values, so downstream parsing will be guesswork." % ", ".join(col["mixed_with"]),
            "Split the column into typed columns, or coerce every value with one explicit rule.",
            affected=col["non_empty"], affected_rows=col["non_empty"],
        ))

    if col["outliers"]:
        findings.append(_finding(
            "outliers", "Outlier values in %r" % name, "low", "validity",
            "%d values sit outside the 1.5xIQR fence (min %s, max %s)." % (
                len(col["outliers"]), col["stats"].get("min"), col["stats"].get("max")),
            "Confirm whether these are genuine extreme values or data-entry errors before "
            "winsorising, capping or dropping them.",
            evidence=col["outliers"],
            affected=len(col["outliers"]), affected_rows=len(col["outliers"]),
        ))

    if col["is_constant"]:
        findings.append(_finding(
            "constant_column", "Constant column %r" % name, "low", "structure",
            "Every row holds the same value, so the column carries no information.",
            "Drop it from the model, or keep it only as documentation of the source.",
            affected=col["rows"], affected_rows=0,
        ))

    return findings


def _table_findings(
    headers: List[str],
    rows: List[List[str]],
    row_numbers: List[int],
    columns: List[Dict[str, Any]],
    context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    header_counts = Counter(h.strip().lower() for h in headers)
    dupes = {h: c for h, c in header_counts.items() if c > 1}
    if dupes:
        findings.append(_finding(
            "duplicate_headers", "Duplicate column names", "high", "structure",
            "These header names appear more than once: %s." % ", ".join(
                "%s (%d)" % (h, c) for h, c in dupes.items()),
            "Rename the duplicates (suffix _2, _3) before importing; most tools silently keep only "
            "one of them.",
            affected=sum(dupes.values()), affected_rows=0,
        ))
    blank_headers = [h for h in headers if not h.strip()]
    if blank_headers:
        findings.append(_finding(
            "blank_headers", "Unnamed columns", "high", "structure",
            "%d column(s) have an empty header." % len(blank_headers),
            "Name every column; unnamed columns become column1, column2 and lose meaning.",
            affected=len(blank_headers), affected_rows=0,
        ))
    padded_headers = [h for h in headers if h != h.strip()]
    if padded_headers:
        findings.append(_finding(
            "header_whitespace", "Whitespace in column names", "low", "structure",
            "%d header(s) have leading or trailing spaces." % len(padded_headers),
            "Trim header names so lookups by column name work.",
            affected=len(padded_headers), affected_rows=0,
        ))
    renamed = {}
    for h in headers:
        if h.strip() and snake_case(h) != h.strip():
            renamed[h] = snake_case(h)
    if renamed:
        findings.append(_finding(
            "header_style", "Inconsistent column naming", "low", "structure",
            "%d header(s) are not snake_case, which breaks most downstream tooling." % len(renamed),
            "Rename to snake_case: " + ", ".join(
                "%s -> %s" % (k, v) for k, v in list(renamed.items())[:6]),
            affected=len(renamed), affected_rows=0,
        ))

    expected_width = len(headers)
    ragged = [
        (row_numbers[i], len(row))
        for i, row in enumerate(rows)
        if len(row) != expected_width
    ]
    if ragged:
        findings.append(_finding(
            "ragged_rows", "Rows with the wrong number of fields", "high", "structure",
            "%d of %d rows do not have %d fields (found %s)." % (
                len(ragged), len(rows), expected_width,
                ", ".join(str(w) for _r, w in ragged[:5])),
            "Fix the export/serialisation step, or pad/truncate the ragged rows explicitly "
            "before import.",
            evidence=[{"row": r, "value": "%d fields (expected %d)" % (w, expected_width)}
                      for r, w in ragged],
            affected=len(ragged), affected_rows=len(ragged),
        ))

    seen: Dict[Tuple[str, ...], List[int]] = {}
    for i, row in enumerate(rows):
        key = tuple((c or "").strip() for c in row)
        seen.setdefault(key, []).append(row_numbers[i])
    dup_rows = {k: v for k, v in seen.items() if len(v) > 1}
    if dup_rows:
        extra = sum(len(v) - 1 for v in dup_rows.values())
        first = list(dup_rows.items())[:3]
        findings.append(_finding(
            "duplicate_rows", "Exact duplicate rows", "medium", "uniqueness",
            "%d row(s) are exact duplicates of an earlier row (%d redundant records)." % (
                len(dup_rows), extra),
            "Deduplicate on load, and find the upstream query or export that is emitting the "
            "duplicate rows.",
            evidence=[{"row": v[0], "value": " | ".join(k[:6]) + (" | ..." if len(k) > 6 else "")}
                      for k, v in first],
            affected=extra, affected_rows=extra,
        ))

    key_names = [k.strip() for k in (context.get("key_columns") or "").split(",") if k.strip()]
    key_cols = [c for c in columns if c["name"] in key_names]
    if not key_cols:
        key_cols = [c for c in columns if c["likely_key"]]
    for col in key_cols:
        dupes_in_key = {v: c for v, c in col["duplicate_values"].items()}
        if dupes_in_key:
            extra = sum(c - 1 for c in dupes_in_key.values())
            findings.append(_finding(
                "duplicate_keys", "Duplicate values in key column %r" % col["name"], "high", "uniqueness",
                "%d value(s) appear more than once, so %r is not a unique identifier (%d redundant "
                "records)." % (len(dupes_in_key), col["name"], extra),
                "Build a composite key, or fix the source so the identifier is genuinely unique.",
                evidence=[{"row": r, "value": v} for r, v in list(col["duplicate_values"].items())[:6]],
                affected=extra, affected_rows=extra,
            ))
        if col["missing"]:
            findings.append(_finding(
                "missing_key", "Key column %r has missing values" % col["name"], "high", "completeness",
                "%d rows are missing a value in the key column." % col["missing"],
                "Keys must be present on every row; fill them or drop those rows.",
                affected=col["missing"], affected_rows=col["missing"],
            ))

    findings.extend(_semantic_findings(columns, row_numbers))

    expected_rows = context.get("expected_rows")
    if expected_rows not in (None, "", 0):
        try:
            expected_n = int(expected_rows)
            if expected_n != len(rows):
                findings.append(_finding(
                    "row_count_mismatch", "Row count differs from expectation", "medium", "structure",
                    "Expected %d rows, found %d (%+d)." % (expected_n, len(rows), len(rows) - expected_n),
                    "Check whether the export was truncated or filtered upstream.",
                    affected=abs(len(rows) - expected_n), affected_rows=0,
                ))
        except (TypeError, ValueError):
            pass

    return findings


def _semantic_findings(columns: List[Dict[str, Any]], row_numbers: List[int]) -> List[Dict[str, Any]]:
    """Cross-field business rules that work on any dataset."""
    findings: List[Dict[str, Any]] = []
    by_name = {c["name"]: c for c in columns}

    def find_col(pattern):
        for c in columns:
            if pattern.search(c["name"].strip().lower()):
                return c
        return None

    qty = find_col(QTY_PATTERN)
    price = find_col(PRICE_PATTERN)
    total = find_col(TOTAL_PATTERN)
    if qty and price and total:
        mismatches = []
        rows = row_numbers
        qty_map = dict(zip(qty["_value_rows"], qty["_values"]))
        price_map = dict(zip(price["_value_rows"], price["_values"]))
        total_map = dict(zip(total["_value_rows"], total["_values"]))
        for row_no in rows:
            if row_no not in qty_map or row_no not in price_map or row_no not in total_map:
                continue
            q, _ = parse_number(qty_map[row_no])
            p, _ = parse_number(price_map[row_no])
            t, _ = parse_number(total_map[row_no])
            if q is None or p is None or t is None:
                continue
            expected_total = q * p
            tolerance = max(abs(expected_total) * 0.01, 0.01)
            if abs(expected_total - t) > tolerance:
                mismatches.append({
                    "row": row_no,
                    "value": "qty=%s price=%s expected=%s but total=%s" % (
                        qty_map[row_no], price_map[row_no], round(expected_total, 2), total_map[row_no]),
                })
        if mismatches:
            findings.append(_finding(
                "formula_mismatch", "Totals do not match quantity x price", "high", "validity",
                "%d row(s) have a total that does not equal quantity x price." % len(mismatches),
                "Recompute the total column in the source system; a spreadsheet edit will drift again.",
                evidence=mismatches,
                affected=len(mismatches), affected_rows=len(mismatches),
            ))

    start_col = find_col(START_PATTERN)
    end_col = find_col(END_PATTERN)
    if start_col and end_col and start_col["name"] != end_col["name"]:
        start_map = dict(zip(start_col["_value_rows"], start_col["_values"]))
        end_map = dict(zip(end_col["_value_rows"], end_col["_values"]))
        violations = []
        for row_no in row_numbers:
            if row_no not in start_map or row_no not in end_map:
                continue
            s, _f, _a = parse_date(start_map[row_no])
            e, _f2, _a2 = parse_date(end_map[row_no])
            if s and e and e < s:
                violations.append({
                    "row": row_no,
                    "value": "%s -> %s" % (start_map[row_no], end_map[row_no]),
                })
        if violations:
            findings.append(_finding(
                "date_order_violation", "End date before start date", "high", "validity",
                "%d row(s) end before they start." % len(violations),
                "Correct the swapped or mistyped dates at source.",
                evidence=violations,
                affected=len(violations), affected_rows=len(violations),
            ))

    for col in columns:
        cname = col["name"].strip().lower()
        if PCT_PATTERN.search(cname) or col["type"] == "percent":
            out_of_range = []
            for row_no, value in zip(col["_value_rows"], col["_values"]):
                num, _ = parse_number(value)
                if num is None:
                    continue
                if num < 0 or num > 100:
                    out_of_range.append({"row": row_no, "value": value})
            if out_of_range:
                findings.append(_finding(
                    "percent_out_of_range", "Percentage outside 0-100 in %r" % col["name"], "medium",
                    "validity",
                    "%d percentage value(s) fall outside 0-100." % len(out_of_range),
                    "Decide whether the column is a fraction (0-1) or a percent (0-100) and scale "
                    "it consistently.",
                    evidence=out_of_range,
                    affected=len(out_of_range), affected_rows=len(out_of_range),
                ))

        if QTY_PATTERN.search(cname) or (PRICE_PATTERN.search(cname) and "total" not in cname):
            negatives = []
            for row_no, value in zip(col["_value_rows"], col["_values"]):
                num, _ = parse_number(value)
                if num is not None and num < 0:
                    negatives.append({"row": row_no, "value": value})
            if negatives:
                findings.append(_finding(
                    "negative_values", "Negative values in %r" % col["name"], "medium", "validity",
                    "%d value(s) are negative in a quantity/amount column." % len(negatives),
                    "If negatives are legitimate (refunds, returns) model them explicitly instead "
                    "of letting them distort totals.",
                    evidence=negatives,
                    affected=len(negatives), affected_rows=len(negatives),
                ))

        symbols = set()
        for value in col["_values"]:
            stripped_v = value.strip()
            if stripped_v and stripped_v[0] in "$EUR£¥₹₽₩¢₺₴฿₫₪":
                symbols.add(stripped_v[0])
            elif stripped_v and stripped_v[-1] in "$EUR£¥₹₽₩¢₺₴฿₫₪":
                symbols.add(stripped_v[-1])
        if len(symbols) > 1:
            findings.append(_finding(
                "mixed_currency", "Mixed currency symbols in %r" % col["name"], "medium", "validity",
                "The column mixes currency symbols (%s), so the amounts are not comparable."
                % ", ".join(sorted(symbols)),
                "Add a currency column and store amounts as plain numbers.",
                affected=col["non_empty"], affected_rows=col["non_empty"],
            ))

    return findings


def _cleaning_plan(
    columns: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    headers: List[str],
) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    seen: set = set()

    def add(action: str, target: str, affected: int, detail: str, auto: bool) -> None:
        key = (action, target)
        if key in seen:
            return
        seen.add(key)
        plan.append({
            "action": action,
            "target": target,
            "affected": affected,
            "detail": detail,
            "auto": auto,
        })

    for col in columns:
        name = col["name"]
        if col["whitespace_rows"]:
            add("Trim whitespace", name, len(col["whitespace_rows"]),
                "Strip leading/trailing spaces from %d cells." % len(col["whitespace_rows"]), True)
        if col["unicode_rows"]:
            add("Strip invisible characters", name, len(col["unicode_rows"]),
                "Remove zero-width characters and normalise non-breaking spaces in %d cells."
                % len(col["unicode_rows"]), True)
        if col["placeholder_count"] and not col["is_empty"]:
            add("Replace placeholder tokens with blanks", name, col["placeholder_count"],
                "Turn %d null-like tokens (N/A, none, -, null) into real blanks."
                % col["placeholder_count"], col["type"] != "text")
        if col["casing_variants"]:
            variants = list(col["casing_variants"].values())[:3]
            add("Normalise casing", name, len(col["casing_variants"]),
                "Collapse case variants (%s) to one convention."
                % "; ".join(" vs ".join(v) for v in variants), False)
        if col["numeric_noise"]:
            add("Strip number formatting", name, sum(col["numeric_noise"].values()),
                "Remove currency symbols, thousands separators or percent signs so the column "
                "parses as a number.", False)
        if col["date_stats"].get("formats") and len(col["date_stats"]["formats"]) > 1:
            add("Standardise dates to ISO", name, col["date_stats"].get("count", 0),
                "Rewrite every date as YYYY-MM-DD.", False)
        if col["missing"]:
            add("Resolve missing values", name, col["missing"],
                "%d cells need a decision: drop the row, impute, or quarantine." % col["missing"], False)
        if col["is_constant"]:
            add("Drop constant column", name, col["rows"],
                "The column never changes value, so it adds nothing to the dataset.", False)
        if col["name"].strip() and snake_case(col["name"]) != col["name"].strip():
            add("Rename column to snake_case", name, 1,
                "Rename %r to %r." % (col["name"], snake_case(col["name"])), False)

    dup = next((f for f in findings if f["id"] == "duplicate_rows"), None)
    if dup:
        add("Remove duplicate rows", "table", dup["affected"],
            "Drop %d redundant row(s) that exactly repeat an earlier record." % dup["affected"], True)
    ragged = next((f for f in findings if f["id"] == "ragged_rows"), None)
    if ragged:
        add("Fix ragged rows", "table", ragged["affected_rows"],
            "%d row(s) have the wrong field count; pad or split them at the source." % ragged["affected_rows"], False)
    mismatch = next((f for f in findings if f["id"] == "formula_mismatch"), None)
    if mismatch:
        add("Recompute derived totals", "table", mismatch["affected"],
            "%d row(s) disagree with quantity x price." % mismatch["affected"], False)

    order = {"Remove duplicate rows": 0, "Trim whitespace": 1, "Strip invisible characters": 1,
             "Replace placeholder tokens with blanks": 2}
    plan.sort(key=lambda p: (order.get(p["action"], 5), -p["affected"]))
    for i, item in enumerate(plan, 1):
        item["priority"] = i
    return plan


def _checks(findings: List[Dict[str, Any]], columns: List[Dict[str, Any]],
            rows: List[int]) -> List[Dict[str, Any]]:
    """Pass/fail checklist derived from the findings."""
    def has(fid: str) -> bool:
        return any(f["id"] == fid for f in findings)

    spec = [
        ("headers_unique", "Column names are unique", ["duplicate_headers"]),
        ("headers_named", "Every column is named", ["blank_headers"]),
        ("row_widths_consistent", "All rows have the same field count", ["ragged_rows"]),
        ("no_duplicate_rows", "No exact duplicate rows", ["duplicate_rows"]),
        ("keys_unique", "Key values are unique", ["duplicate_keys"]),
        ("no_missing_values", "No missing values", ["missing_values"]),
        ("types_consistent", "One type per column", ["mixed_types"]),
        ("dates_parse", "All dates parse in one format", ["invalid_dates", "mixed_date_formats"]),
        ("numbers_parse", "All numbers parse cleanly", ["invalid_numbers", "numeric_noise"]),
        ("ranges_sane", "Values are in a sane range", ["percent_out_of_range", "negative_values"]),
        ("totals_reconcile", "Totals reconcile with their inputs", ["formula_mismatch"]),
        ("text_clean", "No invisible characters", ["unicode_noise"]),
    ]
    checks = []
    for cid, label, problems in spec:
        failed = [p for p in problems if has(p)]
        status = "pass" if not failed else "fail"
        detail = "No issues detected." if not failed else "See findings below."
        checks.append({
            "id": cid,
            "label": label,
            "status": status,
            "detail": detail,
            "finding": problems[0],
        })
    return checks


def profile_dataset(text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Profile a delimited dataset and return a full quality report."""
    context = dict(context or {})
    purpose = context.get("purpose") or "general"
    if purpose not in PURPOSE_WEIGHTS:
        purpose = "general"
    dataset_name = (context.get("dataset_name") or "untitled-dataset").strip() or "untitled-dataset"
    delimiter = context.get("delimiter")
    if delimiter in ("auto", "", None):
        delimiter = None
    has_header = context.get("has_header", True)
    has_header = str(has_header).lower() not in ("false", "0", "no")
    notes = (context.get("notes") or "").strip()

    if not text or not text.strip():
        raise ValueError("Paste some CSV or TSV data to profile.")

    table = parse_table(text, delimiter, has_header)
    headers = table["headers"]
    rows = table["rows"]
    if not headers:
        raise ValueError("No rows detected. Check that the paste contains data.")
    if not rows:
        raise ValueError("Only a header row was found. Add at least one data row.")

    row_numbers = [i + 2 for i in range(len(rows))]

    columns = []
    for idx, name in enumerate(headers):
        raw_values = [row[idx] if idx < len(row) else "" for row in rows]
        columns.append(_profile_column(name, idx, raw_values, row_numbers))

    findings: List[Dict[str, Any]] = []
    for col in columns:
        findings.extend(_column_findings(col))
    findings.extend(_table_findings(headers, rows, row_numbers, columns, context))

    total_cells = sum(len(row) for row in rows) or 1
    missing_cells = sum(c["missing"] for c in columns)
    overall_missing = missing_cells / float(total_cells)
    key_cols = [c for c in columns if c["name"] in
                [k.strip() for k in (context.get("key_columns") or "").split(",") if k.strip()]]
    key_missing = max([c["missing_pct"] / 100.0 for c in key_cols], default=0.0)

    base_axes = {
        "completeness": 100.0 - 70.0 * overall_missing - 30.0 * key_missing,
        "consistency": 100.0,
        "validity": 100.0,
        "uniqueness": 100.0,
        "structure": 100.0,
    }

    dup_rows = next((f for f in findings if f["id"] == "duplicate_rows"), None)
    dup_rate = (dup_rows["affected"] / float(len(rows))) if dup_rows else 0.0
    ragged = next((f for f in findings if f["id"] == "ragged_rows"), None)
    ragged_rate = (ragged["affected"] / float(len(rows))) if ragged else 0.0

    casing_cols = [c for c in columns if c["casing_variants"]]
    unicode_cols = [c for c in columns if c["unicode_rows"]]
    ws_cols = [c for c in columns if c["whitespace_rows"]]
    placeholder_cols = [c for c in columns if c["placeholder_count"]]
    mixed_cols = [c for c in columns if c["mixed_with"]]

    base_axes["consistency"] -= min(30.0, 10.0 * len(casing_cols))
    base_axes["consistency"] -= min(20.0, 10.0 * sum(
        1 for c in columns
        if len(c["date_stats"].get("formats") or {}) > 1))
    base_axes["consistency"] -= min(10.0, 5.0 * len(unicode_cols))
    base_axes["consistency"] -= min(12.0, 4.0 * len(ws_cols))
    base_axes["consistency"] -= min(12.0, 4.0 * len(placeholder_cols))
    base_axes["consistency"] -= min(24.0, 12.0 * len(mixed_cols))
    base_axes["consistency"] -= min(25.0, 20.0 * dup_rate)
    base_axes["consistency"] -= min(35.0, 30.0 * ragged_rate + (8.0 if ragged else 0.0))

    invalid_rates = []
    for col in columns:
        if col["invalid_format_rows"]:
            invalid_rates.append(len(col["invalid_format_rows"]) / float(max(1, col["non_empty"])))
        if col["numeric_fail_rows"] and col["type"] in ("integer", "number", "currency", "percent"):
            invalid_rates.append(len(col["numeric_fail_rows"]) / float(max(1, col["non_empty"])))
    if invalid_rates:
        base_axes["validity"] -= min(50.0, 40.0 * max(invalid_rates))
    if any(f["id"] == "percent_out_of_range" for f in findings):
        base_axes["validity"] -= 15.0
    if any(f["id"] == "negative_values" for f in findings):
        base_axes["validity"] -= 12.0
    if any(f["id"] == "formula_mismatch" for f in findings):
        base_axes["validity"] -= 18.0
    if any(f["id"] == "date_order_violation" for f in findings):
        base_axes["validity"] -= 15.0
    if any(f["id"] == "mixed_currency" for f in findings):
        base_axes["validity"] -= 8.0

    base_axes["uniqueness"] -= min(50.0, 50.0 * dup_rate)
    dup_key = next((f for f in findings if f["id"] == "duplicate_keys"), None)
    if dup_key:
        base_axes["uniqueness"] -= min(40.0, 40.0 * (dup_key["affected"] / float(max(1, len(rows)))))
    if any(f["id"] == "missing_key" for f in findings):
        base_axes["uniqueness"] -= 10.0
    base_axes["uniqueness"] -= min(9.0, 3.0 * sum(1 for c in columns if c["is_constant"]))

    base_axes["structure"] -= min(40.0, 25.0 * sum(1 for f in findings if f["id"] == "duplicate_headers"))
    base_axes["structure"] -= min(30.0, 20.0 * sum(1 for f in findings if f["id"] == "blank_headers"))
    base_axes["structure"] -= min(35.0, 30.0 * ragged_rate + (10.0 if ragged else 0.0))
    base_axes["structure"] -= min(12.0, 3.0 * sum(1 for f in findings if f["id"] == "header_style"))
    base_axes["structure"] -= min(9.0, 3.0 * sum(1 for f in findings if f["id"] == "header_whitespace"))
    base_axes["structure"] -= min(16.0, 8.0 * sum(1 for c in columns if c["is_empty"]))
    if any(f["id"] == "row_count_mismatch" for f in findings):
        base_axes["structure"] -= 10.0

    axes = apply_axis_penalties(base_axes, findings)
    scores = score_dataset(axes, findings, purpose)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order.get(f["severity"], 3), f["id"]))

    preview_rows = []
    for row in rows[:8]:
        preview_rows.append([(c or "")[:40] for c in row])

    report = {
        "meta": {
            "dataset_name": dataset_name,
            "purpose": purpose,
            "purpose_label": PURPOSE_LABELS.get(purpose, purpose),
            "rows": len(rows),
            "columns": len(headers),
            "delimiter": table["delimiter_label"],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "notes": notes,
            "key_columns": [k.strip() for k in (context.get("key_columns") or "").split(",") if k.strip()],
        },
        "scores": scores,
        "axis_labels": AXIS_LABELS,
        "axis_hints": AXIS_HINTS,
        "columns": [
            {k: v for k, v in col.items() if not k.startswith("_")}
            for col in columns
        ],
        "findings": findings,
        "checks": _checks(findings, columns, rows),
        "cleaning_plan": _cleaning_plan(columns, findings, headers),
        "preview": {"headers": headers, "rows": preview_rows},
    }
    report["raw_rows"] = rows
    report["raw_headers"] = headers
    report["raw_delimiter"] = table["delimiter"]
    return report
