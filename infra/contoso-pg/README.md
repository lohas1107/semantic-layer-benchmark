# Contoso on PostgreSQL

PostgreSQL 17 images of the Contoso retail data warehouse, built for the
PostgreSQL port of this benchmark.

| Tag | Schema | Content |
|---|---|---|
| `lohas1107/contoso:pg-cube-1.0.0` | `retail` | The data the Cube paper used (25 tables, same columns as `fixtures/retail_schema.sql`). Used by the benchmark. |
| `lohas1107/contoso:pg-1.0.0` | `dbo` | Microsoft's original ContosoRetailDW, values unchanged. |

Both are built from one `Dockerfile` with `--build-arg VARIANT=cube|original`.

## Sources

- **cube**: Kaggle [`bhanuthakurr/cleaned-contoso-dataset`](https://www.kaggle.com/datasets/bhanuthakurr/cleaned-contoso-dataset).
  The Cube paper (arXiv 2604.25149, section 4.1) names it as the data it loaded
  into ClickHouse. Cube published no conversion code of its own.
- **original**: Hugging Face [`jocapc/ContosoRetailsDW`](https://huggingface.co/datasets/jocapc/ContosoRetailsDW)
  at revision `f3ec01ed`, which is Microsoft's Synapse-tutorial export of ContosoRetailDW.
  Columns and types come from Microsoft's official load script in `source/`
  ([microsoft/sql-server-samples](https://github.com/microsoft/sql-server-samples/tree/master/samples/databases/contoso-data-warehouse),
  MIT). Checksums are pinned in `jocapc.sha256`.

## How the data is converted

`load_kaggle.py` streams each CSV from the Kaggle zip into `COPY`, without
extracting it. It reproduces how ClickHouse read the same files:

- Unquoted fields are trimmed of spaces and tabs (`input_format_csv_trim_whitespaces`).
- An empty field is NULL in a Nullable column and `''` in a non-Nullable String column.

Without the trimming, values such as `'Cameras and camcorders '` keep a trailing
space and 19 answers change. Identifiers are unquoted, so PostgreSQL stores them
in lower case, and `SELECT ProductKey FROM retail.DimProduct` works as written.

`load_jocapc.py` maps only the SQL Server types:

- `money` → `numeric(19,4)`
- `float` → `double precision`
- `datetime` → `timestamp(3)`
- `bit` → `boolean`

The values themselves are loaded unchanged.

`tune.sql` adds settings to both variants:

- Expression statistics on `EXTRACT`, `date_part` and `date_trunc` of each fact
  table's `DateKey`. Without them, year filters are misestimated about 100×, and
  q88 runs for more than 120 s instead of 3 s.
- `jit = off`.
- `temp_file_limit = 4GB`.

The database uses the C collation, so text sorts the same way as in ClickHouse.

## Verification

`docker/contoso/verify_pg_dataset.py` runs the 100 PostgreSQL reference queries in
`text_to_sql_benchmark.pg.json` and compares them with the paper's ClickHouse
results in `published-runs/paper-2026-04-22/reference/`.

The cube variant matches on 98 of 100 queries. Some of those matches are tie
orders under `ORDER BY ... LIMIT`; those queries are marked `@ties` and compare
only the sort keys. The two known deviations are:

- **q33**: The paper excluded it after it exceeded ClickHouse's memory quota.
  Here it hits `temp_file_limit` instead.
- **q44**: The first year's growth is NULL instead of `inf`. PostgreSQL raises an
  error on float division by zero, so the query uses `nullif`.

## Build

```bash
# Kaggle needs a login: download the dataset zip manually first.
./build.sh dumps /path/to/archive.zip   # load + verify + dist/contoso-{original,cube}.dump
./build.sh images                       # docker build both tags
./build.sh push                         # public: check the data licences first
```

## Run (benchmark)

```bash
docker compose -f docker/contoso/compose.yaml up -d --wait
# harness: PG_DSN=postgresql://bench_ro:bench_ro@127.0.0.1:5433/contoso
# Open WebUI tool valve: postgresql://bench_ro:bench_ro@contoso-pg:5432/contoso
```

`bench_ro` is a read-only role with a 60 s statement timeout and
`search_path = <schema>, public`.
