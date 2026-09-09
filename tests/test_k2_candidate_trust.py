from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import pytest

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.sources.auto_extractor import ExtractionInputError, UniversalAutoExtractor
from fxi.sources.candidate_ingestor import CandidateIngestor
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


class _ApprovalVerifier:
    """模拟服务端审批表，避免测试把可编辑候选文件当作审批权威。"""

    def __init__(self, record: Mapping[str, Any]):
        self.record = dict(record)

    def get_approval(self, approval_id: str) -> Mapping[str, Any]:
        if approval_id != self.record["approval_id"]:
            raise ValidationError("审批不存在")
        return dict(self.record)

    def consume(self, approval_id: str, consumer_id: str) -> None:
        if approval_id != self.record["approval_id"] or self.record["consumed_by"] is not None:
            raise ValidationError("审批不可消费")
        self.record["consumed_by"] = consumer_id


def _payload() -> dict[str, object]:
    return {
        "slug": "candidate-a",
        "version": "candidate-v1",
        "package": {"rules": ["仅使用已绑定证据"]},
    }


def _binding(config) -> dict[str, object]:
    text = "synthetic candidate trust evidence"
    text_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        "source-a",
        [
            SourceDocumentInput(
                document_id="document-a",
                chapter_index=1,
                raw_bytes=text.encode("utf-8"),
                text=text,
                relative_path="synthetic.md",
                expected_content_hash=text_hash,
            )
        ],
        version="source-v1",
    )
    return {
        "work_id": "work-a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "input_hash": text_hash,
        "evidence_refs": [
            {
                "evidence_id": "evidence-a",
                "source_id": "source-a",
                "source_version": "source-v1",
                "document_id": "document-a",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": text_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "evaluation-a",
        "submitted_by": "extractor-a",
    }


def _approval(candidate_id: str, package_hash: str) -> dict[str, object]:
    return {
        "approval_id": "approval-a",
        "actor_id": "approver-a",
        "action": "candidate_promotion",
        "target_id": candidate_id,
        "target_hash": package_hash,
        "work_id": "work-a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "candidate_version": "candidate-v1",
        "evaluation_ref": "evaluation-a",
        "expected_version": "style-v0",
    }


def test_promote_requires_consumed_server_receipt_and_rejects_status_forgery(temp_workspace) -> None:
    server = _ApprovalVerifier(
        {
            "approval_id": "approval-a",
            "action": "candidate_promotion",
            "target_id": "",
            "target_hash": "",
            "expected_version": "style-v0",
            "work_id": "work-a",
            "actor_id": "approver-a",
            "source_id": "source-a",
            "source_version": "source-v1",
            "candidate_version": "candidate-v1",
            "evaluation_ref": "evaluation-a",
            "expires_at": None,
            "validity": "VALID",
            "consumed_by": None,
            "created_at": "2026-09-08T00:00:00+00:00",
        }
    )
    store = CandidateStore(temp_workspace, approval_verifier=server)
    record = store.submit(_payload(), **_binding(temp_workspace))
    approval = _approval(record.candidate_id, record.package_hash)
    server.record["target_id"] = record.candidate_id
    server.record["target_hash"] = record.package_hash

    forged = replace(record, status="APPROVED", approval_id="approval-a", approved_by="approver-a")
    store.repository.save(forged)
    assert store.qualification_view() == ()

    store.repository.save(record)
    promoted = store.promote(
        record.candidate_id,
        approval,
        actor_id="approver-a",
        approval_consumer=server.consume,
    )

    assert promoted.approval_receipt_hash
    assert [item.candidate_id for item in store.qualification_view()] == [record.candidate_id]


def test_missing_evidence_binding_is_rejected_before_candidate_persistence(temp_workspace) -> None:
    store = CandidateStore(temp_workspace)

    with pytest.raises(ValidationError, match="候选范围绑定不完整"):
        store.submit(
            _payload(),
            work_id="work-a",
            source_id="source-a",
            source_version="source-v1",
            input_hash="input-hash-a",
            evaluation_ref="evaluation-a",
            submitted_by="extractor-a",
            evidence_refs=[],
        )

    assert store.repository.list_records() == []


def test_auto_extractor_rejects_missing_candidate_binding_before_model_or_candidate(temp_workspace) -> None:
    extractor = UniversalAutoExtractor(temp_workspace, gateway=object())

    with pytest.raises(ExtractionInputError, match="候选抽取必须提供来源版本"):
        extractor.extract_and_ingest("work-a")

    assert not list((temp_workspace.materials_dir / "candidates").glob("*.json"))


def test_candidate_ingestor_does_not_trust_editable_approved_status(temp_workspace) -> None:
    store = CandidateStore(temp_workspace)
    record = store.submit(_payload(), **_binding(temp_workspace))
    store.repository.save(
        replace(record, status="APPROVED", approval_id="approval-a", approved_by="approver-a")
    )

    report = CandidateIngestor(temp_workspace).ingest_work_candidates("work-a", [record.candidate_id])

    assert report["status"] == "REVIEW_REQUIRED"
    assert report["formal_knowledge_written"] is False
    assert not (temp_workspace.projects_dir / "work-a" / "entities").exists()
