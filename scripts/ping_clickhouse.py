"""Quick connectivity check against ClickHouse. Exits 0 on success."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console

from benchmarks.text_to_sql.clickhouse_client import make_client


def main() -> int:
    console = Console()
    try:
        client = make_client()
    except Exception as exc:
        console.print(f"[red]Failed to build client: {exc}[/red]")
        return 1
    ok = client.ping()
    if ok:
        console.print("[green]ClickHouse OK[/green]")
        return 0
    console.print("[red]ClickHouse ping failed[/red]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
