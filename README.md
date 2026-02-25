# papermaps

A practical OpenAlex-based literature mapping workspace for:

- building citation maps from seed papers (DOI list),
- tracking newly published papers that cite watched works,
- monitoring semantically related papers and sending alerts.

## Current Status

The repository now has a usable MVP command flow:

1. initialize database schema
2. ingest seed papers by DOI
3. expand reference edges
4. track latest citing papers
5. export graph to JSON/GEXF/HTML

## Project Structure

- `src/cli.py`: command entrypoint
- `src/db_init.py`: schema setup + run tracking
- `src/openalex_client.py`: OpenAlex API client
- `src/storage.py`: persistence helpers
- `src/graph_export.py`: graph export writers
- `config.example.yaml`: runtime config template
- `docs/`: planning and development records

## Configuration

`config.example.yaml` supports:

- database path
- logging level
- OpenAlex API settings (`api_key`, `mailto`, pagination, retry)

`api_key` can use environment variable substitution:

```bash
set OPENALEX_API_KEY=your_key_here
```

## CLI Commands

Initialize DB (idempotent):

```bash
python src/cli.py init-db --db-path data/papermap.db
```

Smoke check (config + run record):

```bash
python src/cli.py smoke-run --config config.example.yaml --db-path data/papermap.db
```

Ingest seed papers:

```bash
python src/cli.py ingest-dois --config config.example.yaml --db-path data/papermap.db --doi 10.1093/bib/bbae583
```

Expand reference graph:

```bash
python src/cli.py expand-references --config config.example.yaml --db-path data/papermap.db --depth 1 --max-nodes 200
```

Track latest citations for watched papers:

```bash
python src/cli.py track-citations --config config.example.yaml --db-path data/papermap.db --max-pages-per-target 1
```

Lightweight scheduler loop (manual process, not daemon):

```bash
python src/cli.py run-scheduler --config config.example.yaml --db-path data/papermap.db --iterations 2 --interval-seconds 0 --dry-run
```

Generate markdown summary report from database:

```bash
python src/cli.py report-summary --db-path data/papermap.db --out-file outputs/summary.md --recent-runs 10
```

Export graph files:

```bash
python src/cli.py export-graph --db-path data/papermap.db --out-dir outputs --prefix papermap --formats json,gexf,html
```

## Example Run Output

For a seed DOI `10.1093/bib/bbae583`, the MVP pipeline generated:

- `papers`: 71
- `edges`: 70
- edge types: `references=58`, `cites=12`
- exports:
  - `outputs/*.json`
  - `outputs/*.gexf`
  - `outputs/*.html`

## Roadmap Docs

- `docs/openalex_roadmap_2026-02-25_101059.md`
- `docs/development_standard_2026-02-25_112707.md`
- `docs/dev_record_2026-02-25_111821_phase0_step2.md`

## Automated Checks

Run command-level regression checks (no extra dependencies):

```bash
python -m unittest tests/test_cli_regression.py -v
```

Covered checks:

1. `init-db` idempotency
2. `smoke-run` success path (`runs` status)
3. `ingest-dois` invalid DOI failure path
4. `run-scheduler` dry-run success path
5. `run-scheduler` invalid iterations failure path
6. `report-summary` markdown generation path
7. `report-summary` missing DB failure path
