from __future__ import annotations

import pytest

from scripts.pg.build_fixtures import build_benchmark, convert_ddl, pg_type, stale_outputs


def test_generated_pg_fixtures_are_up_to_date():
    stale = stale_outputs()
    assert not stale, (
        f"stale: {[p.name for p in stale]}; run `uv run python scripts/pg/build_fixtures.py`"
    )


def test_pg_type_maps_nullable_and_rejects_unknown():
    assert pg_type("Nullable(Float64)") == ("double precision", True)
    assert pg_type("Date") == ("date", False)
    with pytest.raises(ValueError):
        pg_type("Decimal(18, 2)")


def test_convert_ddl_keeps_identifiers_unquoted():
    ddl = "CREATE TABLE retail.DimX\n(\n    `XKey` Int32,\n    `Name` Nullable(String)\n)\nENGINE = MergeTree"
    out = convert_ddl(ddl)
    assert "CREATE TABLE retail.DimX" in out
    assert "    XKey integer NOT NULL" in out
    assert "    Name text\n" in out


def test_build_benchmark_parses_annotations_and_checks_ids():
    src = {"questions": [{"id": 1, "expected_sql": "SELECT countIf(x)"}]}
    pg = "-- @q1\n-- @note why\n-- @ties 0,2\nSELECT count(*) FILTER (WHERE x)\n"
    q = build_benchmark(src, pg)["questions"][0]
    assert q["expected_sql"] == "SELECT count(*) FILTER (WHERE x)"
    assert q["expected_sql_clickhouse"] == "SELECT countIf(x)"
    assert q["translation_notes"] == ["why"]
    assert q["verify_ties"] == [0, 2]
    assert q["verify_deviation"] is None
    with pytest.raises(ValueError, match="missing"):
        build_benchmark({"questions": [{"id": 2, "expected_sql": ""}]}, pg)
