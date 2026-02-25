from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "cli.py"


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def write_min_config(path: Path, db_path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "database:",
                f"  path: {db_path.as_posix()}",
                "logging:",
                "  level: INFO",
                "openalex:",
                "  base_url: https://api.openalex.org",
                "  api_key: ${OPENALEX_API_KEY}",
                "  mailto: test@example.com",
                "  per_page: 200",
                "  sleep: 0.1",
                "  timeout_s: 30",
                "  max_retries: 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class TestCliRegression(unittest.TestCase):
    def test_init_db_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "idempotent.db"

            first = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(
                first.returncode,
                0,
                msg=f"first init failed stdout={first.stdout} stderr={first.stderr}",
            )

            second = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(
                second.returncode,
                0,
                msg=f"second init failed stdout={second.stdout} stderr={second.stderr}",
            )

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            finally:
                conn.close()

            table_names = {row[0] for row in rows}
            self.assertTrue({"papers", "edges", "watch_targets", "alerts", "runs"}.issubset(table_names))

    def test_smoke_run_success_writes_run_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "smoke.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                ["smoke-run", "--config", str(config_path), "--db-path", str(db_path)],
                ROOT,
            )
            self.assertEqual(result.returncode, 0, msg=f"smoke-run failed stdout={result.stdout} stderr={result.stderr}")

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs WHERE job_name='smoke-run' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "smoke-run")
            self.assertEqual(row[1], "success")

    def test_ingest_invalid_doi_fails_and_marks_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "invalid.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                [
                    "ingest-dois",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--doi",
                    "invalid-doi",
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs WHERE job_name='ingest-dois' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "ingest-dois")
            self.assertEqual(row[1], "failed")
            self.assertIn("No valid DOI input", row[2] or "")

    def test_run_scheduler_dry_run_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "scheduler.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                [
                    "run-scheduler",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--iterations",
                    "2",
                    "--interval-seconds",
                    "0",
                    "--dry-run",
                ],
                ROOT,
            )
            self.assertEqual(result.returncode, 0, msg=f"scheduler dry-run failed stdout={result.stdout} stderr={result.stderr}")

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs WHERE job_name='run-scheduler' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "run-scheduler")
            self.assertEqual(row[1], "success")
            self.assertIn("dry_run=True", row[2] or "")

    def test_run_scheduler_invalid_iterations_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "scheduler_invalid.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                [
                    "run-scheduler",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--iterations",
                    "0",
                    "--dry-run",
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid iterations", result.stderr.lower() + result.stdout.lower())

    def test_run_scheduler_invalid_max_pages_per_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "scheduler_invalid_pages.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                [
                    "run-scheduler",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--iterations",
                    "1",
                    "--max-pages-per-target",
                    "0",
                    "--dry-run",
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid max-pages-per-target", result.stderr.lower() + result.stdout.lower())

    def test_report_summary_generates_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report.db"
            report_path = tmp_path / "summary.md"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            smoke_rc = run_cli(["smoke-run", "--config", str(config_path), "--db-path", str(db_path)], ROOT)
            self.assertEqual(smoke_rc.returncode, 0, msg=smoke_rc.stdout + smoke_rc.stderr)

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--recent-runs",
                    "5",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)
            self.assertTrue(report_path.exists())

            text = report_path.read_text(encoding="utf-8")
            self.assertIn("Papermap Summary Report", text)
            self.assertIn("## Counts", text)
            self.assertIn("## Recent Runs", text)
            self.assertIn("smoke-run", text)

    def test_report_summary_missing_db_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            missing_db = tmp_path / "missing.db"
            report_path = tmp_path / "summary.md"

            result = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(missing_db),
                    "--out-file",
                    str(report_path),
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("database file does not exist", (result.stderr + result.stdout).lower())

    def test_report_summary_json_generates_machine_readable_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_json.db"
            report_path = tmp_path / "summary.json"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            smoke_rc = run_cli(["smoke-run", "--config", str(config_path), "--db-path", str(db_path)], ROOT)
            self.assertEqual(smoke_rc.returncode, 0, msg=smoke_rc.stdout + smoke_rc.stderr)

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--recent-runs",
                    "3",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)
            self.assertTrue(report_path.exists())

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("counts", payload)
            self.assertIn("recent_runs", payload)
            self.assertEqual(payload["counts"]["runs"], 1)
            self.assertEqual(payload["recent_runs"][0]["job_name"], "smoke-run")

    def test_report_summary_include_stats_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_stats.db"
            report_path = tmp_path / "summary_stats.json"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            smoke_rc = run_cli(["smoke-run", "--config", str(config_path), "--db-path", str(db_path)], ROOT)
            self.assertEqual(smoke_rc.returncode, 0, msg=smoke_rc.stdout + smoke_rc.stderr)

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--include-stats-json",
                    "--job-name-filter",
                    "smoke-run",
                    "--recent-runs",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["include_stats_json"])
            self.assertEqual(len(payload["recent_runs"]), 1)
            self.assertIn("stats_json", payload["recent_runs"][0])
            self.assertEqual(payload["recent_runs"][0]["stats_json"], {"ok": True})

    def test_report_summary_status_filter_only_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_filter.db"
            report_path = tmp_path / "summary_failed.json"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            smoke_rc = run_cli(["smoke-run", "--config", str(config_path), "--db-path", str(db_path)], ROOT)
            self.assertEqual(smoke_rc.returncode, 0, msg=smoke_rc.stdout + smoke_rc.stderr)

            fail_rc = run_cli(
                [
                    "ingest-dois",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--doi",
                    "invalid-doi",
                ],
                ROOT,
            )
            self.assertNotEqual(fail_rc.returncode, 0)

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--status-filter",
                    "failed",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status_filter"], "failed")
            self.assertTrue(len(payload["recent_runs"]) >= 1)
            for row in payload["recent_runs"]:
                self.assertEqual(row["status"], "failed")

    def test_report_summary_job_name_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_job_filter.db"
            report_path = tmp_path / "summary_smoke.json"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            smoke_rc = run_cli(["smoke-run", "--config", str(config_path), "--db-path", str(db_path)], ROOT)
            self.assertEqual(smoke_rc.returncode, 0, msg=smoke_rc.stdout + smoke_rc.stderr)

            fail_rc = run_cli(
                [
                    "ingest-dois",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--doi",
                    "invalid-doi",
                ],
                ROOT,
            )
            self.assertNotEqual(fail_rc.returncode, 0)

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--job-name-filter",
                    "smoke-run",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["job_name_filter"], "smoke-run")
            self.assertTrue(len(payload["recent_runs"]) >= 1)
            for row in payload["recent_runs"]:
                self.assertEqual(row["job_name"], "smoke-run")

    def test_report_summary_started_after_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_started_after.db"
            report_path = tmp_path / "summary_started_after.json"

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO runs (job_name, status, started_at, finished_at, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("job-old", "success", "2024-01-01 00:00:00", "2024-01-01 00:01:00", "old"),
                )
                conn.execute(
                    """
                    INSERT INTO runs (job_name, status, started_at, finished_at, detail)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("job-new", "success", "2026-02-01 00:00:00", "2026-02-01 00:01:00", "new"),
                )
                conn.commit()
            finally:
                conn.close()

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--started-after",
                    "2025-01-01",
                    "--recent-runs",
                    "10",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["started_after"], "2025-01-01")
            job_names = [row["job_name"] for row in payload["recent_runs"]]
            self.assertIn("job-new", job_names)
            self.assertNotIn("job-old", job_names)

    def test_report_summary_max_detail_length_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "report_truncate.db"
            report_path = tmp_path / "summary_truncate.json"

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO runs (job_name, status, detail) VALUES (?, ?, ?)",
                    ("manual-test", "success", "abcdefghijklmnopqrstuvwxyz"),
                )
                conn.commit()
            finally:
                conn.close()

            report_rc = run_cli(
                [
                    "report-summary",
                    "--db-path",
                    str(db_path),
                    "--out-file",
                    str(report_path),
                    "--format",
                    "json",
                    "--max-detail-length",
                    "5",
                ],
                ROOT,
            )
            self.assertEqual(report_rc.returncode, 0, msg=report_rc.stdout + report_rc.stderr)

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["max_detail_length"], 5)
            self.assertEqual(payload["recent_runs"][0]["detail"], "abcde")

    def test_add_watch_target_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_add.db"

            result = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--note",
                    "manual",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT target_type, target_value, enabled, note FROM watch_targets ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "paper")
            self.assertEqual(row[1], "W1234567890")
            self.assertEqual(row[2], 1)
            self.assertEqual(row[3], "manual")

    def test_list_watch_targets_json_respects_include_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_list.db"

            add_enabled = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(add_enabled.returncode, 0, msg=add_enabled.stdout + add_enabled.stderr)

            add_disabled = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W9999999999",
                    "--enabled",
                    "0",
                ],
                ROOT,
            )
            self.assertEqual(add_disabled.returncode, 0, msg=add_disabled.stdout + add_disabled.stderr)

            list_enabled = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                ],
                ROOT,
            )
            self.assertEqual(list_enabled.returncode, 0, msg=list_enabled.stdout + list_enabled.stderr)
            enabled_payload = json.loads(list_enabled.stdout)
            self.assertEqual(len(enabled_payload), 1)
            self.assertEqual(enabled_payload[0]["target_value"], "W1234567890")

            list_all = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                    "--include-disabled",
                ],
                ROOT,
            )
            self.assertEqual(list_all.returncode, 0, msg=list_all.stdout + list_all.stderr)
            all_payload = json.loads(list_all.stdout)
            self.assertEqual(len(all_payload), 2)

    def test_list_watch_targets_enabled_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_filter.db"

            add_enabled = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(add_enabled.returncode, 0, msg=add_enabled.stdout + add_enabled.stderr)

            add_disabled = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W9999999999",
                    "--enabled",
                    "0",
                ],
                ROOT,
            )
            self.assertEqual(add_disabled.returncode, 0, msg=add_disabled.stdout + add_disabled.stderr)

            list_disabled = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                    "--include-disabled",
                    "--enabled",
                    "0",
                ],
                ROOT,
            )
            self.assertEqual(list_disabled.returncode, 0, msg=list_disabled.stdout + list_disabled.stderr)
            payload = json.loads(list_disabled.stdout)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["target_value"], "W9999999999")
            self.assertEqual(payload[0]["enabled"], 0)

    def test_set_watch_enabled_updates_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_toggle.db"

            add_rc = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(add_rc.returncode, 0, msg=add_rc.stdout + add_rc.stderr)

            disable_rc = run_cli(
                [
                    "set-watch-enabled",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "0",
                ],
                ROOT,
            )
            self.assertEqual(disable_rc.returncode, 0, msg=disable_rc.stdout + disable_rc.stderr)

            list_enabled = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                ],
                ROOT,
            )
            payload_enabled = json.loads(list_enabled.stdout)
            self.assertEqual(len(payload_enabled), 0)

            list_all = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                    "--include-disabled",
                ],
                ROOT,
            )
            payload_all = json.loads(list_all.stdout)
            self.assertEqual(len(payload_all), 1)
            self.assertEqual(payload_all[0]["enabled"], 0)

    def test_remove_watch_target_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_remove.db"

            add_rc = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(add_rc.returncode, 0, msg=add_rc.stdout + add_rc.stderr)

            remove_rc = run_cli(
                [
                    "remove-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                ],
                ROOT,
            )
            self.assertEqual(remove_rc.returncode, 0, msg=remove_rc.stdout + remove_rc.stderr)

            list_all = run_cli(
                [
                    "list-watch-targets",
                    "--db-path",
                    str(db_path),
                    "--format",
                    "json",
                    "--include-disabled",
                ],
                ROOT,
            )
            payload_all = json.loads(list_all.stdout)
            self.assertEqual(len(payload_all), 0)

    def test_remove_watch_target_not_found_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "watch_remove_missing.db"

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            remove_rc = run_cli(
                [
                    "remove-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                ],
                ROOT,
            )
            self.assertNotEqual(remove_rc.returncode, 0)
            self.assertIn("not found", remove_rc.stderr.lower() + remove_rc.stdout.lower())

    def test_track_citations_dry_run_succeeds_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "track_dry.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            add_rc = run_cli(
                [
                    "add-watch-target",
                    "--db-path",
                    str(db_path),
                    "--target-type",
                    "paper",
                    "--target-value",
                    "W1234567890",
                    "--enabled",
                    "1",
                ],
                ROOT,
            )
            self.assertEqual(add_rc.returncode, 0, msg=add_rc.stdout + add_rc.stderr)

            track_rc = run_cli(
                [
                    "track-citations",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--dry-run",
                ],
                ROOT,
            )
            self.assertEqual(track_rc.returncode, 0, msg=track_rc.stdout + track_rc.stderr)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs WHERE job_name='track-citations' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "track-citations")
            self.assertEqual(row[1], "success")
            self.assertIn("targets=1", row[2] or "")

    def test_track_citations_invalid_max_pages_per_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "track_invalid_pages.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            result = run_cli(
                [
                    "track-citations",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--dry-run",
                    "--target-id",
                    "W1234567890",
                    "--max-pages-per-target",
                    "0",
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid max-pages-per-target", result.stderr.lower() + result.stdout.lower())

    def test_track_citations_dry_run_with_target_id_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "track_override.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            track_rc = run_cli(
                [
                    "track-citations",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--dry-run",
                    "--target-id",
                    "W1234567890",
                ],
                ROOT,
            )
            self.assertEqual(track_rc.returncode, 0, msg=track_rc.stdout + track_rc.stderr)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "track-citations")
            self.assertEqual(row[1], "success")
            self.assertIn("targets=1", row[2] or "")

    def test_run_scheduler_stop_on_failure_stops_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "scheduler_stop.db"
            config_path = tmp_path / "config.yaml"
            write_min_config(config_path, db_path)

            init_rc = run_cli(["init-db", "--db-path", str(db_path)], ROOT)
            self.assertEqual(init_rc.returncode, 0, msg=init_rc.stdout + init_rc.stderr)

            result = run_cli(
                [
                    "run-scheduler",
                    "--config",
                    str(config_path),
                    "--db-path",
                    str(db_path),
                    "--iterations",
                    "3",
                    "--interval-seconds",
                    "0",
                    "--stop-on-failure",
                ],
                ROOT,
            )
            self.assertNotEqual(result.returncode, 0)

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT job_name, status, detail FROM runs WHERE job_name='run-scheduler' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()

            self.assertIsNotNone(row)
            self.assertEqual(row[0], "run-scheduler")
            self.assertEqual(row[1], "failed")
            self.assertIn("completed=1", row[2] or "")
            self.assertIn("failures=1", row[2] or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
