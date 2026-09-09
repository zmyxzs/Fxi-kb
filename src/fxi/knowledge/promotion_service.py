"""Atomic candidate promotion into a versioned, projection-ready knowledge head."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import ValidationError as PydanticValidationError

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .candidate_service import CandidateService, candidate_hash
from .contracts import ApprovalRef, ErrorCode
from .decisions import DecisionAction, PromotionReceipt
from .evaluation_service import EvaluationService
from .evaluations import Suitability
from .objects import KnowledgeHead, KnowledgeObject, KnowledgeVersion


class PromotionServiceError(FxiError):
    """A promotion failed before or during the atomic repository operation."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class PromotionRepository(Protocol):
    def get_head(self, work_id: str, branch_id: str) -> KnowledgeHead | None: ...

    def get_receipt(self, promotion_id: str) -> PromotionReceipt | None: ...

    def get_approval(self, approval_id: str) -> ApprovalRef | None: ...

    def register_approval(self, approval: ApprovalRef) -> None: ...

    def commit_promotion(
        self,
        *,
        candidate_id: str,
        approval_id: str,
        expected_head: str,
        object_record: KnowledgeObject,
        version: KnowledgeVersion,
        head: KnowledgeHead,
        receipt: PromotionReceipt,
        projection_task: Mapping[str, Any],
    ) -> None: ...


class InMemoryKnowledgeRepository:
    """Atomic in-memory authority for tests and explicit repository injection."""

    def __init__(self) -> None:
        self.objects: dict[str, KnowledgeObject] = {}
        self.versions: dict[str, KnowledgeVersion] = {}
        self.heads: dict[tuple[str, str], KnowledgeHead] = {}
        self.receipts: dict[str, PromotionReceipt] = {}
        self.approvals: dict[str, ApprovalRef] = {}
        self.projection_tasks: dict[str, Mapping[str, Any]] = {}
        self._lock = RLock()

    def get_head(self, work_id: str, branch_id: str) -> KnowledgeHead | None:
        with self._lock:
            head = self.heads.get((work_id, branch_id))
            return None if head is None else head.model_copy(deep=True)

    def get_receipt(self, promotion_id: str) -> PromotionReceipt | None:
        with self._lock:
            receipt = self.receipts.get(promotion_id)
            return None if receipt is None else receipt.model_copy(deep=True)

    def get_approval(self, approval_id: str) -> ApprovalRef | None:
        with self._lock:
            approval = self.approvals.get(approval_id)
            return None if approval is None else approval.model_copy(deep=True)

    def register_approval(self, approval: ApprovalRef) -> None:
        with self._lock:
            existing = self.approvals.get(approval.approval_id)
            if existing is not None and existing.approval_hash != approval.approval_hash:
                raise PromotionServiceError(
                    "approval_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self.approvals[approval.approval_id] = approval.model_copy(deep=True)

    def commit_promotion(
        self,
        *,
        candidate_id: str,
        approval_id: str,
        expected_head: str,
        object_record: KnowledgeObject,
        version: KnowledgeVersion,
        head: KnowledgeHead,
        receipt: PromotionReceipt,
        projection_task: Mapping[str, Any],
    ) -> None:
        with self._lock:
            current = self.heads.get((head.work_id, head.branch_id))
            current_value = "genesis" if current is None else current.knowledge_version
            current_hash = None if current is None else current.version_hash
            if current_value != expected_head and current_hash != expected_head:
                raise PromotionServiceError(
                    "knowledge head changed during promotion",
                    ErrorCode.CAS_CONFLICT.value,
                )
            approval = self.approvals.get(approval_id)
            if approval is None or approval.consumed:
                raise PromotionServiceError(
                    "approval is missing or already consumed",
                    ErrorCode.APPROVAL_REQUIRED.value,
                )
            self.objects[object_record.object_id] = object_record.model_copy(deep=True)
            self.versions[version.knowledge_version] = version.model_copy(deep=True)
            self.heads[(head.work_id, head.branch_id)] = head.model_copy(deep=True)
            self.projection_tasks[str(projection_task["task_id"])] = dict(projection_task)
            self.receipts[receipt.promotion_id] = receipt.model_copy(deep=True)
            self.approvals[approval_id] = approval.model_copy(update={"consumed": True}, deep=True)


class ApprovalAdapter(Protocol):
    def validate(
        self,
        approval: ApprovalRef,
        candidate: Any,
        evaluation: Any,
    ) -> None: ...


class PromotionService:
    """Promote only an evaluated, approved candidate using one CAS operation."""

    def __init__(
        self,
        candidate_service: CandidateService,
        evaluation_service: EvaluationService,
        *,
        repository: PromotionRepository | None = None,
        decision_service: Any | None = None,
        approval_adapter: ApprovalAdapter | Callable[[ApprovalRef, Any, Any], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.candidate_service = candidate_service
        self.evaluation_service = evaluation_service
        self.repository = repository or InMemoryKnowledgeRepository()
        self.decision_service = decision_service
        self.approval_adapter = approval_adapter
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()

    def register_approval(self, approval: ApprovalRef) -> None:
        if not isinstance(approval, ApprovalRef):
            raise PromotionServiceError("approval must be a real ApprovalRef", ErrorCode.APPROVAL_REQUIRED.value)
        self.repository.register_approval(approval)

    def promote(self, candidate_id: str, approval: ApprovalRef, expected_head: str) -> PromotionReceipt:
        if not isinstance(approval, ApprovalRef):
            raise PromotionServiceError(
                "promotion requires a real ApprovalRef, not caller JSON",
                ErrorCode.APPROVAL_REQUIRED.value,
            )
        candidate = self.candidate_service.get(candidate_id)
        self._validate_expected_head(expected_head)
        promotion_id = self._promotion_id(candidate, approval, expected_head)
        with self._lock:
            previous = self.repository.get_receipt(promotion_id)
            if previous is not None:
                if previous.approval_ref.approval_hash != approval.approval_hash:
                    raise PromotionServiceError(
                        "promotion replay contains a different approval",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
                replay = previous.model_dump(mode="json")
                replay["idempotent_replay"] = True
                replay.pop("receipt_hash", None)
                return PromotionReceipt.model_validate(replay)
        evaluation = self._latest_complete_evaluation(candidate_id, candidate.input_hash)
        self._validate_approval(approval, candidate, evaluation)
        if self.decision_service is not None:
            decisions = self.decision_service.get_for_candidate(candidate_id)
            if not any(
                item.evaluation_ref == evaluation.evaluation_id and item.action is DecisionAction.APPROVE
                for item in decisions
            ):
                raise PromotionServiceError(
                    "promotion requires a registered APPROVE decision",
                    ErrorCode.APPROVAL_REQUIRED.value,
                )
        with self._lock:
            head = self.repository.get_head(candidate.work_id, candidate.branch_id)
            current_value = "genesis" if head is None else head.knowledge_version
            current_hash = None if head is None else head.version_hash
            if current_value != expected_head and current_hash != expected_head:
                raise PromotionServiceError(
                    "expected knowledge head is stale",
                    ErrorCode.STALE_VERSION.value,
                )
            parent = None if head is None else head.knowledge_version
            object_id = f"object-{candidate.candidate_id}"
            version_id = f"knowledge-{sha256_hex({'candidate': candidate_hash(candidate), 'head': expected_head, 'approval': approval.approval_id})[:48]}"
            object_record = KnowledgeObject(
                object_id=object_id,
                work_id=candidate.work_id,
                branch_id=candidate.branch_id,
                type_uri=candidate.artifact_kind,
                schema_uri=candidate.schema_version,
                schema_version=candidate.domain_package_version,
                payload=candidate.payload,
                origin=candidate.extractor_id,
                scope=approval.actor.scope,
                evidence_refs=candidate.evidence_refs,
                evaluation_ref=evaluation.evaluation_id,
                knowledge_version=version_id,
                status="APPROVED",
            )
            version = KnowledgeVersion(
                knowledge_version=version_id,
                work_id=candidate.work_id,
                branch_id=candidate.branch_id,
                parent_version=parent,
                object_refs=(object_id,),
                source_snapshot_refs=(candidate.source_snapshot_ref,),
                actor=approval.actor.actor_id,
                status="APPROVED",
            )
            new_head = KnowledgeHead(
                work_id=candidate.work_id,
                branch_id=candidate.branch_id,
                knowledge_version=version.knowledge_version,
                version_hash=version.version_hash,
                cas_revision=0 if head is None else head.cas_revision + 1,
            )
            task_id = f"projection-{version.knowledge_version}"
            receipt = PromotionReceipt(
                promotion_id=promotion_id,
                candidate_id=candidate.candidate_id,
                approval_ref=approval,
                expected_head=expected_head,
                new_knowledge_version=version.knowledge_version,
                promoted_refs=(object_id, version.knowledge_version),
                projection_task_refs=(task_id,),
            )
            self.repository.commit_promotion(
                candidate_id=candidate_id,
                approval_id=approval.approval_id,
                expected_head=expected_head,
                object_record=object_record,
                version=version,
                head=new_head,
                receipt=receipt,
                projection_task={
                    "task_id": task_id,
                    "candidate_id": candidate_id,
                    "knowledge_version": version.knowledge_version,
                    "status": "PENDING",
                },
            )
            stored = self.repository.get_receipt(promotion_id)
            if stored is None:
                raise PromotionServiceError("promotion repository did not retain receipt")
            return stored

    def _latest_complete_evaluation(self, candidate_id: str, input_hash: str) -> Any:
        manifests = self.evaluation_service.get_for_candidate(candidate_id)
        if not manifests:
            raise PromotionServiceError(
                "promotion requires a registered evaluation",
                ErrorCode.EVALUATION_REQUIRED.value,
            )
        manifest = manifests[-1]
        if manifest.input_hash != input_hash:
            raise PromotionServiceError(
                "evaluation input hash does not match candidate",
                ErrorCode.CONFLICTING_ASSERTIONS.value,
            )
        if manifest.status == "INCOMPLETE":
            raise PromotionServiceError(
                "incomplete evaluation cannot be promoted",
                ErrorCode.EVALUATION_REQUIRED.value,
            )
        if manifest.suitability is not Suitability.SUITABLE:
            raise PromotionServiceError(
                "only a suitable evaluation can be promoted",
                ErrorCode.CONFLICTING_ASSERTIONS.value,
            )
        if manifest.conflicts or manifest.status in {"QUARANTINED", "REJECTED"}:
            raise PromotionServiceError(
                "conflicted or quarantined evaluation cannot be promoted",
                ErrorCode.CONFLICTING_ASSERTIONS.value,
            )
        return manifest

    def _validate_approval(self, approval: ApprovalRef, candidate: Any, evaluation: Any) -> None:
        registered = self.repository.get_approval(approval.approval_id)
        if registered is None or registered.approval_hash != approval.approval_hash:
            raise PromotionServiceError(
                "approval is not registered by the approval service",
                ErrorCode.APPROVAL_REQUIRED.value,
            )
        if registered.consumed:
            raise PromotionServiceError("approval has already been consumed", ErrorCode.APPROVAL_REQUIRED.value)
        if registered.actor.actor_id != approval.actor.actor_id:
            raise PromotionServiceError(
                "approval actor does not match registered approval",
                ErrorCode.AUTHORIZATION_FAILED.value,
            )
        if approval.actor.scope is None or approval.actor.scope.work_id != candidate.work_id or approval.actor.scope.branch_id != candidate.branch_id:
            raise PromotionServiceError(
                "approval actor scope does not match candidate",
                ErrorCode.AUTHORIZATION_FAILED.value,
            )
        self._check_expiry(registered)
        if self.approval_adapter is not None:
            method = getattr(self.approval_adapter, "validate", None)
            if callable(method):
                method(approval, candidate, evaluation)
            else:
                self.approval_adapter(approval, candidate, evaluation)

    def _check_expiry(self, approval: ApprovalRef) -> None:
        if approval.expires_at is None:
            return
        try:
            if isinstance(approval.expires_at, int):
                expired = self.clock().timestamp() >= approval.expires_at
            else:
                value = str(approval.expires_at).replace("Z", "+00:00")
                expiry = datetime.fromisoformat(value)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                expired = self.clock() >= expiry
        except (TypeError, ValueError, OverflowError) as exc:
            raise PromotionServiceError("approval expiry is invalid", ErrorCode.APPROVAL_EXPIRED.value) from exc
        if expired:
            raise PromotionServiceError("approval has expired", ErrorCode.APPROVAL_EXPIRED.value)

    @staticmethod
    def _validate_expected_head(expected_head: str) -> None:
        if not isinstance(expected_head, str) or not expected_head.strip():
            raise PromotionServiceError("expected_head is required", ErrorCode.STALE_VERSION.value)

    @staticmethod
    def _promotion_id(candidate: Any, approval: ApprovalRef, expected_head: str) -> str:
        return f"promotion-{sha256_hex({'candidate_id': candidate.candidate_id, 'approval_id': approval.approval_id, 'expected_head': expected_head})[:48]}"


__all__ = [
    "ApprovalAdapter",
    "InMemoryKnowledgeRepository",
    "PromotionRepository",
    "PromotionService",
    "PromotionServiceError",
]
