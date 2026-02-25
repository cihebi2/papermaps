# Papermap Development Record (2026-02-25_115130)

## 1. Round Goal

Deliver a roughly usable MVP around OpenAlex paper mapping:

1. ingest seed papers from DOI
2. expand reference graph
3. track latest citations
4. export graph artifacts for analysis

## 2. Scope This Round

Implemented:

- multi-command CLI (`init-db`, `smoke-run`, `ingest-dois`, `expand-references`, `track-citations`, `export-graph`)
- OpenAlex API client with retry/pagination
- storage helpers for paper/edge/watch management
- graph export to JSON/GEXF/HTML
- schema migration to support richer paper metadata and tracking fields

Out of scope:

- semantic monitoring notifications
- scheduling daemon / long-running worker
- web UI backend

## 3. Key Engineering Decisions

1. Kept dependency footprint minimal (Python standard library only).
2. Added idempotent DB migrations via `ALTER TABLE` checks.
3. Used `watch_targets` as incremental tracking cursor source (`last_check_date`).
4. Exported graph files for immediate usability instead of introducing a web service first.

## 4. Issues Found and Fixed

1. SQLite locking issue in run finalization:
- cause: `finish_run` called while another write connection was still open.
- fix: close operational DB context first, then finalize run status.

2. Expansion performance at depth=1:
- cause: boundary nodes were fetched unnecessarily.
- fix: only queue nodes that still require deeper expansion.

## 5. Validation Snapshot

Validated pipeline on seed DOI `10.1093/bib/bbae583`:

- `ingest-dois`: success (1 seed paper)
- `expand-references`: success (`references=58`)
- `track-citations`: success (`cites=12` with 1-page target scan)
- `export-graph`: success (`json`, `gexf`, `html`)
- resulting graph:
  - papers: 71
  - edges: 70

## 6. Files Updated In This Round

- `src/cli.py`
- `src/db_init.py`
- `src/config_loader.py` (new)
- `src/openalex_client.py` (new)
- `src/storage.py` (new)
- `src/graph_export.py` (new)
- `config.example.yaml`
- `README.md`
- `docs/dev_record_2026-02-25_115130_phase1_mvp.md`

## 7. Next Small Step Recommendation

Add lightweight automated command-level checks (no new dependencies):

1. `init-db` idempotency check
2. `ingest-dois` single DOI happy path check
3. `smoke-run` missing config failure check
4. `export-graph` artifact existence check
