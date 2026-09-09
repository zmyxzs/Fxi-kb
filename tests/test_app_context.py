"""Shared configuration, identifier and transaction-boundary regressions."""

from pathlib import Path
import sqlite3

import pytest

from fxi.core import AppContext, UnitOfWork, canonical_json, load_config, sha256_hex, validate_work_id
from fxi.core.exceptions import ValidationError
from fxi.storage.sqlite_client import DatabaseClient


def test_load_config_is_read_only_until_explicit_bootstrap(tmp_path: Path):
    config = load_config(workspace_root=tmp_path)

    assert not config.data_dir.exists()
    context = AppContext.from_config(config)
    assert not config.data_dir.exists()
    assert not config.sqlite_path.exists()

    bootstrapped = AppContext.bootstrap(config)
    assert bootstrapped.config == config
    assert config.data_dir.is_dir()
    assert config.sqlite_path.is_file()


def test_database_client_can_open_without_ddl_side_effect(tmp_path: Path):
    db_path = tmp_path / "nested" / "manifest.sqlite"
    client = DatabaseClient(db_path, initialize=False)

    assert not db_path.parent.exists()
    with pytest.raises(sqlite3.OperationalError):
        with client.get_connection():
            pass


def test_unit_of_work_reuses_context_connection_boundary(tmp_path: Path):
    context = AppContext.bootstrap(load_config(workspace_root=tmp_path))
    unit = UnitOfWork(context)

    with unit.transaction() as cursor:
        row = cursor.execute("SELECT 1 AS value").fetchone()

    assert row["value"] == 1


def test_identifier_and_canonical_contracts_are_shared():
    assert validate_work_id("work_a") == "work_a"
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert sha256_hex("fxi") == sha256_hex(b"fxi")

    with pytest.raises(ValidationError):
        validate_work_id("../outside")
    with pytest.raises(ValidationError):
        canonical_json(float("nan"))
