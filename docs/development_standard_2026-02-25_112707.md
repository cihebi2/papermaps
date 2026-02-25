# Papermap Development Standard and Record

- Timestamp: 2026-02-25 11:27:07
- Scope: project-level execution standard + completed step records
- Status: active baseline for upcoming iterations

## 1. Execution Standard (must follow every round)

1. One smallest meaningful step per round.
2. Output DoD, assumptions/risks, TODO with paths, and test plan before coding.
3. Keep change scope strictly related to current step.
4. Ensure each step is testable, reviewable, reversible.
5. Do not fake test results; report actual command outcomes.
6. Do not introduce new dependencies unless explicitly justified and approved.
7. If interface changes, document contract (input/output/error/boundary/idempotency).
8. Add key-path logs: start, success, failure.
9. Use versioned commit notes and milestone tags.

## 2. Timeline Record

1. 2026-02-25 10:10:59
- Created initial roadmap:
  - `docs/openalex_roadmap_2026-02-25_101059.md`
- Defined phased delivery:
  - Phase 0/1 -> Phase 2 -> Phase 3/4.

2. 2026-02-25 10:26:22
- Added project codex memory:
  - `AGENTS.md`
- Established mandatory 10-section reporting format.

3. 2026-02-25 10:31 ~ 10:38
- Completed Phase 0 Step 1:
  - Minimal skeleton (`src/`, `data/`, `config.example.yaml`)
  - Idempotent `init-db` command
  - SQLite base schema (`papers`, `edges`, `watch_targets`, `alerts`, `runs`)

4. 2026-02-25 11:18:21
- Completed Phase 0 Step 2:
  - `smoke-run` CLI command
  - minimal config loading
  - `runs` status transition: `running -> success/failed`
  - timestamped step doc:
    - `docs/dev_record_2026-02-25_111821_phase0_step2.md`

5. 2026-02-25 11:51:30
- Completed Phase 1 MVP baseline:
  - `ingest-dois` / `expand-references` / `track-citations` / `export-graph`
  - OpenAlex client + storage + graph exporters
  - validated end-to-end on a real DOI
  - timestamped step doc:
    - `docs/dev_record_2026-02-25_115130_phase1_mvp.md`

## 3. Current Constraints

1. Out of scope until explicitly scheduled:
- OpenAlex ingestion implementation
- citation graph expansion
- notification integration

2. Current approved baseline:
- Python standard library only
- SQLite as local state store
- CLI-driven incremental workflow

## 4. Next-Step Gate

The next round should stay within one smallest step. Recommended target:

1. Phase 0 Step 3 (minimal):
- add lightweight automated command-level checks for
  - `init-db` idempotency
  - `smoke-run` success path
  - `smoke-run` missing-config failure path

Do not start Phase 1 ingestion before this gate is complete.
