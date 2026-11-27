"""NFR-5: versioned configuration is validated; invalid files fail loudly."""

import json

import pytest
from pydantic import ValidationError

from finops_engine.engine_config import EngineConfig, load_engine_config


def test_defaults_are_valid_and_versioned():
    cfg = EngineConfig()
    assert cfg.version
    assert cfg.anomaly.z_threshold > 0


def test_shipped_config_file_matches_schema():
    from pathlib import Path

    config_dir = Path(__file__).resolve().parent.parent / "config"
    for name, currency in (
        ("finops-engine.json", "INR"),
        ("finops-engine.sample.json", "INR"),
        ("finops-engine.usd.json", "USD"),
    ):
        cfg = load_engine_config(config_dir / name)
        assert cfg.version.startswith(EngineConfig().version), name
        assert cfg.ingestion.base_currency == currency, name
        if currency == "INR":
            assert cfg.ingestion.fx_rates_to_base["USD"] > 0, name


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"anomaly": {"z_treshold": 3}}))
    with pytest.raises(ValidationError):
        load_engine_config(p)


def test_out_of_range_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"commitments": {"assumed_discount_rate": {"AWS": 1.5}}}))
    with pytest.raises(ValidationError):
        load_engine_config(p)


def test_invalid_json_and_missing_explicit_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    with pytest.raises(ValueError):
        load_engine_config(p)
    with pytest.raises(ValueError):
        load_engine_config(tmp_path / "missing.json")
