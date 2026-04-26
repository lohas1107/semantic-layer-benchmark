from __future__ import annotations

import pytest

from benchmarks.text_to_sql.config import Config, load_config


def test_load_config_reads_env(monkeypatch, tmp_path):
    # Use a non-existent env path so load_dotenv doesn't repopulate from .env
    nonexistent = tmp_path / "no.env"
    monkeypatch.setenv("CLICKHOUSE_HOST", "ch.example.invalid")
    monkeypatch.setenv("CLICKHOUSE_PORT", "443")
    monkeypatch.setenv("CLICKHOUSE_USER", "reader")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "pw")
    monkeypatch.setenv("CLICKHOUSE_SECURE", "true")
    monkeypatch.setenv("CLICKHOUSE_VERIFY", "true")

    cfg = load_config(env_path=nonexistent)

    assert isinstance(cfg, Config)
    assert cfg.clickhouse_host == "ch.example.invalid"
    assert cfg.clickhouse_port == 443
    assert cfg.clickhouse_secure is True
    assert cfg.clickhouse_verify is True


def test_load_config_missing_required_raises(monkeypatch, tmp_path):
    # Use a non-existent env path so load_dotenv doesn't repopulate from .env
    nonexistent = tmp_path / "no.env"
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    monkeypatch.setenv("CLICKHOUSE_HOST", "ch.example.invalid")
    monkeypatch.setenv("CLICKHOUSE_PORT", "443")
    monkeypatch.setenv("CLICKHOUSE_USER", "reader")

    with pytest.raises(RuntimeError, match="CLICKHOUSE_PASSWORD"):
        load_config(env_path=nonexistent)


def test_load_config_bool_parsing(monkeypatch, tmp_path):
    # Use a non-existent env path so load_dotenv doesn't repopulate from .env
    nonexistent = tmp_path / "no.env"
    for truthy in ("true", "TRUE", "1", "yes"):
        monkeypatch.setenv("CLICKHOUSE_HOST", "h")
        monkeypatch.setenv("CLICKHOUSE_PORT", "443")
        monkeypatch.setenv("CLICKHOUSE_USER", "u")
        monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
        monkeypatch.setenv("CLICKHOUSE_SECURE", truthy)
        monkeypatch.setenv("CLICKHOUSE_VERIFY", "false")
        cfg = load_config(env_path=nonexistent)
        assert cfg.clickhouse_secure is True, f"expected True for {truthy!r}"
        assert cfg.clickhouse_verify is False
