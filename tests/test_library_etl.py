import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library_etl import FIELDS, DataError, preprocess, refresh, read_current
from library_etl.pipeline import REQUIRED


def source(day="2026-09-10", gate="자료실.정문", count="1"):
    row = {key: count for key in REQUIRED}
    row.update({"수집일자": day, "게이트": gate, "통로ID": "sensor-A", "전체_IN": "16", "전체_OUT": "16"})
    return row


class Sheet:
    def __init__(self, rows, header=REQUIRED):
        self.rows, self.header = rows, header

    def iter_rows(self, values_only=True):
        yield self.header
        for row in self.rows:
            yield tuple(row.get(k) for k in self.header)


class Workbook:
    sheetnames = ["Data"]

    def __init__(self, rows, header=REQUIRED):
        self.sheet = Sheet(rows, header)

    def __getitem__(self, key):
        return self.sheet

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def run_source(self, rows, **kwargs):
        with patch("library_etl.pipeline.load_workbook", return_value=Workbook(rows)):
            return preprocess("arbitrary.xlsx", **kwargs)

    def test_exact_contract_types_and_values(self):
        rows, _ = self.run_source([source()], partial_dates=["2026-09-10"])
        self.assertEqual(len(rows), 16)
        self.assertEqual(tuple(rows[0]), FIELDS)
        self.assertEqual(rows[0], dict(zip(FIELDS, ("2026-09-10", "Thu", "front", "자료실.정문",
                                                   "sensor-A", 8, 1, 1, 16, 16, True, "arbitrary.xlsx"))))
        self.assertIs(type(rows[0]["in_count"]), int)
        self.assertIs(type(rows[0]["is_partial"]), bool)
        self.assertEqual(rows[-1]["hour"], 23)

    def test_missing_negative_fraction_formula_rejected(self):
        for bad in [None, "", -1, 1.5, "1.5", "=1+1", True, float("nan"), "NaN"]:
            with self.subTest(value=bad):
                row = source()
                row["IN_08"] = bad
                with self.assertRaises(DataError) as cm:
                    self.run_source([row])
                self.assertEqual(cm.exception.issues[0]["field"], "IN_08")

    def test_zero_is_preserved(self):
        rows, _ = self.run_source([source(count="0")])
        self.assertEqual(rows[0]["in_count"], 0)

    def test_duplicate_does_not_sum(self):
        with self.assertRaises(DataError):
            self.run_source([source(), source()])

    def test_source_summary_only_is_skipped(self):
        summary = {"수집일자": "전체", "게이트": "그룹", "통로ID": "합계"}
        rows, report = self.run_source([summary, source()])
        self.assertEqual(len(rows), 16)
        self.assertEqual(report["skipped_rows"][0]["row"], 2)
        summary["게이트"] = "unexpected"
        with self.assertRaises(DataError):
            self.run_source([summary, source()])

    def test_bad_date_gate_missing_columns(self):
        for field, value in [("수집일자", "2026-02-30"), ("게이트", "other")]:
            row = source()
            row[field] = value
            with self.assertRaises(DataError):
                self.run_source([row])
        with patch("library_etl.pipeline.load_workbook", return_value=Workbook([source()], REQUIRED[:-1])):
            with self.assertRaises(DataError):
                preprocess("x.xlsx")

    def test_partial_is_not_based_on_execution_date(self):
        rows, report = self.run_source([source("2022-01-01"), source("2022-01-02")])
        self.assertFalse(rows[0]["is_partial"])
        self.assertTrue(rows[-1]["is_partial"])
        self.assertIn("PARTIAL_DATE_UNCONFIRMED", report["warning_counts"])
        rows, _ = self.run_source([source()], partial_dates=[])
        self.assertFalse(rows[0]["is_partial"])

    def test_totals_are_not_overwritten_or_distributed(self):
        row = source()
        row["전체_IN"] = "999"
        records, report = self.run_source([row])
        self.assertEqual(records[0]["total_in"], 999)
        self.assertEqual(sum(r["in_count"] for r in records), 16)
        self.assertEqual(report["warning_counts"]["HOURLY_TOTAL_MISMATCH"], 1)

    def test_invalid_file_and_sheet(self):
        with self.assertRaises(DataError):
            preprocess("file.csv")
        with self.assertRaises(DataError):
            self.run_source([source()], sheet="missing")
        with self.assertRaises(DataError):
            self.run_source([source()], partial_dates=["2020-01-01"])

    def test_refresh_idempotent_and_replace_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "data.sqlite3"
            initial = [source("2026-09-09"), source()]
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook(initial)):
                refresh("x.xlsx", db)
                refresh("x.xlsx", db)
            self.assertEqual(len(read_current(db)[0]), 32)
            updated = source(count="3")
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook([updated])):
                state = refresh("x.xlsx", db, partial_dates=[])
            rows, _ = read_current(db)
            self.assertEqual(len(rows), 32)
            self.assertEqual(rows[0]["in_count"], 1)
            self.assertEqual(rows[-1]["in_count"], 3)
            self.assertEqual(state["integration_status"], "pending_downstream")

    def test_bad_refresh_preserves_records_and_derived_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "data.sqlite3"
            def calculate(rows):
                return {"test_in_sum": sum(r["in_count"] for r in rows)}
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook([source()])):
                refresh("x.xlsx", db, rebuild=calculate)
            old = read_current(db)
            def broken(rows):
                raise RuntimeError("downstream failed")
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook([source(count="2")])):
                with self.assertRaises(DataError):
                    refresh("x.xlsx", db, rebuild=broken)
            self.assertEqual(read_current(db), old)
            invalid = source(count=None)
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook([invalid])):
                with self.assertRaises(DataError):
                    refresh("x.xlsx", db)
            self.assertEqual(read_current(db), old)

    def test_complete_cannot_be_downgraded_to_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "data.sqlite3"
            with patch("library_etl.pipeline.load_workbook", return_value=Workbook([source()])):
                refresh("x.xlsx", db, partial_dates=[])
                old = read_current(db)
                with self.assertRaises(DataError):
                    refresh("x.xlsx", db)
                self.assertEqual(read_current(db), old)

    def test_missing_database_read_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "absent.sqlite3"
            with self.assertRaises(DataError):
                read_current(db)
            self.assertFalse(db.exists())

    @unittest.skipUnless(os.environ.get("LIBRARY_SAMPLE_XLSX"), "실제 파일 경로 미지정")
    def test_real_workbook_end_to_end(self):
        path = os.environ["LIBRARY_SAMPLE_XLSX"]
        rows, report = preprocess(path, partial_dates=["2026-09-10"])
        self.assertEqual(len(rows), 29376)
        self.assertEqual(report["source_rows"], 1836)
        self.assertEqual(report["dates"], 918)
        self.assertEqual(len(report["skipped_rows"]), 2)
        self.assertEqual(sum(r["is_partial"] for r in rows), 32)
        self.assertEqual(report["warning_counts"]["HOURLY_TOTAL_MISMATCH"], 3626)
        # Source row 4: IN_08=4, OUT_08=2, daily totals=75/58.
        record = next(r for r in rows if r["date"] == "2026-09-10" and r["gate"] == "front" and r["hour"] == 8)
        self.assertEqual([record[k] for k in ("in_count", "out_count", "total_in", "total_out")], [4, 2, 75, 58])
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "data.sqlite3"
            refresh(path, db, partial_dates=["2026-09-10"])
            refresh(path, db, partial_dates=["2026-09-10"])
            self.assertEqual(read_current(db)[0], rows)


if __name__ == "__main__":
    unittest.main()
