"""Load the original ContosoRetailDW text export into PostgreSQL (schema dbo).

Data:  Hugging Face jocapc/ContosoRetailsDW — Microsoft's Synapse-tutorial
       export of ContosoRetailDW (pipe-delimited, no header, one zip per table).
DDL:   source/load-contoso-data-warehouse-to-sql-data-warehouse.sql, Microsoft's
       official load script (microsoft/sql-server-samples), which defines all 25
       external tables over exactly these files.

Only types are mapped (SQL Server -> PostgreSQL); values are loaded unchanged.
Empty fields load as NULL, matching the script's USE_TYPE_DEFAULT = FALSE.
Names stay CamelCase but unquoted, so PostgreSQL stores them in lower case.

Usage:
  uv run python docker/contoso/load_jocapc.py --dsn postgresql://... --zips DIR [--ddl-only]
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
import zipfile
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
LOAD_SCRIPT = HERE / "source" / "load-contoso-data-warehouse-to-sql-data-warehouse.sql"
SCHEMA = "dbo"

_TABLE_RE = re.compile(
    r"CREATE EXTERNAL TABLE \[asb\]\.(\w+)\s*\((.*?)\)\s*WITH\s*\(\s*LOCATION\s*=\s*'/(\w+)/'",
    re.S | re.I,
)
_COL_RE = re.compile(
    r"\[(\w+)\]\s+\[?(\w+)\]?(?:\s*\(([\d\s,]+|max)\))?\s+(NOT NULL|NULL)", re.I
)


def pg_type(mssql: str, args: str | None) -> str:
    t = mssql.lower()
    simple = {
        "int": "integer", "bigint": "bigint", "smallint": "smallint", "tinyint": "smallint",
        "bit": "boolean", "float": "double precision", "real": "real",
        "money": "numeric(19,4)", "smallmoney": "numeric(10,4)",
        "datetime": "timestamp(3)", "datetime2": "timestamp", "smalldatetime": "timestamp(0)",
        "date": "date", "time": "time", "uniqueidentifier": "uuid",
    }
    if t in simple:
        return simple[t]
    if t in ("decimal", "numeric"):
        return f"numeric({args.replace(' ', '')})" if args else "numeric"
    if t in ("nvarchar", "varchar"):
        return "text" if not args or args.lower() == "max" else f"varchar({args.strip()})"
    if t in ("nchar", "char"):
        return f"char({args.strip()})" if args else "char(1)"
    raise ValueError(f"unmapped SQL Server type {mssql}({args})")


def parse_tables(script: str) -> list[tuple[str, str, list[tuple[str, str, bool]]]]:
    """[(table, folder, [(column, pg_type, nullable)])] in script order."""
    out = []
    script = re.sub(r"--[^\n]*", "", script)  # the script comments out some columns
    for table, body, folder in _TABLE_RE.findall(script):
        cols = [(name, pg_type(typ, args), null.upper() == "NULL")
                for name, typ, args, null in _COL_RE.findall(body)]
        if not cols:
            raise ValueError(f"no columns parsed for {table}")
        out.append((table, folder, cols))
    return out


def create_sql(table: str, cols: list[tuple[str, str, bool]]) -> str:
    defs = ",\n  ".join(f"{c} {t}{'' if n else ' NOT NULL'}" for c, t, n in cols)
    return f"CREATE TABLE {SCHEMA}.{table} (\n  {defs}\n)"


def load_table(conn: psycopg.Connection, table: str, ncols: int, zpath: Path) -> int:
    rows = 0
    copy_sql = (f"COPY {SCHEMA}.{table} FROM STDIN WITH (FORMAT csv, DELIMITER '|', "
                f"NULL '', QUOTE E'\\x01')")
    with zipfile.ZipFile(zpath) as zf, conn.cursor() as cur, cur.copy(copy_sql) as cp:
        members = sorted(m for m in zf.namelist() if m.lower().endswith(".txt"))
        if not members:
            raise ValueError(f"{zpath.name}: no .txt members")
        for m in members:
            with zf.open(m) as fh:
                for raw in io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""):
                    line = raw.rstrip("\r\n")
                    if not line:
                        continue
                    if line.count("|") != ncols - 1:
                        raise ValueError(f"{zpath.name}:{m}: expected {ncols} fields, "
                                         f"got {line.count('|') + 1}: {line[:200]}")
                    cp.write(line + "\n")
                    rows += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--zips", type=Path, required=True)
    ap.add_argument("--ddl-only", action="store_true")
    args = ap.parse_args(argv)

    tables = parse_tables(LOAD_SCRIPT.read_text(encoding="utf-8"))
    print(f"{len(tables)} tables parsed from {LOAD_SCRIPT.name}")
    with psycopg.connect(args.dsn) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        for table, _folder, cols in tables:
            conn.execute(create_sql(table, cols))
        conn.commit()
        if args.ddl_only:
            return 0
        for table, folder, cols in tables:
            start = time.time()
            n = load_table(conn, table, len(cols), args.zips / f"{folder}.zip")
            conn.commit()
            print(f"{table}: {n} rows ({time.time() - start:.0f}s)", flush=True)
        conn.autocommit = True
        conn.execute("ANALYZE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
