# Papermap Development Record (2026-02-25_165047)

## 1. Round Goal

Add a minimal automated regression gate for the current CLI MVP, without introducing new dependencies.

## 2. Scope

Implemented:

- command-level tests with `unittest` + `subprocess`
- coverage for happy path and boundary/error path
- README update with verification command

Out of scope:

- no business feature expansion
- no API behavior change
- no new dependency

## 3. Test Cases Added

1. `init-db` idempotency:
- run init twice, both return code 0
- verify target tables exist

2. `smoke-run` success:
- run smoke command with temp config and temp db
- verify latest run record status is `success`

3. `ingest-dois` invalid input failure:
- run with invalid DOI
- expect non-zero return code and latest run status `failed`

## 4. Files Changed

- `tests/test_cli_regression.py` (new)
- `tests/__init__.py` (new)
- `README.md` (updated with test command)
- `docs/dev_record_2026-02-25_165047_phase0_step3_tests.md` (new)

## 5. Validation Result

Command:

`python -m unittest tests/test_cli_regression.py -v`

Result:

- Ran 3 tests
- All passed (`OK`)

## 6. Next Small Step

Add one minimal scheduling entrypoint (manual interval runner) for `track-citations` dry-run orchestration, while keeping CLI-first architecture.
