"""Synthetic checks for the generic v3 authority schema."""

from __future__ import annotations

import sqlite3

import pytest

from fxi.knowledge.contracts import CONTRACT_REVISION, SCHEMA_HASH
from fxi.storage.sqlite_client import (
    KNOWLEDGE_V3_SCHEMA_VERSION,
    DatabaseClient,
    knowledge_v3_ddl_hash,
)


def test_v3_bootstrap_persists_generic_schema_identity(tmp_path) -> None:
    client = DatabaseClient(tmp_path / "manifest.sqlite")

    with client.get_connection() as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name LIKE 'knowledge_%'"
            )
        }
        assert {
            "knowledge_schema_meta",
            "knowledge_source_bindings",
            "knowledge_source_snapshots",
            "knowledge_evidence",
            "knowledge_candidates",
            "knowledge_evaluations",
            "knowledge_decisions",
            "knowledge_approvals",
            "knowledge_objects",
            "knowledge_claims",
            "knowledge_relations",
            "knowledge_versions",
            "knowledge_branches",
            "knowledge_heads",
            "knowledge_context_views",
            "knowledge_reviews",
            "knowledge_state_changes",
            "knowledge_proposals",
            "knowledge_commits",
            "knowledge_projection_tasks",
            "knowledge_operation_receipts",
        } <= table_names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == KNOWLEDGE_V3_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    metadata = client.schema_metadata()
    assert metadata["schema_version"] == KNOWLEDGE_V3_SCHEMA_VERSION
    assert metadata["contract_revision"] == CONTRACT_REVISION
    assert metadata["contract_schema_hash"] == SCHEMA_HASH
    assert metadata["ddl_hash"] == knowledge_v3_ddl_hash()


def test_v3_authority_transaction_rolls_back_all_rows_and_keeps_schema_version(tmp_path) -> None:
    client = DatabaseClient(tmp_path / "manifest.sqlite")

    with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
        with client.transaction() as cursor:
            cursor.execute(
                "INSERT INTO knowledge_operation_receipts "
                "(operation_id, operation_kind, idempotency_key, input_hash, status, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "operation-synthetic",
                    "synthetic",
                    "idempotency-synthetic",
                    "a" * 64,
                    "STARTED",
                    "{}",
                    "now",
                    "now",
                ),
            )
            raise sqlite3.IntegrityError("synthetic rollback")

    with client.get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_operation_receipts"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == KNOWLEDGE_V3_SCHEMA_VERSION


def test_v3_evidence_link_foreign_keys_reject_unknown_authority_rows(tmp_path) -> None:
    client = DatabaseClient(tmp_path / "manifest.sqlite")

    with pytest.raises(sqlite3.IntegrityError):
        with client.transaction() as cursor:
            cursor.execute(
                "INSERT INTO knowledge_object_evidence (object_id, evidence_id) "
                "VALUES (?, ?)",
                ("object-unknown", "evidence-unknown"),
            )
