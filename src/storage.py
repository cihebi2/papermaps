from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .openalex_client import canonical_work_id, reconstruct_abstract
except ImportError:
    from openalex_client import canonical_work_id, reconstruct_abstract


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/") :]
    if doi.startswith("doi:"):
        doi = doi[4:]
    return doi or None


def _work_to_record(work: dict[str, Any]) -> dict[str, Any]:
    work_id = canonical_work_id(work.get("id"))
    if not work_id:
        raise ValueError("Work has no valid OpenAlex id")

    source_obj = ((work.get("primary_location") or {}).get("source") or {}) if isinstance(work, dict) else {}
    title = work.get("title") or work.get("display_name") or work_id
    published_date = work.get("publication_date")
    doi = _normalize_doi(work.get("doi"))
    abstract = work.get("abstract") or reconstruct_abstract(work.get("abstract_inverted_index"))
    cited_by_count = work.get("cited_by_count")
    journal = source_obj.get("display_name")

    return {
        "paper_id": work_id,
        "title": str(title) if title is not None else work_id,
        "published_date": str(published_date) if published_date else None,
        "doi": doi,
        "abstract": str(abstract) if abstract else None,
        "cited_by_count": int(cited_by_count) if isinstance(cited_by_count, int) else None,
        "journal": str(journal) if journal else None,
        "raw_json": json.dumps(work, ensure_ascii=False),
    }


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def set_app_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    setting_key = str(key).strip()
    if not setting_key:
        raise ValueError("Setting key must not be empty")
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (setting_key, value),
    )


def get_app_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    setting_key = str(key).strip()
    if not setting_key:
        return default
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (setting_key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def list_app_settings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT key, value, updated_at FROM app_settings ORDER BY key ASC").fetchall())


def add_saved_search(conn: sqlite3.Connection, doi_list: list[str], result_payload: dict[str, Any]) -> int:
    dois = [str(item).strip().lower() for item in doi_list if str(item).strip()]
    if not dois:
        raise ValueError("doi_list must not be empty")
    cursor = conn.execute(
        """
        INSERT INTO saved_searches (doi_list, result_json)
        VALUES (?, ?)
        """,
        (
            json.dumps(dois, ensure_ascii=False),
            json.dumps(result_payload, ensure_ascii=False),
        ),
    )
    return int(cursor.lastrowid)


def list_saved_searches(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT id, doi_list, result_json, created_at
            FROM saved_searches
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    )


def upsert_notification_target(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_value: str,
    enabled: int = 1,
) -> int:
    t_type = str(target_type).strip()
    t_value = str(target_value).strip()
    if not t_type or not t_value:
        raise ValueError("target_type and target_value must not be empty")
    conn.execute(
        """
        INSERT INTO notification_targets (target_type, target_value, enabled)
        VALUES (?, ?, ?)
        ON CONFLICT(target_type, target_value) DO UPDATE SET
            enabled = excluded.enabled
        """,
        (t_type, t_value, int(enabled)),
    )
    row = conn.execute(
        """
        SELECT id
        FROM notification_targets
        WHERE target_type = ? AND target_value = ?
        LIMIT 1
        """,
        (t_type, t_value),
    ).fetchone()
    return int(row["id"]) if row is not None else 0


def list_notification_targets(
    conn: sqlite3.Connection,
    *,
    target_type: str | None = None,
    include_disabled: bool = False,
    limit: int = 100,
) -> list[sqlite3.Row]:
    sql = "SELECT id, target_type, target_value, enabled, created_at FROM notification_targets WHERE 1=1"
    params: list[Any] = []
    if target_type is not None:
        sql += " AND target_type = ?"
        params.append(str(target_type))
    if not include_disabled:
        sql += " AND enabled = 1"
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(max(1, int(limit)))
    return list(conn.execute(sql, tuple(params)).fetchall())


def add_alert(
    conn: sqlite3.Connection,
    *,
    watch_target_id: int,
    paper_id: str,
    alert_type: str,
    status: str = "new",
    payload_json: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO alerts (watch_target_id, paper_id, alert_type, status, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(watch_target_id), paper_id, alert_type, status, payload_json),
    )
    if int(cursor.rowcount) == 1:
        return int(cursor.lastrowid)
    row = conn.execute(
        """
        SELECT id
        FROM alerts
        WHERE watch_target_id = ? AND paper_id = ? AND alert_type = ?
        LIMIT 1
        """,
        (int(watch_target_id), paper_id, alert_type),
    ).fetchone()
    return int(row["id"]) if row is not None else 0


def list_alerts(conn: sqlite3.Connection, *, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    sql = "SELECT id, watch_target_id, paper_id, alert_type, status, payload_json, pushed_at, created_at FROM alerts WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return list(conn.execute(sql, tuple(params)).fetchall())


def mark_alert_pushed(conn: sqlite3.Connection, alert_id: int) -> int:
    cursor = conn.execute(
        """
        UPDATE alerts
        SET status = 'pushed', pushed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (int(alert_id),),
    )
    return int(cursor.rowcount)


def upsert_work(conn: sqlite3.Connection, work: dict[str, Any], source: str | None = None) -> str:
    record = _work_to_record(work)
    conn.execute(
        """
        INSERT INTO papers (paper_id, title, published_date, doi, abstract, cited_by_count, journal, raw_json, source, updated_at)
        VALUES (:paper_id, :title, :published_date, :doi, :abstract, :cited_by_count, :journal, :raw_json, :source, CURRENT_TIMESTAMP)
        ON CONFLICT(paper_id) DO UPDATE SET
            title = excluded.title,
            published_date = COALESCE(excluded.published_date, papers.published_date),
            doi = COALESCE(excluded.doi, papers.doi),
            abstract = COALESCE(excluded.abstract, papers.abstract),
            cited_by_count = COALESCE(excluded.cited_by_count, papers.cited_by_count),
            journal = COALESCE(excluded.journal, papers.journal),
            raw_json = COALESCE(excluded.raw_json, papers.raw_json),
            source = COALESCE(excluded.source, papers.source),
            updated_at = CURRENT_TIMESTAMP
        """,
        {
            **record,
            "source": source,
        },
    )
    return str(record["paper_id"])


def ensure_paper_stub(conn: sqlite3.Connection, paper_id: str, title: str | None = None) -> None:
    pid = canonical_work_id(paper_id)
    if not pid:
        return
    conn.execute(
        """
        INSERT INTO papers (paper_id, title, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(paper_id) DO UPDATE SET
            title = COALESCE(papers.title, excluded.title),
            updated_at = CURRENT_TIMESTAMP
        """,
        (pid, title or pid),
    )


def add_edge(
    conn: sqlite3.Connection,
    src_paper_id: str,
    dst_paper_id: str,
    relation: str,
    run_id: int | None = None,
) -> None:
    src = canonical_work_id(src_paper_id)
    dst = canonical_work_id(dst_paper_id)
    if not src or not dst:
        return
    conn.execute(
        """
        INSERT INTO edges (src_paper_id, dst_paper_id, relation, run_id, discovered_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(src_paper_id, dst_paper_id, relation) DO NOTHING
        """,
        (src, dst, relation, run_id),
    )


def add_watch_target(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_value: str,
    note: str | None = None,
    enabled: int = 1,
) -> None:
    target = canonical_work_id(target_value) if target_type == "paper" else target_value
    if not target:
        return
    conn.execute(
        """
        INSERT INTO watch_targets (target_type, target_value, enabled, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(target_type, target_value) DO UPDATE SET
            enabled = excluded.enabled,
            note = COALESCE(excluded.note, watch_targets.note)
        """,
        (target_type, target, int(enabled), note),
    )


def set_watch_target_enabled(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_value: str,
    enabled: int,
) -> int:
    target = canonical_work_id(target_value) if target_type == "paper" else target_value
    if not target:
        return 0
    cursor = conn.execute(
        """
        UPDATE watch_targets
        SET enabled = ?
        WHERE target_type = ? AND target_value = ?
        """,
        (int(enabled), target_type, target),
    )
    return int(cursor.rowcount)


def remove_watch_target(
    conn: sqlite3.Connection,
    *,
    target_type: str,
    target_value: str,
) -> int:
    target = canonical_work_id(target_value) if target_type == "paper" else target_value
    if not target:
        return 0
    cursor = conn.execute(
        """
        DELETE FROM watch_targets
        WHERE target_type = ? AND target_value = ?
        """,
        (target_type, target),
    )
    return int(cursor.rowcount)


def list_watch_targets(
    conn: sqlite3.Connection,
    *,
    target_type: str = "paper",
    include_disabled: bool = False,
    enabled: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    base_sql = """
        SELECT id, target_type, target_value, enabled, last_check_date, note
        FROM watch_targets
        WHERE target_type = ?
    """
    params: list[Any] = [target_type]
    if enabled is not None:
        base_sql += " AND enabled = ?"
        params.append(int(enabled))
    elif not include_disabled:
        base_sql += " AND enabled = 1"
    base_sql += " ORDER BY id ASC"
    if limit is not None:
        base_sql += " LIMIT ?"
        params.append(int(limit))
    cursor = conn.execute(base_sql, tuple(params))
    return list(cursor.fetchall())


def update_watch_target_last_check(conn: sqlite3.Connection, target_type: str, target_value: str) -> None:
    target = canonical_work_id(target_value) if target_type == "paper" else target_value
    if not target:
        return
    conn.execute(
        """
        UPDATE watch_targets
        SET last_check_date = ?
        WHERE target_type = ? AND target_value = ?
        """,
        (date.today().isoformat(), target_type, target),
    )


def list_seed_paper_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT target_value
        FROM watch_targets
        WHERE target_type = 'paper' AND enabled = 1
        ORDER BY id ASC
        """
    ).fetchall()
    out: list[str] = []
    for row in rows:
        pid = canonical_work_id(row["target_value"])
        if pid:
            out.append(pid)
    return out


def list_papers_and_edges(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for row in conn.execute(
        """
        SELECT paper_id, title, published_date, doi, cited_by_count, journal
        FROM papers
        ORDER BY paper_id ASC
        """
    ):
        nodes.append(
            {
                "id": row["paper_id"],
                "label": row["title"] or row["paper_id"],
                "published_date": row["published_date"],
                "doi": row["doi"],
                "cited_by_count": row["cited_by_count"],
                "journal": row["journal"],
            }
        )

    for row in conn.execute(
        """
        SELECT src_paper_id, dst_paper_id, relation
        FROM edges
        ORDER BY id ASC
        """
    ):
        edges.append(
            {
                "source": row["src_paper_id"],
                "target": row["dst_paper_id"],
                "relation": row["relation"],
            }
        )

    return nodes, edges
