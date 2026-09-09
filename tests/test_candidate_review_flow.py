from __future__ import annotations

import pytest

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.materials_skills.distillation_receiver import DistillationReceiver
from fxi.timeline.chapter_commit import (
    ChapterCommitService,
    CommitConflictError,
    InMemoryCommitStore,
    content_hash,
)
from fxi.timeline.writing_review import ReviewRejectedError, WritingReviewService
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


def _review_request() -> dict[str, object]:
    return {
        "work_id": "work-a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "knowledge_version": "knowledge-v0",
        "text": "主角走进雨幕。",
        "text_hash": "text-hash-a",
        "plan_hash": "plan-hash-a",
        "context_hash": "context-hash-a",
        "required_checks": ["semantic"],
        "text_length": 8,
    }


class PassingReviewer:
    reviewer_id = "reviewer-test"
    reviewer_version = "reviewer-v1"

    def review(self, request: dict[str, object]) -> dict[str, object]:
        return {
            "reviewer_id": self.reviewer_id,
            "reviewer_version": self.reviewer_version,
            "checks": [
                {
                    "check_id": "semantic",
                    "status": "PASSED",
                    "required": True,
                    "input_text_hash": request["text_hash"],
                    "coverage_spans": [[0, int(request["text_length"])]],
                }
            ],
        }


class _ApprovalVerifier:
    """为候选生命周期测试提供可消费、可复验的服务端审批边界。"""

    def __init__(self) -> None:
        self.record: dict[str, object] | None = None

    def set(self, record: dict[str, object]) -> None:
        self.record = dict(record)

    def get_approval(self, approval_id: str) -> dict[str, object]:
        if self.record is None or approval_id != self.record.get("approval_id"):
            raise ValidationError(f"合成审批不存在: {approval_id}")
        return dict(self.record)

    def consume(self, approval_id: str, consumer_id: str) -> None:
        if self.record is None or approval_id != self.record.get("approval_id"):
            raise ValidationError(f"合成审批不存在: {approval_id}")
        if self.record.get("consumed_by") is not None:
            raise ValidationError(f"合成审批已消费: {approval_id}")
        self.record["consumed_by"] = consumer_id


def _candidate_binding(config, *, text: str = "synthetic review evidence") -> dict[str, object]:
    text_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        "synthetic-review-source",
        [
            SourceDocumentInput(
                document_id="synthetic-review-document",
                chapter_index=1,
                raw_bytes=text.encode("utf-8"),
                text=text,
                relative_path="synthetic-review.md",
                expected_content_hash=text_hash,
            )
        ],
        version="source-v1",
    )
    return {
        "work_id": "work-a",
        "source_id": "synthetic-review-source",
        "source_version": "source-v1",
        "input_hash": text_hash,
        "evidence_refs": [
            {
                "evidence_id": "synthetic-review-evidence",
                "source_id": "synthetic-review-source",
                "source_version": "source-v1",
                "document_id": "synthetic-review-document",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": text_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "synthetic-review-evaluation",
        "submitted_by": "synthetic-review-extractor",
    }


def _approval(
    record,
    *,
    approval_id: str = "approval-1",
    actor_id: str = "approver-1",
    expected_version: str = "style-v0",
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "actor_id": actor_id,
        "action": "candidate_promotion",
        "target_id": record.candidate_id,
        "target_hash": record.package_hash,
        "work_id": record.work_id,
        "source_id": record.source_id,
        "source_version": record.source_version,
        "candidate_version": record.version,
        "evaluation_ref": record.evaluation_ref,
        "expected_version": expected_version,
        "expires_at": None,
        "validity": "VALID",
        "consumed_by": None,
        "created_at": "2026-09-09T00:00:00+00:00",
    }


def test_missing_reviewer_is_incomplete_and_cannot_fabricate_pass() -> None:
    service = WritingReviewService()
    decision = service.review(_review_request())

    assert decision.status == "INCOMPLETE"
    assert decision.to_dict()["checks"][0]["error_code"] == "REVIEWER_NOT_CONFIGURED"
    with pytest.raises(ReviewRejectedError):
        service.assert_passed(
            {
                "report_id": "fake-review",
                "reviewer_id": "fake",
                "reviewer_version": "fake-v1",
                "text_hash": "text-hash-a",
                "plan_hash": "plan-hash-a",
                "context_hash": "context-hash-a",
                "checks": [
                    {
                        "check_id": "semantic",
                        "status": "PASSED",
                        "input_text_hash": "text-hash-a",
                    }
                ],
            },
            text_hash="text-hash-a",
            plan_hash="plan-hash-a",
            context_hash="context-hash-a",
        )


def test_configured_reviewer_produces_verifiable_passed_report() -> None:
    service = WritingReviewService(reviewer=PassingReviewer())
    decision = service.review(_review_request())

    assert decision.passed
    report = decision.to_dict()
    assert report["reviewer_id"] == "reviewer-test"
    assert report["reviewer_version"] == "reviewer-v1"
    assert report["work_id"] == "work-a"
    assert report["source_id"] == "source-a"
    assert report["source_version"] == "source-v1"
    assert service.assert_passed(
        report,
        text_hash="text-hash-a",
        plan_hash="plan-hash-a",
        context_hash="context-hash-a",
        work_id="work-a",
        source_id="source-a",
        source_version="source-v1",
        knowledge_version="knowledge-v0",
    ).passed


def test_reviewer_scope_mismatch_is_visible() -> None:
    class WrongScopeReviewer(PassingReviewer):
        def review(self, request: dict[str, object]) -> dict[str, object]:
            result = super().review(request)
            result["source_id"] = "other-source"
            return result

    decision = WritingReviewService(reviewer=WrongScopeReviewer()).review(_review_request())

    assert decision.status == "INCOMPLETE"
    assert decision.to_dict()["checks"][0]["error_code"] == "REVIEWER_ATTESTATION_INVALID"


def test_review_dependency_fingerprint_and_replay_binding() -> None:
    service = WritingReviewService(reviewer=PassingReviewer())
    request = {
        **_review_request(),
        "dependency_versions": {
            "context_hash": "context-hash-a",
            "knowledge_version": "knowledge-v0",
        },
    }

    decision = service.review(request)
    report = decision.to_dict()

    assert decision.passed
    assert report["dependency_versions"] == {
        "context_hash": "context-hash-a",
        "knowledge_version": "knowledge-v0",
    }
    assert service.assert_passed(
        report,
        text_hash="text-hash-a",
        plan_hash="plan-hash-a",
        context_hash="context-hash-a",
        work_id="work-a",
        source_id="source-a",
        source_version="source-v1",
        knowledge_version="knowledge-v0",
        dependency_versions=request["dependency_versions"],
    ).passed
    with pytest.raises(ReviewRejectedError):
        service.assert_passed(
            report,
            text_hash="text-hash-a",
            plan_hash="plan-hash-a",
            context_hash="context-hash-a",
            work_id="work-a",
            source_id="source-a",
            source_version="source-v1",
            knowledge_version="knowledge-v0",
            dependency_versions={"context_hash": "changed", "knowledge_version": "knowledge-v0"},
        )


@pytest.mark.parametrize("status", ["UNKNOWN", "TIMEOUT"])
def test_unknown_or_timeout_review_result_is_incomplete(status: str) -> None:
    class FailingReviewer(PassingReviewer):
        def review(self, request: dict[str, object]) -> dict[str, object]:
            if status == "TIMEOUT":
                raise TimeoutError("review timed out")
            return {
                "checks": [
                    {
                        "check_id": "semantic",
                        "status": status,
                        "input_text_hash": request["text_hash"],
                    }
                ]
            }

    decision = WritingReviewService(reviewer=FailingReviewer()).review(_review_request())

    assert decision.status == "INCOMPLETE"
    assert decision.passed is False


def test_non_api_commit_boundary_reuses_review_and_dependency_contracts() -> None:
    text = "主角在雨中停下。"
    plan = {"beat": "停下"}
    context = {"scene": "雨幕"}
    dependencies = {"context_hash": "context-v1"}
    review_service = WritingReviewService(reviewer=PassingReviewer())
    review = review_service.review(
        {
            "work_id": "work-a",
            "source_id": "source-a",
            "source_version": "source-v1",
            "knowledge_version": "knowledge-v0",
            "text": text,
            "text_hash": content_hash(text),
            "plan_hash": content_hash(plan),
            "context_hash": content_hash(context),
            "required_checks": ["semantic"],
            "text_length": len(text),
            "dependency_versions": dependencies,
        }
    )
    request = {
        "work_id": "work-a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "chapter_index": 1,
        "chapter_version": "chapter-v1",
        "proposal_id": "proposal-1",
        "proposal_hash": "proposal-hash",
        "text": text,
        "text_hash": content_hash(text),
        "plan": plan,
        "plan_hash": content_hash(plan),
        "context": context,
        "context_hash": content_hash(context),
        "final_review_reference": review.report_id,
        "approval_id": "approval-1",
        "expected_knowledge_version": "knowledge-v0",
        "idempotency_key": "commit-1",
        "dependency_versions": dependencies,
    }
    approval = {
        "approval_id": "approval-1",
        "actor_id": "writer-1",
        "action": "chapter_commit",
        "target_id": "proposal-1",
        "target_hash": "proposal-hash",
        "expected_version": "knowledge-v0",
        "validity": "VALID",
    }
    service = ChapterCommitService(store=InMemoryCommitStore(), review_service=review_service)

    first = service.commit(request, review=review.to_dict(), approval=approval, actor_id="writer-1")
    replay = service.commit(request, review=review.to_dict(), approval=approval, actor_id="writer-1")

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    with pytest.raises(CommitConflictError):
        service.commit(
            {**request, "commit_id": "another-commit"},
            review=review.to_dict(),
            approval=approval,
            actor_id="writer-1",
        )


def test_distillation_only_creates_candidate_and_replay_is_idempotent(temp_workspace) -> None:
    receiver = DistillationReceiver(temp_workspace)
    binding = _candidate_binding(temp_workspace)
    payload = {
        "slug": "skill-candidate",
        "version": "candidate-v1",
        "work_id": binding["work_id"],
        "source_id": binding["source_id"],
        "source_version": binding["source_version"],
        "input_hash": binding["input_hash"],
        "evidence_refs": binding["evidence_refs"],
        "evaluation_ref": binding["evaluation_ref"],
        "idempotency_key": "candidate-request-1",
        "package": {"rules": ["保持动作因果"], "anti_patterns": ["空泛总结"]},
    }

    first = receiver.ingest_skill_package(payload, actor_id="extractor-1")
    replay = receiver.ingest_skill_package(payload, actor_id="extractor-1")

    assert first["status"] == "EVALUATION_CANDIDATE"
    assert first["formal_knowledge_written"] is False
    assert first["requires_review"] is True
    assert first["candidate_id"] == replay["candidate_id"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert not (temp_workspace.skills_dir / "skill-candidate").exists()


def test_replay_with_changed_provenance_is_rejected(temp_workspace) -> None:
    receiver = DistillationReceiver(temp_workspace)
    binding = _candidate_binding(temp_workspace)
    payload = {
        "slug": "skill-candidate",
        "version": "candidate-v1",
        "work_id": binding["work_id"],
        "source_id": binding["source_id"],
        "source_version": binding["source_version"],
        "input_hash": binding["input_hash"],
        "evidence_refs": binding["evidence_refs"],
        "evaluation_ref": binding["evaluation_ref"],
        "idempotency_key": "candidate-request-1",
        "package": {"rules": ["保持动作因果"]},
    }

    receiver.ingest_skill_package(payload, actor_id="extractor-1")

    with pytest.raises(ValidationError):
        receiver.ingest_skill_package(
            {
                **payload,
                "evidence_refs": [
                    {**binding["evidence_refs"][0], "evidence_id": "synthetic-review-evidence-2"}
                ],
            },
            actor_id="extractor-1",
        )


def test_qualification_view_contains_approved_candidates_only(temp_workspace) -> None:
    binding = _candidate_binding(temp_workspace)
    approval_verifier = _ApprovalVerifier()
    store = CandidateStore(temp_workspace, approval_verifier=approval_verifier)
    pending = store.submit({"slug": "pending", "package": {"rules": ["待审"]}}, **binding)
    approved = store.submit({"slug": "approved", "package": {"rules": ["已审"]}}, **binding)
    approval = _approval(approved, actor_id="reviewer")
    approval_verifier.set(approval)
    store.promote(
        approved.candidate_id,
        approval,
        actor_id="reviewer",
        approval_consumer=approval_verifier.consume,
    )

    visible = store.qualification_view()

    assert [record.candidate_id for record in visible] == [approved.candidate_id]
    assert pending.candidate_id not in {record.candidate_id for record in visible}


def test_candidate_promotion_requires_bound_approval_and_is_idempotent(temp_workspace) -> None:
    binding = _candidate_binding(temp_workspace)
    approval_verifier = _ApprovalVerifier()
    store = CandidateStore(temp_workspace, approval_verifier=approval_verifier)
    record = store.submit(
        {
            "slug": "skill-candidate",
            "version": "candidate-v1",
            "package": {"rules": ["保持动作因果"]},
        },
        **binding,
    )
    approval = _approval(record)
    approval_verifier.set(approval)

    promoted = store.promote(
        record.candidate_id,
        approval,
        actor_id="approver-1",
        approval_consumer=approval_verifier.consume,
    )
    replay = store.promote(record.candidate_id, {**approval, "work_id": "work-b"}, actor_id="approver-1")

    assert promoted.status == "APPROVED"
    assert replay.candidate_id == promoted.candidate_id
    assert (temp_workspace.skills_dir / "skill-candidate" / "package.yaml").is_file()

    assert replay.status == "APPROVED"


def test_promotion_consumer_failure_is_visible_and_keeps_candidate_isolated(temp_workspace) -> None:
    binding = _candidate_binding(temp_workspace)
    approval_verifier = _ApprovalVerifier()
    store = CandidateStore(temp_workspace, approval_verifier=approval_verifier)
    record = store.submit(
        {
            "slug": "failed-promotion",
            "version": "candidate-v1",
            "package": {"rules": ["保持动作因果"]},
        },
        **binding,
    )
    approval = _approval(record, approval_id="approval-failed")
    approval_verifier.set(approval)

    def fail_consume(_approval_id: str, _candidate_id: str) -> None:
        raise RuntimeError("approval consumer unavailable")

    with pytest.raises(RuntimeError, match="approval consumer unavailable"):
        store.promote(
            record.candidate_id,
            approval,
            actor_id="approver-1",
            approval_consumer=fail_consume,
        )

    assert store.get(record.candidate_id).status == "EVALUATION_CANDIDATE"
    assert not (temp_workspace.skills_dir / "failed-promotion").exists()


def test_receiver_rejects_self_reported_identity(temp_workspace) -> None:
    receiver = DistillationReceiver(temp_workspace)
    with pytest.raises(ValidationError):
        receiver.ingest_skill_package(
            {
                "slug": "skill-candidate",
                "work_id": "work-a",
                "source_id": "source-a",
                "source_version": "source-v1",
                "input_hash": "source-text-hash",
                "package": {"rules": ["保持动作因果"]},
                "actor_id": "spoofed",
            },
            actor_id="extractor-1",
        )
