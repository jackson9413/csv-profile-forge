"""Smoke tests for the csv-profile-forge engine and API."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dataq import db  # noqa: E402
from dataq.clean import build_cleaned_csv  # noqa: E402
from dataq.parser import (  # noqa: E402
    detect_delimiter,
    infer_column_type,
    parse_date,
    parse_number,
    parse_table,
)
from dataq.profile import profile_dataset  # noqa: E402
from dataq.report import export_json, export_markdown  # noqa: E402
from dataq.scoring import verdict_for  # noqa: E402

TMP_DB = os.path.join(tempfile.gettempdir(), "csv_profile_forge_tests.db")
db.DB_PATH = TMP_DB

from app import app as flask_app  # noqa: E402

SAMPLE_PATH = os.path.join(BASE_DIR, "samples", "sample_orders.csv")
with open(SAMPLE_PATH, "r", encoding="utf-8") as handle:
    SAMPLE_CSV = handle.read()

CLEAN_CSV = (
    "id,name,email,amount,signup_date\n"
    "1,Alice,alice@example.com,19.99,2026-01-01\n"
    "2,Bob,bob@example.com,24.50,2026-01-02\n"
    "3,Carol,carol@example.com,30.00,2026-01-03\n"
)


def finding_ids(report):
    return {f["id"] for f in report["findings"]}


class ParserTests(unittest.TestCase):
    def test_detect_delimiter(self):
        self.assertEqual(detect_delimiter("a,b,c\n1,2,3\n"), ",")
        self.assertEqual(detect_delimiter("a\tb\tc\n1\t2\t3\n"), "\t")
        self.assertEqual(detect_delimiter("a;b;c\n1;2;3\n"), ";")

    def test_parse_table_tracks_ragged_rows(self):
        table = parse_table("a,b\n1,2\n3\n")
        self.assertEqual(table["headers"], ["a", "b"])
        self.assertEqual(table["ragged_rows"], 1)

    def test_parse_number_reports_noise(self):
        value, noise = parse_number("$1,234.50")
        self.assertAlmostEqual(value, 1234.50)
        self.assertIn("currency_symbol", noise)
        self.assertIn("thousands_separator", noise)

    def test_parse_number_rejects_bad_format(self):
        value, _noise = parse_number("1,23")
        self.assertIsNone(value)

    def test_parse_date_flags_ambiguity(self):
        parsed, family, ambiguous = parse_date("01/07/2026")
        self.assertIsNotNone(parsed)
        self.assertEqual(family, "slash")
        self.assertTrue(ambiguous)

    def test_infer_types(self):
        self.assertEqual(infer_column_type(["1", "2", "3"])["type"], "integer")
        self.assertEqual(infer_column_type(["19.5", "20.25", "3"])["type"], "number")
        self.assertEqual(infer_column_type(["2026-01-01", "2026-01-02"])["type"], "date")
        self.assertEqual(infer_column_type(["a@b.com", "c@d.org"])["type"], "email")
        self.assertEqual(infer_column_type(["yes", "no", "yes"])["type"], "boolean")
        self.assertIn("integer", infer_column_type(["abc", "12", "xyz"])["mixed_with"])


class ProfilingTests(unittest.TestCase):
    def setUp(self):
        self.report = profile_dataset(SAMPLE_CSV, {"dataset_name": "orders-export"})

    def test_sample_shape(self):
        self.assertEqual(self.report["meta"]["rows"], 9)
        self.assertEqual(self.report["meta"]["columns"], 12)
        self.assertEqual(self.report["meta"]["delimiter"], "comma")

    def test_finds_known_problems(self):
        ids = finding_ids(self.report)
        for expected in [
            "duplicate_rows", "ragged_rows", "formula_mismatch", "percent_out_of_range",
            "mixed_date_formats", "ambiguous_dates", "inconsistent_casing", "whitespace_padding",
            "numeric_noise", "constant_column", "missing_values",
        ]:
            self.assertIn(expected, ids)

    def test_evidence_points_at_rows(self):
        dup = next(f for f in self.report["findings"] if f["id"] == "duplicate_rows")
        self.assertTrue(dup["evidence"])
        self.assertIn("row", dup["evidence"][0])

    def test_score_and_verdict(self):
        scores = self.report["scores"]
        self.assertLess(scores["overall"], 70)
        self.assertIn(scores["verdict"], {"needs_rework", "tight_but_usable", "needs_polish"})
        self.assertEqual(len(scores["axes"]), 5)

    def test_check_list_reflects_findings(self):
        failed = [c for c in self.report["checks"] if c["status"] == "fail"]
        passed = [c for c in self.report["checks"] if c["status"] == "pass"]
        self.assertGreaterEqual(len(failed), 6)
        self.assertGreaterEqual(len(passed), 4)
        self.assertIn("totals_reconcile", [c["id"] for c in failed])

    def test_clean_data_has_all_checks_passing(self):
        clean = profile_dataset(CLEAN_CSV, {"dataset_name": "clean"})
        self.assertTrue(all(c["status"] == "pass" for c in clean["checks"]))

    def test_cleaning_plan_has_auto_and_manual_steps(self):
        plan = self.report["cleaning_plan"]
        self.assertTrue(any(p["auto"] for p in plan))
        self.assertTrue(any(not p["auto"] for p in plan))

    def test_clean_data_scores_high(self):
        clean = profile_dataset(CLEAN_CSV, {"dataset_name": "clean"})
        self.assertGreaterEqual(clean["scores"]["overall"], 85)
        self.assertEqual(clean["scores"]["verdict"], "ship_ready")

    def test_key_columns_surface_duplicate_keys(self):
        report = profile_dataset(
            "id,name\n1,A\n1,B\n2,C\n", {"key_columns": "id"}
        )
        self.assertIn("duplicate_keys", finding_ids(report))

    def test_row_count_mismatch(self):
        report = profile_dataset(CLEAN_CSV, {"expected_rows": 99})
        self.assertIn("row_count_mismatch", finding_ids(report))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.report = profile_dataset(SAMPLE_CSV, {"dataset_name": "orders-export"})

    def test_markdown_export(self):
        md = export_markdown(self.report)
        self.assertIn("Data quality report", md)
        self.assertIn("Cleaning plan", md)

    def test_json_export(self):
        payload = json.loads(export_json(self.report))
        self.assertIn("scores", payload)
        self.assertNotIn("raw_rows", payload)

    def test_cleaned_csv_fixes_safe_things(self):
        cleaned = build_cleaned_csv(self.report)
        rows = [r for r in cleaned.split("\n") if r.strip()]
        self.assertEqual(len(rows), 9)  # header + 8 unique data rows (duplicate dropped)
        self.assertNotIn(" Bob Jones ", cleaned)
        self.assertIn("Bob Jones", cleaned)
        self.assertNotIn(",N/A,", cleaned)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(TMP_DB):
            os.remove(TMP_DB)
        db.init_db()
        flask_app.config["TESTING"] = True
        cls.client = flask_app.test_client()

    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    def test_sample_endpoint(self):
        data = self.client.get("/api/sample").get_json()
        self.assertIn("order_id", data["csv"])

    def test_profile_endpoint(self):
        res = self.client.post("/api/profile", json={"csv": SAMPLE_CSV, "dataset_name": "orders"})
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("scores", body)
        self.assertGreaterEqual(body["scores"]["overall"], 0)

    def test_profile_rejects_empty(self):
        res = self.client.post("/api/profile", json={"csv": ""})
        self.assertEqual(res.status_code, 400)

    def test_session_lifecycle_and_exports(self):
        report = self.client.post("/api/profile", json={"csv": SAMPLE_CSV}).get_json()
        saved = self.client.post("/api/sessions", json={"profile": report, "notes": "messy file"})
        self.assertEqual(saved.status_code, 200)
        session_id = saved.get_json()["id"]

        listing = self.client.get("/api/sessions").get_json()
        self.assertTrue(any(s["id"] == session_id for s in listing["sessions"]))

        patched = self.client.patch(
            "/api/sessions/%d" % session_id, json={"notes": "fixed upstream", "self_rating": 4}
        ).get_json()
        self.assertEqual(patched["notes"], "fixed upstream")
        self.assertEqual(patched["self_rating"], 4)

        md = self.client.get("/api/export/%d?fmt=md" % session_id)
        self.assertEqual(md.status_code, 200)
        self.assertIn("Data quality report", md.get_data(as_text=True))

        blob = self.client.get("/api/export/%d?fmt=csv" % session_id)
        self.assertEqual(blob.status_code, 200)
        self.assertIn("text/csv", blob.headers["Content-Type"])

        js = self.client.get("/api/export/%d?fmt=json" % session_id)
        self.assertIn("scores", js.get_json())

    def test_index_renders(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("csv-profile-forge", res.get_data(as_text=True))


class VerdictTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(verdict_for(90)["verdict"], "ship_ready")
        self.assertEqual(verdict_for(75)["verdict"], "mostly_ready")
        self.assertEqual(verdict_for(60)["verdict"], "needs_polish")
        self.assertEqual(verdict_for(45)["verdict"], "tight_but_usable")
        self.assertEqual(verdict_for(10)["verdict"], "needs_rework")


if __name__ == "__main__":
    unittest.main(verbosity=2)
