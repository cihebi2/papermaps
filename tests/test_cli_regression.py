from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
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
                    "SELECT job_name, status, detail FROM runs ORDER BY id DESC LIMIT 1"
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
                    "SELECT job_name, status, detail FROM runs ORDER BY id DESC LIMIT 1"
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
                    "SELECT job_name, status, detail FROM runs ORDER BY id DESC LIMIT 1"
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
