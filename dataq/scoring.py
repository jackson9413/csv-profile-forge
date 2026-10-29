"""Axis scoring, context weights, and verdict bands."""

from __future__ import annotations

from typing import Any, Dict, List

AXIS_LABELS = {
    "completeness": "Completeness",
    "consistency": "Consistency",
    "validity": "Validity",
    "uniqueness": "Uniqueness",
    "structure": "Structure",
}

AXIS_HINTS = {
    "completeness": "Are the cells actually filled in?",
    "consistency": "Do values follow one format?",
    "validity": "Do values parse and make sense?",
    "uniqueness": "Any duplicate records or keys?",
    "structure": "Are headers and row shapes sane?",
}

PURPOSE_WEIGHTS = {
    "general": {"completeness": 0.25, "consistency": 0.20, "validity": 0.25, "uniqueness": 0.15, "structure": 0.15},
    "analytics": {"completeness": 0.25, "consistency": 0.20, "validity": 0.30, "uniqueness": 0.10, "structure": 0.15},
    "financial": {"completeness": 0.25, "consistency": 0.15, "validity": 0.30, "uniqueness": 0.10, "structure": 0.20},
    "migration": {"completeness": 0.20, "consistency": 0.25, "validity": 0.15, "uniqueness": 0.10, "structure": 0.30},
    "customer": {"completeness": 0.30, "consistency": 0.25, "validity": 0.20, "uniqueness": 0.15, "structure": 0.10},
}

PURPOSE_LABELS = {
    "general": "General purpose",
    "analytics": "Analytics / reporting",
    "financial": "Financial / audit",
    "migration": "Data migration / import",
    "customer": "Customer / CRM records",
}

VERDICT_BANDS = [
    (85, "ship_ready", "Ship ready"),
    (70, "mostly_ready", "Mostly ready"),
    (55, "needs_polish", "Needs polish"),
    (40, "tight_but_usable", "Tight but usable"),
    (0, "needs_rework", "Needs rework"),
]


def verdict_for(score: float) -> Dict[str, str]:
    for threshold, key, label in VERDICT_BANDS:
        if score >= threshold:
            return {"verdict": key, "verdict_label": label}
    return {"verdict": "needs_rework", "verdict_label": "Needs rework"}


def apply_axis_penalties(base: Dict[str, float], findings: List[Dict[str, Any]]) -> Dict[str, float]:
    """Findings subtract from their own axis so a bad column cannot hide."""
    out = {}
    for axis, value in base.items():
        penalty = 0.0
        for f in findings:
            if f.get("axis") != axis:
                continue
            sev = f.get("severity")
            penalty += {"high": 12.0, "medium": 6.0, "low": 2.0}.get(sev, 1.0)
        out[axis] = max(0.0, min(100.0, value - penalty))
    return out


def score_dataset(axes: Dict[str, float], findings: List[Dict[str, Any]], purpose: str) -> Dict[str, Any]:
    weights = PURPOSE_WEIGHTS.get(purpose, PURPOSE_WEIGHTS["general"])
    overall = sum(axes.get(k, 0.0) * w for k, w in weights.items())
    highs = sum(1 for f in findings if f.get("severity") == "high")
    mediums = sum(1 for f in findings if f.get("severity") == "medium")
    overall -= min(10.0, 2.0 * highs)
    overall -= min(6.0, 1.0 * mediums)
    overall = max(0.0, min(100.0, overall))
    result = {"axes": axes, "weights": weights, "overall": round(overall, 1)}
    result.update(verdict_for(overall))
    result["finding_counts"] = {
        "high": highs,
        "medium": mediums,
        "low": sum(1 for f in findings if f.get("severity") == "low"),
    }
    return result
