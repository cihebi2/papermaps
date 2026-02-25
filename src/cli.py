from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

try:
    from .config_loader import config_get, load_config, resolve_db_path
    from .db_init import create_run, finish_run, init_db
    from .graph_export import export_graph_gexf, export_graph_html, export_graph_json
    from .openalex_client import OpenAlexClient, canonical_work_id
    from .storage import (
        add_edge,
        add_watch_target,
        connect,
        ensure_paper_stub,
        list_papers_and_edges,
        list_seed_paper_ids,
        list_watch_targets,
        set_watch_target_enabled,
        update_watch_target_last_check,
        upsert_work,
    )
except ImportError:
    from config_loader import config_get, load_config, resolve_db_path
    from db_init import create_run, finish_run, init_db
    from graph_export import export_graph_gexf, export_graph_html, export_graph_json
    from openalex_client import OpenAlexClient, canonical_work_id
    from storage import (
        add_edge,
        add_watch_target,
        connect,
        ensure_paper_stub,
        list_papers_and_edges,
        list_seed_paper_ids,
        list_watch_targets,
        set_watch_target_enabled,
        update_watch_target_last_check,
        upsert_work,
    )

LOGGER = logging.getLogger(__name__)
DOI_REGEX = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="papermaps command line tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="Initialize SQLite database schema")
    init_db_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")

    smoke_run_parser = subparsers.add_parser(
        "smoke-run",
        help="Load config and write a smoke run record into runs table",
    )
    smoke_run_parser.add_argument("--config", required=True, help="Config file path")
    smoke_run_parser.add_argument("--db-path", default=None, help="SQLite file path override")

    ingest_parser = subparsers.add_parser("ingest-dois", help="Ingest seed papers by DOI via OpenAlex")
    ingest_parser.add_argument("--config", required=True, help="Config file path")
    ingest_parser.add_argument("--db-path", default=None, help="SQLite file path override")
    ingest_parser.add_argument("--doi", action="append", default=[], help="DOI (repeatable)")
    ingest_parser.add_argument("--doi-file", default=None, help="Text file containing DOI list")
    ingest_parser.add_argument("--no-watch", action="store_true", help="Do not add ingested papers to watch targets")

    expand_parser = subparsers.add_parser("expand-references", help="Expand reference graph from seed papers")
    expand_parser.add_argument("--config", required=True, help="Config file path")
    expand_parser.add_argument("--db-path", default=None, help="SQLite file path override")
    expand_parser.add_argument("--depth", type=int, default=1, help="Expansion depth from start papers")
    expand_parser.add_argument("--max-nodes", type=int, default=200, help="Maximum nodes to discover in this run")
    expand_parser.add_argument("--max-refs-per-paper", type=int, default=200, help="Reference cap per source paper")
    expand_parser.add_argument("--start-id", action="append", default=[], help="Start OpenAlex work id (repeatable)")

    track_parser = subparsers.add_parser("track-citations", help="Track latest papers citing watched papers")
    track_parser.add_argument("--config", required=True, help="Config file path")
    track_parser.add_argument("--db-path", default=None, help="SQLite file path override")
    track_parser.add_argument("--from-date", default=None, help="Override from_publication_date (YYYY-MM-DD)")
    track_parser.add_argument("--lookback-days", type=int, default=30, help="Fallback window when watch has no cursor")
    track_parser.add_argument("--max-pages-per-target", type=int, default=3, help="OpenAlex pages per target paper")
    track_parser.add_argument("--dry-run", action="store_true", help="Validate tracking loop without API calls")

    scheduler_parser = subparsers.add_parser(
        "run-scheduler",
        help="Run citation tracking in a lightweight loop",
    )
    scheduler_parser.add_argument("--config", required=True, help="Config file path")
    scheduler_parser.add_argument("--db-path", default=None, help="SQLite file path override")
    scheduler_parser.add_argument("--iterations", type=int, default=1, help="Number of cycles to run")
    scheduler_parser.add_argument("--interval-seconds", type=int, default=300, help="Sleep between cycles")
    scheduler_parser.add_argument("--dry-run", action="store_true", help="Validate loop without calling OpenAlex")
    scheduler_parser.add_argument("--from-date", default=None, help="Forwarded to track-citations")
    scheduler_parser.add_argument("--lookback-days", type=int, default=30, help="Forwarded to track-citations")
    scheduler_parser.add_argument(
        "--max-pages-per-target",
        type=int,
        default=3,
        help="Forwarded to track-citations",
    )

    add_watch_parser = subparsers.add_parser("add-watch-target", help="Add a watch target")
    add_watch_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")
    add_watch_parser.add_argument("--target-type", default="paper", help="Target type (default: paper)")
    add_watch_parser.add_argument("--target-value", required=True, help="Target value (paper uses OpenAlex W id)")
    add_watch_parser.add_argument("--note", default=None, help="Optional note")
    add_watch_parser.add_argument("--enabled", type=int, choices=[0, 1], default=1, help="Enabled flag")

    list_watch_parser = subparsers.add_parser("list-watch-targets", help="List watch targets")
    list_watch_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")
    list_watch_parser.add_argument("--target-type", default="paper", help="Target type")
    list_watch_parser.add_argument("--include-disabled", action="store_true", help="Include disabled watch targets")
    list_watch_parser.add_argument("--limit", type=int, default=100, help="Max rows to show")
    list_watch_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    set_watch_parser = subparsers.add_parser("set-watch-enabled", help="Enable or disable an existing watch target")
    set_watch_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")
    set_watch_parser.add_argument("--target-type", default="paper", help="Target type")
    set_watch_parser.add_argument("--target-value", required=True, help="Target value")
    set_watch_parser.add_argument("--enabled", type=int, choices=[0, 1], required=True, help="Enabled flag")

    report_parser = subparsers.add_parser("report-summary", help="Generate a markdown summary report from database")
    report_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")
    report_parser.add_argument("--out-file", default=None, help="Output markdown file path")
    report_parser.add_argument("--recent-runs", type=int, default=10, help="How many latest runs to include")
    report_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Summary output format",
    )
    report_parser.add_argument(
        "--status-filter",
        choices=["success", "failed", "running"],
        default=None,
        help="Filter recent runs by status",
    )
    report_parser.add_argument(
        "--job-name-filter",
        default=None,
        help="Filter recent runs by job name",
    )
    report_parser.add_argument(
        "--max-detail-length",
        type=int,
        default=200,
        help="Maximum detail length in report output",
    )

    export_parser = subparsers.add_parser("export-graph", help="Export paper graph from database")
    export_parser.add_argument("--db-path", default="data/papermap.db", help="SQLite file path")
    export_parser.add_argument("--out-dir", default="outputs", help="Output directory")
    export_parser.add_argument("--prefix", default="papermap", help="Output filename prefix")
    export_parser.add_argument(
        "--formats",
        default="json,gexf,html",
        help="Comma-separated formats: json,gexf,html",
    )
    return parser


def _json_stats(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _mark_run_failed(db_path: Path, run_id: int, error_message: str) -> None:
    try:
        finish_run(db_path, run_id, "failed", detail=error_message)
    except Exception:
        LOGGER.exception("failed to update run status to failed run_id=%s", run_id)


def _build_openalex_client(config: dict[str, Any]) -> OpenAlexClient:
    config_api_key = config_get(config, "openalex", "api_key", "")
    api_key = str(config_api_key).strip() if config_api_key is not None else ""
    if not api_key:
        api_key = os.getenv("OPENALEX_API_KEY", "")
    mailto_cfg = config_get(config, "openalex", "mailto", "")
    base_url_cfg = config_get(config, "openalex", "base_url", "https://api.openalex.org")
    per_page_cfg = config_get(config, "openalex", "per_page", 200)
    sleep_cfg = config_get(config, "openalex", "sleep", 0.1)
    timeout_cfg = config_get(config, "openalex", "timeout_s", 30)
    retries_cfg = config_get(config, "openalex", "max_retries", 3)
    return OpenAlexClient(
        api_key=str(api_key).strip() or None,
        mailto=str(mailto_cfg).strip() or None,
        base_url=str(base_url_cfg),
        per_page=int(per_page_cfg),
        sleep_s=float(sleep_cfg),
        timeout_s=int(timeout_cfg),
        max_retries=int(retries_cfg),
    )


def _parse_doi(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if text.lower().startswith("https://doi.org/"):
        text = text[len("https://doi.org/") :]
    if text.lower().startswith("doi:"):
        text = text[4:]
    text = text.strip()
    if DOI_REGEX.fullmatch(text):
        return text.lower()
    return None


def _collect_dois(doi_args: list[str], doi_file: str | None) -> list[str]:
    collected: list[str] = []
    for item in doi_args:
        doi = _parse_doi(item)
        if doi:
            collected.append(doi)
    if doi_file:
        file_path = Path(doi_file)
        if not file_path.exists():
            raise FileNotFoundError(f"DOI file not found: {file_path}")
        for raw in file_path.read_text(encoding="utf-8").splitlines():
            for piece in raw.split(","):
                doi = _parse_doi(piece)
                if doi:
                    collected.append(doi)
    return sorted(set(collected))


def run_smoke(config_path: Path, db_path_arg: str | None) -> int:
    LOGGER.info("smoke-run start config=%s db_path_override=%s", config_path, db_path_arg)
    run_id: int | None = None
    db_path: Path | None = None
    try:
        config = load_config(config_path)
        db_path = resolve_db_path(db_path_arg, config)
        init_db(db_path)
        run_id = create_run(
            db_path=db_path,
            job_name="smoke-run",
            detail=f"config={config_path};db={db_path}",
        )
        finish_run(db_path, run_id, "success", "smoke-run completed", _json_stats({"ok": True}))
        LOGGER.info("smoke-run success run_id=%s db_path=%s", run_id, db_path)
        return 0
    except (FileNotFoundError, ValueError) as exc:
        if run_id is not None and db_path is not None:
            _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.error("smoke-run failed: %s", exc)
        return 1
    except Exception as exc:
        if run_id is not None and db_path is not None:
            _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.exception("smoke-run unexpected failure")
        return 1


def ingest_dois(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    db_path = resolve_db_path(args.db_path, config)
    init_db(db_path)
    run_id = create_run(db_path, "ingest-dois", detail=f"config={args.config}")

    client = _build_openalex_client(config)
    dois = _collect_dois(args.doi, args.doi_file)
    if not dois:
        _mark_run_failed(db_path, run_id, "No valid DOI input")
        LOGGER.error("No valid DOI input")
        return 1

    inserted = 0
    failed: list[str] = []
    status = "failed"
    detail = ""
    stats_json = ""
    try:
        with connect(db_path) as conn:
            for doi in dois:
                try:
                    work = client.get_work_by_doi(doi)
                    if not work:
                        failed.append(doi)
                        LOGGER.warning("DOI not found in OpenAlex doi=%s", doi)
                        continue
                    paper_id = upsert_work(conn, work, source="ingest-dois")
                    if not args.no_watch:
                        add_watch_target(conn, target_type="paper", target_value=paper_id, note="seed")
                    inserted += 1
                except Exception as exc:
                    failed.append(doi)
                    LOGGER.warning("ingest-dois failed doi=%s error=%s", doi, exc)
        stats = {"input_count": len(dois), "inserted": inserted, "failed": len(failed), "failed_doi": failed}
        status = "success" if inserted > 0 else "failed"
        detail = f"inserted={inserted};failed={len(failed)}"
        stats_json = _json_stats(stats)
        finish_run(
            db_path,
            run_id,
            status,
            detail=detail,
            stats_json=stats_json,
        )
        LOGGER.info("ingest-dois done inserted=%s failed=%s", inserted, len(failed))
        return 0 if inserted > 0 else 1
    except Exception as exc:
        _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.exception("ingest-dois unexpected failure")
        return 1


def _resolve_start_papers(args: argparse.Namespace, db_path: Path) -> list[str]:
    provided = [canonical_work_id(item) for item in args.start_id]
    provided_ids = [pid for pid in provided if pid]
    if provided_ids:
        return provided_ids
    with connect(db_path) as conn:
        return list_seed_paper_ids(conn)


def expand_references(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    db_path = resolve_db_path(args.db_path, config)
    init_db(db_path)
    run_id = create_run(
        db_path,
        "expand-references",
        detail=f"depth={args.depth};max_nodes={args.max_nodes};config={args.config}",
    )

    start_ids = _resolve_start_papers(args, db_path)
    if not start_ids:
        _mark_run_failed(db_path, run_id, "No start papers available")
        LOGGER.error("No start papers available. Ingest seeds first or pass --start-id.")
        return 1

    client = _build_openalex_client(config)
    queue = deque((pid, 0) for pid in start_ids)
    expanded: set[str] = set()
    discovered: set[str] = set(start_ids)

    fetched_works = 0
    edge_attempts = 0
    try:
        with connect(db_path) as conn:
            while queue and len(discovered) <= int(args.max_nodes):
                paper_id, depth = queue.popleft()
                if paper_id in expanded:
                    continue
                expanded.add(paper_id)
                try:
                    work = client.get_work_by_id(paper_id)
                except Exception as exc:
                    LOGGER.warning("expand-references fetch failed paper_id=%s error=%s", paper_id, exc)
                    continue

                fetched_works += 1
                src_id = upsert_work(conn, work, source="expand-references")

                if depth >= int(args.depth):
                    continue

                refs = (work.get("referenced_works") or [])[: int(args.max_refs_per_paper)]
                for ref in refs:
                    ref_id = canonical_work_id(ref)
                    if not ref_id:
                        continue
                    ensure_paper_stub(conn, ref_id)
                    add_edge(conn, src_id, ref_id, "references", run_id=run_id)
                    edge_attempts += 1

                    if ref_id not in discovered and len(discovered) < int(args.max_nodes):
                        discovered.add(ref_id)
                        # Only fetch deeper nodes when they still need expansion.
                        if depth + 1 < int(args.depth):
                            queue.append((ref_id, depth + 1))
        stats = {
            "start_count": len(start_ids),
            "fetched_works": fetched_works,
            "expanded_nodes": len(expanded),
            "discovered_nodes": len(discovered),
            "edge_attempts": edge_attempts,
        }
        finish_run(
            db_path,
            run_id,
            "success",
            detail=f"expanded={len(expanded)};edges={edge_attempts}",
            stats_json=_json_stats(stats),
        )
        LOGGER.info("expand-references done expanded=%s edge_attempts=%s", len(expanded), edge_attempts)
        return 0
    except Exception as exc:
        _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.exception("expand-references unexpected failure")
        return 1


def _fallback_from_date(lookback_days: int) -> str:
    return (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()


def track_citations(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    db_path = resolve_db_path(args.db_path, config)
    init_db(db_path)
    run_id = create_run(
        db_path,
        "track-citations",
        detail=f"from_date={args.from_date};lookback_days={args.lookback_days};config={args.config}",
    )
    client = _build_openalex_client(config)

    total_citing = 0
    target_count = 0
    try:
        with connect(db_path) as conn:
            targets = list_watch_targets(conn, target_type="paper")
            if not targets:
                _mark_run_failed(db_path, run_id, "No watch targets found")
                LOGGER.error("No watch targets found. Run ingest-dois first.")
                return 1

            for target in targets:
                target_id = str(target["target_value"])
                from_date = args.from_date or target["last_check_date"] or _fallback_from_date(args.lookback_days)
                target_count += 1
                try:
                    if bool(args.dry_run):
                        LOGGER.info(
                            "track-citations dry-run target=%s from_date=%s skip API call",
                            target_id,
                            from_date,
                        )
                    else:
                        works = client.iter_citing_works(
                            target_id,
                            from_publication_date=from_date,
                            max_pages=int(args.max_pages_per_target),
                        )
                        for work in works:
                            src_id = upsert_work(conn, work, source="track-citations")
                            add_edge(conn, src_id, target_id, "cites", run_id=run_id)
                            total_citing += 1
                        update_watch_target_last_check(conn, "paper", target_id)
                except Exception as exc:
                    LOGGER.warning("track-citations failed target=%s error=%s", target_id, exc)
        stats = {"target_count": target_count, "citing_papers_processed": total_citing}
        finish_run(
            db_path,
            run_id,
            "success",
            detail=f"targets={target_count};citing_processed={total_citing}",
            stats_json=_json_stats(stats),
        )
        LOGGER.info("track-citations done targets=%s citing_processed=%s", target_count, total_citing)
        return 0
    except Exception as exc:
        _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.exception("track-citations unexpected failure")
        return 1


def run_scheduler(args: argparse.Namespace) -> int:
    if int(args.iterations) <= 0:
        LOGGER.error("run-scheduler invalid iterations=%s (must be > 0)", args.iterations)
        return 1
    if int(args.interval_seconds) < 0:
        LOGGER.error("run-scheduler invalid interval-seconds=%s (must be >= 0)", args.interval_seconds)
        return 1

    config = load_config(Path(args.config))
    db_path = resolve_db_path(args.db_path, config)
    init_db(db_path)

    run_id = create_run(
        db_path,
        "run-scheduler",
        detail=(
            f"iterations={args.iterations};interval_seconds={args.interval_seconds};"
            f"dry_run={args.dry_run};config={args.config}"
        ),
    )

    completed = 0
    failures = 0
    try:
        for idx in range(int(args.iterations)):
            cycle = idx + 1
            LOGGER.info(
                "run-scheduler cycle start cycle=%s/%s dry_run=%s",
                cycle,
                args.iterations,
                bool(args.dry_run),
            )
            if args.dry_run:
                LOGGER.info("run-scheduler dry-run cycle=%s skip track-citations", cycle)
            else:
                track_args = argparse.Namespace(
                    config=args.config,
                    db_path=str(db_path),
                    from_date=args.from_date,
                    lookback_days=int(args.lookback_days),
                    max_pages_per_target=int(args.max_pages_per_target),
                    dry_run=False,
                )
                rc = track_citations(track_args)
                if rc != 0:
                    failures += 1
                    LOGGER.warning("run-scheduler cycle=%s track-citations failed rc=%s", cycle, rc)
            completed += 1
            if cycle < int(args.iterations) and int(args.interval_seconds) > 0:
                LOGGER.info("run-scheduler sleeping seconds=%s", args.interval_seconds)
                time.sleep(int(args.interval_seconds))

        status = "success" if failures == 0 else "failed"
        detail = f"completed={completed};failures={failures};dry_run={bool(args.dry_run)}"
        finish_run(
            db_path,
            run_id,
            status,
            detail=detail,
            stats_json=_json_stats(
                {
                    "iterations": int(args.iterations),
                    "interval_seconds": int(args.interval_seconds),
                    "completed": completed,
                    "failures": failures,
                    "dry_run": bool(args.dry_run),
                }
            ),
        )
        return 0 if failures == 0 else 1
    except Exception as exc:
        _mark_run_failed(db_path, run_id, str(exc))
        LOGGER.exception("run-scheduler unexpected failure")
        return 1


def add_watch_target_command(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    init_db(db_path)

    target_type = str(args.target_type).strip()
    target_value = str(args.target_value).strip()
    if not target_type:
        LOGGER.error("target-type must not be empty")
        return 1
    if not target_value:
        LOGGER.error("target-value must not be empty")
        return 1
    if target_type == "paper":
        normalized = canonical_work_id(target_value)
        if not normalized:
            LOGGER.error("Invalid paper target-value, expected OpenAlex id like W123...")
            return 1
        target_value = normalized

    try:
        with connect(db_path) as conn:
            add_watch_target(
                conn,
                target_type=target_type,
                target_value=target_value,
                note=args.note,
                enabled=int(args.enabled),
            )
        LOGGER.info(
            "add-watch-target success db_path=%s target_type=%s target_value=%s enabled=%s",
            db_path,
            target_type,
            target_value,
            int(args.enabled),
        )
        return 0
    except Exception:
        LOGGER.exception("add-watch-target failed db_path=%s", db_path)
        return 1


def list_watch_targets_command(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    if not db_path.exists():
        LOGGER.error("Database file does not exist: %s", db_path)
        return 1
    if int(args.limit) <= 0:
        LOGGER.error("Invalid --limit=%s (must be > 0)", args.limit)
        return 1

    try:
        with connect(db_path) as conn:
            rows = list_watch_targets(
                conn,
                target_type=str(args.target_type),
                include_disabled=bool(args.include_disabled),
                limit=int(args.limit),
            )
        payload = [
            {
                "id": int(row["id"]),
                "target_type": row["target_type"],
                "target_value": row["target_value"],
                "enabled": int(row["enabled"]),
                "last_check_date": row["last_check_date"],
                "note": row["note"],
            }
            for row in rows
        ]

        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if not payload:
                print("(none)")
            else:
                for row in payload:
                    print(
                        f"#{row['id']} type={row['target_type']} value={row['target_value']} "
                        f"enabled={row['enabled']} last_check={row['last_check_date'] or '-'} note={row['note'] or '-'}"
                    )
        return 0
    except Exception:
        LOGGER.exception("list-watch-targets failed db_path=%s", db_path)
        return 1


def set_watch_enabled_command(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    if not db_path.exists():
        LOGGER.error("Database file does not exist: %s", db_path)
        return 1

    target_type = str(args.target_type).strip()
    target_value = str(args.target_value).strip()
    if target_type == "paper":
        normalized = canonical_work_id(target_value)
        if not normalized:
            LOGGER.error("Invalid paper target-value, expected OpenAlex id like W123...")
            return 1
        target_value = normalized

    try:
        with connect(db_path) as conn:
            changed = set_watch_target_enabled(
                conn,
                target_type=target_type,
                target_value=target_value,
                enabled=int(args.enabled),
            )
        if changed == 0:
            LOGGER.error("Watch target not found target_type=%s target_value=%s", target_type, target_value)
            return 1
        LOGGER.info(
            "set-watch-enabled success target_type=%s target_value=%s enabled=%s",
            target_type,
            target_value,
            int(args.enabled),
        )
        return 0
    except Exception:
        LOGGER.exception("set-watch-enabled failed db_path=%s", db_path)
        return 1


def report_summary(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    if not db_path.exists():
        LOGGER.error("Database file does not exist: %s", db_path)
        return 1
    if int(args.recent_runs) <= 0:
        LOGGER.error("Invalid --recent-runs=%s (must be > 0)", args.recent_runs)
        return 1
    if int(args.max_detail_length) <= 0:
        LOGGER.error("Invalid --max-detail-length=%s (must be > 0)", args.max_detail_length)
        return 1

    if args.out_file:
        out_file = Path(args.out_file)
    else:
        ext = "md" if args.format == "markdown" else "json"
        out_file = Path("outputs") / f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with connect(db_path) as conn:
            papers_count = int(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
            edges_count = int(conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
            watches_count = int(conn.execute("SELECT COUNT(*) FROM watch_targets").fetchone()[0])
            runs_count = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

            relation_rows = conn.execute(
                "SELECT relation, COUNT(*) AS cnt FROM edges GROUP BY relation ORDER BY relation"
            ).fetchall()
            if args.status_filter and args.job_name_filter:
                run_rows = conn.execute(
                    """
                    SELECT id, job_name, status, started_at, finished_at, detail
                    FROM runs
                    WHERE status = ? AND job_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (args.status_filter, args.job_name_filter, int(args.recent_runs)),
                ).fetchall()
            elif args.status_filter:
                run_rows = conn.execute(
                    """
                    SELECT id, job_name, status, started_at, finished_at, detail
                    FROM runs
                    WHERE status = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (args.status_filter, int(args.recent_runs)),
                ).fetchall()
            elif args.job_name_filter:
                run_rows = conn.execute(
                    """
                    SELECT id, job_name, status, started_at, finished_at, detail
                    FROM runs
                    WHERE job_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (args.job_name_filter, int(args.recent_runs)),
                ).fetchall()
            else:
                run_rows = conn.execute(
                    """
                    SELECT id, job_name, status, started_at, finished_at, detail
                    FROM runs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(args.recent_runs),),
                ).fetchall()

        def truncate_detail(value: str | None) -> str | None:
            if value is None:
                return None
            text = str(value)
            max_len = int(args.max_detail_length)
            if len(text) <= max_len:
                return text
            return text[:max_len]

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "database": str(db_path),
            "counts": {
                "papers": papers_count,
                "edges": edges_count,
                "watch_targets": watches_count,
                "runs": runs_count,
            },
            "edge_relations": [{"relation": row["relation"], "count": int(row["cnt"])} for row in relation_rows],
            "recent_runs": [
                {
                    "id": int(row["id"]),
                    "job_name": row["job_name"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "detail": truncate_detail(row["detail"]),
                }
                for row in run_rows
            ],
            "recent_runs_limit": int(args.recent_runs),
            "status_filter": args.status_filter,
            "job_name_filter": args.job_name_filter,
            "max_detail_length": int(args.max_detail_length),
        }

        if args.format == "json":
            out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            lines: list[str] = []
            lines.append("# Papermap Summary Report")
            lines.append("")
            lines.append(f"- Generated at: {payload['generated_at']}")
            lines.append(f"- Database: `{db_path}`")
            if args.status_filter:
                lines.append(f"- Status filter: `{args.status_filter}`")
            if args.job_name_filter:
                lines.append(f"- Job filter: `{args.job_name_filter}`")
            lines.append("")
            lines.append("## Counts")
            lines.append(f"- papers: `{papers_count}`")
            lines.append(f"- edges: `{edges_count}`")
            lines.append(f"- watch_targets: `{watches_count}`")
            lines.append(f"- runs: `{runs_count}`")
            lines.append("")
            lines.append("## Edge Relations")
            if relation_rows:
                for row in relation_rows:
                    lines.append(f"- {row['relation']}: `{row['cnt']}`")
            else:
                lines.append("- (none)")
            lines.append("")
            lines.append(f"## Recent Runs (last {int(args.recent_runs)})")
            if run_rows:
                for row in run_rows:
                    lines.append(
                        f"- #{row['id']} `{row['job_name']}` `{row['status']}` "
                        f"start={row['started_at']} end={row['finished_at'] or '-'} detail={row['detail'] or '-'}"
                    )
            else:
                lines.append("- (none)")
            lines.append("")
            out_file.write_text("\n".join(lines), encoding="utf-8")

        LOGGER.info("report-summary wrote %s", out_file)
        return 0
    except Exception:
        LOGGER.exception("report-summary failed db_path=%s", db_path)
        return 1


def export_graph(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    if not db_path.exists():
        LOGGER.error("Database file does not exist: %s", db_path)
        return 1

    out_dir = Path(args.out_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = str(args.prefix).strip() or "papermap"
    formats = {item.strip().lower() for item in str(args.formats).split(",") if item.strip()}

    with connect(db_path) as conn:
        nodes, edges = list_papers_and_edges(conn)
    LOGGER.info("export-graph source nodes=%s edges=%s", len(nodes), len(edges))

    outputs: list[Path] = []
    if "json" in formats:
        outputs.append(export_graph_json(nodes, edges, out_dir / f"{prefix}_{ts}.json"))
    if "gexf" in formats:
        outputs.append(export_graph_gexf(nodes, edges, out_dir / f"{prefix}_{ts}.gexf"))
    if "html" in formats:
        outputs.append(export_graph_html(nodes, edges, out_dir / f"{prefix}_{ts}.html"))

    if not outputs:
        LOGGER.error("No valid export format selected. Use json,gexf,html")
        return 1

    for path in outputs:
        LOGGER.info("export-graph wrote %s", path)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db(Path(args.db_path))
        return 0
    if args.command == "smoke-run":
        return run_smoke(Path(args.config), args.db_path)
    if args.command == "ingest-dois":
        return ingest_dois(args)
    if args.command == "expand-references":
        return expand_references(args)
    if args.command == "track-citations":
        return track_citations(args)
    if args.command == "run-scheduler":
        return run_scheduler(args)
    if args.command == "add-watch-target":
        return add_watch_target_command(args)
    if args.command == "list-watch-targets":
        return list_watch_targets_command(args)
    if args.command == "set-watch-enabled":
        return set_watch_enabled_command(args)
    if args.command == "report-summary":
        return report_summary(args)
    if args.command == "export-graph":
        return export_graph(args)

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
