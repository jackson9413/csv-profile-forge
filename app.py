"""csv-profile-forge - a local-first data quality workbench for CSV/TSV files.

Paste a delimited export, get a column profile, prioritized findings, a cleaning
plan, a 0-100 quality score and a downloadable cleaned CSV. No API keys, no
network calls, standard-library analysis engine.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

from flask import Flask, Response, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataq.clean import build_cleaned_csv, cleaning_summary
from dataq.db import add_session, get_session, init_db, list_sessions, update_session
from dataq.profile import profile_dataset
from dataq.report import export_json, export_markdown
from dataq.scoring import AXIS_HINTS, AXIS_LABELS, PURPOSE_LABELS, PURPOSE_WEIGHTS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SAMPLE_CSV = """order_id,customer_name,email,order_date,status,quantity,unit_price,total,discount_pct,notes,region,channel
1001,Alice Smith,alice@example.com,2026-01-05,shipped,2,19.99,39.98,0,Gift wrap,Austin,web
1002, Bob Jones ,bob@example.com,2026-01-06,Shipped,1,24.50,$24.50,10,Call on delivery,Round Rock,web
1003,Carol White,carol@example.com,01/07/2026,pending,3,10.00,30.00,0,,Austin,web
1004,Dave Green,N/A,2026-01-08,shipped,1,5.00,9.99,5,Total does not match,Houston,web
1005,Erin Black,erin@example.com,2026-01-09,SHIPPED,2,15.00,30.00,0,,Austin,web
1006,Frank Hall,frank@example.com,2026-01-10,shipped,1,12.00,12.00,150,Discount over 100,Houston,web
1007,Grace Ivy,grace@example.com,2026-01-11,shipped,2,8.00,16.00,0,Extra field,Houston,web,extra
1008,Heidi Blue,heidi@example.com,2026-01-12,shipped,1,9.99,9.99,0,Duplicate row,Round Rock,web
1008,Heidi Blue,heidi@example.com,2026-01-12,shipped,1,9.99,9.99,0,Duplicate row,Round Rock,web
"""

app = Flask(__name__)
init_db()


@app.route("/")
def index():
    return render_template(
        "index.html",
        purposes=[{"key": k, "label": PURPOSE_LABELS[k]} for k in PURPOSE_WEIGHTS],
        axis_labels=AXIS_LABELS,
        axis_hints=AXIS_HINTS,
    )


@app.route("/api/profile", methods=["POST"])
def api_profile():
    payload = request.get_json(silent=True) or {}
    text = payload.get("csv") or payload.get("data") or ""
    try:
        report = profile_dataset(text, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": "profile failed: %s" % exc}), 500
    report["cleaning_summary"] = cleaning_summary(report)
    return jsonify(report)


@app.route("/api/sample", methods=["GET"])
def api_sample():
    return jsonify({"csv": SAMPLE_CSV, "filename": "sample_orders.csv"})


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok", "app": "csv-profile-forge"})


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    return jsonify({"sessions": list_sessions(limit=200)})


@app.route("/api/sessions", methods=["POST"])
def api_add_session():
    payload = request.get_json(silent=True) or {}
    profile = payload.get("profile") or {}
    if not profile:
        return jsonify({"error": "profile payload required"}), 400
    notes = payload.get("notes") or profile.get("meta", {}).get("notes") or ""
    rating = payload.get("self_rating")
    try:
        session_id = add_session(profile, notes=notes, self_rating=rating)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": "could not save session: %s" % exc}), 500
    return jsonify({"id": session_id, "sessions": list_sessions(limit=200)})


@app.route("/api/sessions/<int:session_id>", methods=["GET"])
def api_get_session(session_id: int):
    record = get_session(session_id)
    if record is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(record)


@app.route("/api/sessions/<int:session_id>", methods=["PATCH"])
def api_patch_session(session_id: int):
    payload = request.get_json(silent=True) or {}
    record = update_session(
        session_id,
        notes=payload.get("notes"),
        self_rating=payload.get("self_rating"),
    )
    if record is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(record)


@app.route("/api/export/<int:session_id>", methods=["GET"])
def api_export(session_id: int):
    record = get_session(session_id)
    if record is None:
        return jsonify({"error": "session not found"}), 404
    profile = record.get("profile") or {}
    fmt = (request.args.get("fmt") or "md").lower()
    stem = (profile.get("meta", {}).get("dataset_name") or "dataset").replace(" ", "-")
    if fmt == "json":
        return Response(
            export_json(profile),
            mimetype="application/json",
            headers={"Content-Disposition": 'attachment; filename="%s-report.json"' % stem},
        )
    if fmt == "csv":
        return Response(
            build_cleaned_csv(profile),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="%s-cleaned.csv"' % stem},
        )
    return Response(
        export_markdown(profile),
        mimetype="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="%s-report.md"' % stem},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    app.run(host="127.0.0.1", port=port, debug=False)
