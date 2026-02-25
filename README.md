# papermaps

A practical OpenAlex-based literature mapping workspace for:

- building citation maps from seed papers (DOI list),
- tracking newly published papers that cite watched works,
- monitoring semantically related papers and sending alerts.

## Current Status

- Initial roadmap document is available in `docs/`.
- One extracted sample record is included:
  - `openalex_doi_bbae583_extracted.json`

## Project Structure

- `docs/`: planning and implementation documents
- `openalex_doi_bbae583_extracted.json`: sample extracted metadata

## Roadmap

See:

- `docs/openalex_roadmap_2026-02-25_101059.md`

Planned phases:

1. Project skeleton + config + SQLite schema
2. DOI ingestion + citation graph expansion (MVP)
3. Latest citation tracking (incremental)
4. Semantic monitoring + notifications
5. Reliability, scheduling, and reporting

## Next Step

Implement Phase 0 and Phase 1 first:

- initialize code layout (`src/`, config loader, logging),
- create database schema (`papers`, `edges`, `watch_targets`, `alerts`, `runs`),
- support DOI ingestion and depth-limited graph export.
