"""Load the Cube benchmark dataset into PostgreSQL (schema retail).

Data:  Kaggle bhanuthakurr/cleaned-contoso-dataset ("Cleaned Contoso Retail
       Dataset", Thakur 2023) — the exact CSVs the Cube paper loaded into
       ClickHouse (arXiv 2604.25149, section 4.1). One CSV per table, with a header.
DDL:   fixtures/retail_schema.pg.sql, generated from Cube's ClickHouse DDL.

The CSVs are streamed straight from the zip into COPY; nothing is extracted.
NULL handling mirrors ClickHouse's CSV import: an empty field is NULL in a
Nullable column and '' in a non-Nullable String column (FORCE_NOT_NULL).
DimCustomer's unnamed first column (a pandas index) is Cube's RowNum.

Usage:
  uv run python docker/contoso/load_kaggle.py --dsn postgresql://... --zip archive.zip
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_DDL = REPO_ROOT / "fixtures" / "retail_schema.pg.sql"
HEADER_ALIASES = {("DimCustomer", ""): "RowNum"}


def parse_ddl(ddl: str) -> dict[str, list[tuple[str, str, bool]]]:
    """{table: [(column, type, not_null)]} from retail_schema.pg.sql."""
    out = {}
    for table, body in re.findall(r"CREATE TABLE retail\.(\w+)\s*\((.*?)\);", ddl, re.S):
        cols = []
        for line in body.strip().splitlines():
            name, rest = line.strip().rstrip(",").split(" ", 1)
            cols.append((name, rest.replace(" NOT NULL", ""), "NOT NULL" in rest))
        out[table] = cols
    return out


_WS = " \t"


def _quote(v: str) -> str:
    return '"' + v.replace('"', '""') + '"'


def _fields(record: str) -> list[tuple[str, bool]]:
    """Split one CSV record into (value, was_quoted) pairs."""
    out, i, n = [], 0, len(record)
    while True:
        # skip leading spaces before an opening quote (ClickHouse tolerates them)
        j = i
        while j < n and record[j] in _WS:
            j += 1
        if j < n and record[j] == '"':
            j += 1
            val = []
            while True:
                k = record.index('"', j)
                val.append(record[j:k])
                if k + 1 < n and record[k + 1] == '"':
                    val.append('"')
                    j = k + 2
                    continue
                j = k + 1
                break
            while j < n and record[j] in _WS:
                j += 1
            out.append(("".join(val), True))
            if j >= n:
                return out
            if record[j] != ",":
                raise ValueError(f"bad CSV after quoted field: {record[:200]!r}")
            i = j + 1
        else:
            k = record.find(",", i)
            if k < 0:
                out.append((record[i:], False))
                return out
            out.append((record[i:k], False))
            i = k + 1


def clickhouse_csv_records(text) -> Iterator[str]:
    """Re-emit CSV records the way ClickHouse's CSV input reads them
    (input_format_csv_trim_whitespaces=1): unquoted fields lose leading/trailing
    spaces and tabs and become NULL when empty; quoted fields keep their value
    verbatim ("" stays an empty string). Header line is passed through."""
    first = True
    pending = ""
    for line in text:
        if first:
            first = False
            yield line.rstrip("\r\n") + "\n"
            continue
        record = pending + line
        if record.count('"') % 2:  # newline inside a quoted field: keep reading
            pending = record
            continue
        pending = ""
        record = record.rstrip("\r\n")
        if '"' not in record:
            vals = [v.strip(_WS) for v in record.split(",")]
            yield ",".join(_quote(v) if v else "" for v in vals) + "\n"
            continue
        parts = []
        for v, quoted in _fields(record):
            if quoted:
                parts.append(_quote(v))
            else:
                v = v.strip(_WS)
                parts.append(_quote(v) if v else "")
        yield ",".join(parts) + "\n"
    if pending:
        raise ValueError("unterminated quoted field at end of file")


def load_table(conn, zf: zipfile.ZipFile, table: str, cols) -> int:
    member = f"{table}.csv"
    with zf.open(member) as fh:
        header = next(csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")))
    header = [HEADER_ALIASES.get((table, h), h) for h in header]
    expected = [c[0] for c in cols]
    if header != expected:
        raise ValueError(f"{member}: header {header} != DDL {expected}")
    not_null_text = [c[0] for c in cols if c[2] and c[1] == "text"]
    opts = "FORMAT csv, HEADER true"
    if not_null_text:
        opts += f", FORCE_NOT_NULL ({', '.join(not_null_text)})"
    with conn.cursor() as cur, cur.copy(f"COPY retail.{table} FROM STDIN WITH ({opts})") as cp, \
            zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")
        buf: list[str] = []
        for record in clickhouse_csv_records(text):
            buf.append(record)
            if len(buf) >= 20_000:
                cp.write("".join(buf))
                buf.clear()
        if buf:
            cp.write("".join(buf))
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM retail.{table}")
        rows = cur.fetchone()[0]
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--zip", type=Path, required=True)
    args = ap.parse_args(argv)

    ddl = PG_DDL.read_text(encoding="utf-8")
    tables = parse_ddl(ddl)
    with zipfile.ZipFile(args.zip) as zf, psycopg.connect(args.dsn) as conn:
        missing = {f"{t}.csv" for t in tables} - set(zf.namelist())
        if missing:
            raise SystemExit(f"zip lacks {sorted(missing)}")
        conn.execute("DROP SCHEMA IF EXISTS retail CASCADE")
        conn.execute("CREATE SCHEMA retail")
        conn.execute(ddl)
        conn.commit()
        for table, cols in tables.items():
            start = time.time()
            n = load_table(conn, zf, table, cols)
            conn.commit()
            print(f"{table}: {n} rows ({time.time() - start:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
