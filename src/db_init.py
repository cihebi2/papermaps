from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS papers (
        paper_id TEXT PRIMARY KEY,
        title TEXT,
        published_date TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        src_paper_id TEXT NOT NULL,
        dst_paper_id TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT 'cites',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (src_paper_id, dst_paper_id, relation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS watch_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_type TEXT NOT NULL,
        target_value TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        watch_target_id INTEGER NOT NULL,
        paper_id TEXT,
        alert_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (watch_target_id) REFERENCES watch_targets(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        detail TEXT
    )
    """,
]

INDEX_STATEMENTS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_targets_unique ON watch_targets(target_type, target_value)",
    "CREATE INDEX IF NOT EXISTS idx_edges_src_relation ON edges(src_paper_id, relation)",
    "CREATE INDEX IF NOT EXISTS idx_edges_dst_relation ON edges(dst_paper_id, relation)",
]

PAPER_EXTRA_COLUMNS = {
    "doi": "TEXT",
    "abstract": "TEXT",
    "cited_by_count": "INTEGER",
    "journal": "TEXT",
    "raw_json": "TEXT",
    "source": "TEXT",
    "updated_at": "TEXT",
}

WATCH_EXTRA_COLUMNS = {
    "last_check_date": "TEXT",
    "note": "TEXT",
}

EDGE_EXTRA_COLUMNS = {
    "run_id": "INTEGER",
    "discovered_at": "TEXT",
}

RUN_EXTRA_COLUMNS = {
    "stats_json": "TEXT",
}


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    cursor = connection.execute(f"PRAGMA table_info({table_name})")
    return {str(row[1]) for row in cursor.fetchall()}


def _ensure_columns(connection: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = _table_columns(connection, table_name)
    for column_name, column_type in columns.items():
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _run_migrations(connection: sqlite3.Connection) -> None:
    _ensure_columns(connection, "papers", PAPER_EXTRA_COLUMNS)
    _ensure_columns(connection, "watch_targets", WATCH_EXTRA_COLUMNS)
    _ensure_columns(connection, "edges", EDGE_EXTRA_COLUMNS)
    _ensure_columns(connection, "runs", RUN_EXTRA_COLUMNS)
    for statement in INDEX_STATEMENTS:
        connection.execute(statement)


def init_db(db_path: Path) -> None:
    """Create SQLite database and base schema, then run idempotent migrations."""
    LOGGER.info("init-db start db_path=%s", db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(db_path)
        with connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            _run_migrations(connection)
        LOGGER.info("init-db success db_path=%s tables=%d", db_path, len(SCHEMA_STATEMENTS))
    except sqlite3.Error:
        LOGGER.exception("init-db failed db_path=%s", db_path)
        raise
    finally:
        if connection is not None:
            connection.close()


def create_run(db_path: Path, job_name: str, detail: str | None = None) -> int:
    """Insert a running record into runs and return run id."""
    LOGGER.info("run create start db_path=%s job_name=%s", db_path, job_name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(db_path)
        cursor = connection.execute(
            "INSERT INTO runs (job_name, status, detail) VALUES (?, 'running', ?)",
            (job_name, detail),
        )
        connection.commit()
        run_id = int(cursor.lastrowid)
        LOGGER.info("run create success db_path=%s run_id=%s", db_path, run_id)
        return run_id
    except sqlite3.Error:
        LOGGER.exception("run create failed db_path=%s job_name=%s", db_path, job_name)
        raise
    finally:
        if connection is not None:
            connection.close()


def finish_run(
    db_path: Path,
    run_id: int,
    status: str,
    detail: str | None = None,
    stats_json: str | None = None,
) -> None:
    """Update run status to success or failed and stamp finished_at."""
    if status not in {"success", "failed"}:
        raise ValueError(f"Unsupported run status: {status}")

    LOGGER.info("run finish start db_path=%s run_id=%s status=%s", db_path, run_id, status)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(db_path)
        cursor = connection.execute(
            """
            UPDATE runs
            SET status = ?, finished_at = CURRENT_TIMESTAMP, detail = COALESCE(?, detail), stats_json = COALESCE(?, stats_json)
            WHERE id = ? AND status = 'running'
            """,
            (status, detail, stats_json, run_id),
        )
        connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError(f"Run not found or not running: id={run_id}")
        LOGGER.info("run finish success db_path=%s run_id=%s status=%s", db_path, run_id, status)
    except sqlite3.Error:
        LOGGER.exception("run finish failed db_path=%s run_id=%s", db_path, run_id)
        raise
    finally:
        if connection is not None:
            connection.close()
