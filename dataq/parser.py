"""Delimited-text parsing and value type inference.

Pure standard library: no pandas, no network, no API keys.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DELIMITERS = (",", "\t", ";", "|")
DELIMITER_LABELS = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}

# Tokens that people use to mean "no value". SAFE_NULL_TOKENS are converted to
# real blanks by the cleaned-CSV export; "none"/"unknown" are kept because they
# can be legitimate content.
NULL_TOKENS = {
    "", "na", "n/a", "null", "none", "nan", "nil", "-", "--", "?",
    "n.a.", "(blank)", "undefined", "missing", "tbd",
}
SAFE_NULL_TOKENS = {"", "na", "n/a", "null", "nan", "nil", "-", "--", "n.a.", "(blank)"}

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")
URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
PHONE_RE = re.compile(r"^\+?[0-9\s().-]{7,20}$")
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
PERCENT_RE = re.compile(r"^[+-]?[\d,]*\.?\d+\s*%$")
UNIT_TAIL_RE = re.compile(r"^([+-]?\d*\.?\d+)([A-Za-z°%].*)$")

CURRENCY_CHARS = "$EUR£¥₹₽₩¢₺₴฿₫₪"
NBSP = "\u00a0"
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
SMART_QUOTES = "\u2018\u2019\u201c\u201d"

DATE_FORMATS = [
    ("%Y-%m-%d", "iso"),
    ("%Y-%m-%dT%H:%M:%S", "iso"),
    ("%Y-%m-%d %H:%M:%S", "iso"),
    ("%Y-%m-%d %H:%M", "iso"),
    ("%Y/%m/%d", "ymd_slash"),
    ("%d/%m/%Y", "slash"),
    ("%m/%d/%Y", "slash"),
    ("%d-%m-%Y", "dash"),
    ("%m-%d-%Y", "dash"),
    ("%d.%m.%Y", "dot"),
    ("%d/%m/%Y %H:%M", "slash"),
    ("%m/%d/%Y %H:%M", "slash"),
    ("%b %d, %Y", "month_name"),
    ("%d %b %Y", "month_name"),
    ("%d-%b-%Y", "month_name"),
    ("%Y%m%d", "compact"),
]


def detect_delimiter(text: str) -> str:
    """Pick the delimiter that splits the most lines the most consistently."""
    sample_lines = [ln for ln in text.split("\n")[:25] if ln.strip()]
    if not sample_lines:
        return ","
    best_delim = ","
    best_score = (-1.0, 0)
    for delim in DELIMITERS:
        counts = []
        for line in sample_lines:
            try:
                row = next(csv.reader([line], delimiter=delim))
            except Exception:
                row = line.split(delim)
            counts.append(max(0, len(row) - 1))
        if not counts:
            continue
        avg = sum(counts) / len(counts)
        if avg <= 0:
            continue
        modal = Counter(counts).most_common(1)[0]
        consistency = modal[1] / len(counts)
        score = (round(avg, 3), round(consistency, 3))
        if score > best_score:
            best_score = score
            best_delim = delim
    return best_delim


def parse_table(
    text: str,
    delimiter: Optional[str] = None,
    has_header: bool = True,
) -> Dict[str, Any]:
    """Parse delimited text into headers + rows, reporting row-width drift."""
    clean = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    delim = delimiter if delimiter in DELIMITERS else detect_delimiter(clean)
    records: List[List[str]] = []
    if clean.strip():
        reader = csv.reader(io.StringIO(clean), delimiter=delim)
        for row in reader:
            records.append(list(row))
    while records and all((c or "").strip() == "" for c in records[-1]):
        records.pop()
    if not records:
        return {
            "headers": [],
            "rows": [],
            "delimiter": delim,
            "delimiter_label": DELIMITER_LABELS.get(delim, delim),
            "widths": {},
            "ragged_rows": 0,
            "n_rows": 0,
        }
    if has_header:
        headers = [str(h) for h in records[0]]
        data = records[1:]
    else:
        headers = ["column_%d" % (i + 1) for i in range(len(records[0]))]
        data = records
    widths = Counter(len(r) for r in data)
    expected = len(headers)
    ragged = [r for r in data if len(r) != expected]
    return {
        "headers": headers,
        "rows": data,
        "delimiter": delim,
        "delimiter_label": DELIMITER_LABELS.get(delim, delim),
        "widths": dict(widths),
        "ragged_rows": len(ragged),
        "n_rows": len(data),
    }


def parse_number(value: Any) -> Tuple[Optional[float], set]:
    """Parse a number, reporting the formatting noise that had to be removed."""
    noise: set = set()
    if value is None:
        return None, noise
    s = str(value).strip()
    if not s:
        return None, noise
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
        noise.add("parentheses_negative")
    if s and s[0] in CURRENCY_CHARS:
        noise.add("currency_symbol")
        s = s[1:].strip()
    if s and s[-1] in CURRENCY_CHARS:
        noise.add("currency_symbol")
        s = s[:-1].strip()
    if s.endswith("%") or PERCENT_RE.match(s):
        noise.add("percent_suffix")
        s = s.rstrip("%").strip()
    if "," in s:
        if GROUPED_RE.match(s):
            noise.add("thousands_separator")
            s = s.replace(",", "")
        else:
            return None, noise
    s = s.replace(" ", "")
    m = UNIT_TAIL_RE.match(s)
    if m:
        noise.add("trailing_unit")
        s = m.group(1)
    if INT_RE.match(s):
        try:
            num = int(s)
        except ValueError:
            return None, noise
        return (-num if negative else num), noise
    if FLOAT_RE.match(s):
        try:
            num = float(s)
        except ValueError:
            return None, noise
        return (-num if negative else num), noise
    return None, noise


def parse_date(value: Any) -> Tuple[Optional[datetime], Optional[str], bool]:
    """Parse a date/datetime, returning (datetime, format_family, ambiguous)."""
    if value is None:
        return None, None, False
    s = str(value).strip()
    if not s:
        return None, None, False
    if s.endswith("Z"):
        s = s[:-1]
    if re.match(r"^\d{10}$", s):
        try:
            return datetime.utcfromtimestamp(int(s)), "epoch", False
        except Exception:
            return None, None, False
    if re.match(r"^\d{13}$", s):
        try:
            return datetime.utcfromtimestamp(int(s) / 1000.0), "epoch_ms", False
        except Exception:
            return None, None, False
    for fmt, family in DATE_FORMATS:
        try:
            parsed = datetime.strptime(s, fmt)
        except ValueError:
            continue
        ambiguous = False
        if family == "slash":
            day_first = _slash_day_first(s)
            ambiguous = day_first is None
        return parsed, family, ambiguous
    return None, None, False


def _slash_day_first(s: str) -> Optional[bool]:
    """For slash dates return True if it must be day-first, False if month-first,
    None when both orders are valid (ambiguous)."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > 12 and b <= 12:
        return True
    if b > 12 and a <= 12:
        return False
    return None


BOOLEAN_VALUES = {"true", "false", "yes", "no", "y", "n", "t", "f"}


def classify_value(value: str) -> Tuple[str, Dict[str, Any]]:
    """Classify one raw value into a single primary type plus supporting detail."""
    s = (value or "").strip()
    low = s.lower()
    if low in BOOLEAN_VALUES:
        return "boolean", {}
    if EMAIL_RE.match(s):
        return "email", {}
    if URL_RE.match(s):
        return "url", {}
    if UUID_RE.match(s):
        return "uuid", {}
    num, noise = parse_number(s)
    if num is not None:
        if "currency_symbol" in noise:
            return "currency", noise
        if "percent_suffix" in noise:
            return "percent", noise
        if isinstance(num, int) and "thousands_separator" not in noise and "trailing_unit" not in noise:
            return "integer", noise
        return "number", noise
    parsed, family, ambiguous = parse_date(s)
    if parsed is not None:
        return "date", {"family": family, "ambiguous": ambiguous}
    if PHONE_RE.match(s) and re.search(r"\d", s):
        return "phone", {}
    return "text", {}


def infer_column_type(values: List[str]) -> Dict[str, Any]:
    """Infer the dominant type of a column plus the supporting evidence."""
    counts: Dict[str, int] = {
        "integer": 0, "number": 0, "currency": 0, "percent": 0, "boolean": 0,
        "date": 0, "email": 0, "url": 0, "uuid": 0, "phone": 0, "text": 0,
    }
    families: Dict[str, int] = {}
    ambiguous_dates = 0
    noise_counts: Dict[str, int] = {}
    for raw in values:
        primary, extra = classify_value(str(raw).strip())
        counts[primary] += 1
        if primary == "date":
            family = extra.get("family") or "iso"
            families[family] = families.get(family, 0) + 1
            if extra.get("ambiguous"):
                ambiguous_dates += 1
        for flag in extra:
            if flag in ("currency_symbol", "thousands_separator", "percent_suffix",
                        "parentheses_negative", "trailing_unit"):
                noise_counts[flag] = noise_counts.get(flag, 0) + 1
    total = len(values) or 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    dominant, dominant_count = ordered[0]
    confidence = dominant_count / float(total)
    mixed_with = [name for name, count in ordered[1:] if count / float(total) >= 0.25]
    return {
        "type": dominant,
        "confidence": round(confidence, 3),
        "counts": counts,
        "date_families": families,
        "ambiguous_dates": ambiguous_dates,
        "numeric_noise": noise_counts,
        "mixed_with": mixed_with,
    }
