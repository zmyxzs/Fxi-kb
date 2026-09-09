"""Evaluation registry and service for submitted Fxi v3 candidates."""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import ValidationError as PydanticValidationError

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .candidate_service import CandidateService
from .contracts import CandidateEnvelope, EvaluationPolicy, ErrorCode
from .duplicate_clusters import DuplicateClusterIndex
from .evaluations import EvaluationManifest, Suitability


class EvaluationServiceError(FxiError):
    """An evaluation could not be created or retrieved."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class EvaluationRepository(Protocol):
    def get(self, evaluation_id: str) -> EvaluationManifest | None: ...

    def save(self, manifest: EvaluationManifest, policy: EvaluationPolicy) -> None: ...

    def list(self) -> Sequence[EvaluationManifest]: ...


class InMemoryEvaluationRepository:
    """Thread-safe registry retaining manifests and their policy bindings."""

    def __init__(self) -> None:
        self._manifests: dict[str, EvaluationManifest] = {}
        self._policies: dict[str, EvaluationPolicy] = {}
        self._lock = RLock()

    def get(self, evaluation_id: str) -> EvaluationManifest | None:
        with self._lock:
            manifest = self._manifests.get(evaluation_id)
            return None if manifest is None else manifest.model_copy(deep=True)

    def get_policy(self, evaluation_id: str) -> EvaluationPolicy | None:
        with self._lock:
            policy = self._policies.get(evaluation_id)
            return None if policy is None else policy.model_copy(deep=True)

    def save(self, manifest: EvaluationManifest, policy: EvaluationPolicy) -> None:
        with self._lock:
            existing = self._manifests.get(manifest.evaluation_id)
            if existing is not None and existing.result_hash != manifest.result_hash:
                raise EvaluationServiceError(
                    "evaluation_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self._manifests[manifest.evaluation_id] = manifest.model_copy(deep=True)
            self._policies[manifest.evaluation_id] = policy.model_copy(deep=True)

    def list(self) -> tuple[EvaluationManifest, ...]:
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._manifests.values())


class EvaluationProvider(Protocol):
    def evaluate(self, candidate: CandidateEnvelope, policy: EvaluationPolicy) -> Mapping[str, Any]: ...


class SemanticReviewer(Protocol):
    def review(self, candidate: CandidateEnvelope, policy: EvaluationPolicy) -> Mapping[str, Any]: ...


class EvaluationService:
    """Create queryable, replayable manifests from real candidates."""

    def __init__(
        self,
        candidate_service: CandidateService,
        *,
        repository: EvaluationRepository | None = None,
        evaluator: EvaluationProvider | Callable[[CandidateEnvelope, EvaluationPolicy], Mapping[str, Any]] | None = None,
        semantic_reviewer: SemanticReviewer | Callable[[CandidateEnvelope, EvaluationPolicy], Mapping[str, Any]] | None = None,
        duplicate_index: DuplicateClusterIndex | None = None,
    ) -> None:
        self.candidate_service = candidate_service
        self.repository = repository or InMemoryEvaluationRepository()
        self.evaluator = evaluator
        self.semantic_reviewer = semantic_reviewer
        self.duplicate_index = duplicate_index or DuplicateClusterIndex()
        self._lock = RLock()

    def evaluate(self, candidate_id: str, policy: EvaluationPolicy) -> EvaluationManifest:
        try:
            if not isinstance(policy, EvaluationPolicy):
                policy = EvaluationPolicy.model_validate(policy)
        except PydanticValidationError as exc:
            raise EvaluationServiceError("evaluation policy is invalid") from exc
        candidate = self.candidate_service.get(candidate_id)
        evaluation_id = self._evaluation_id(candidate, policy)
        with self._lock:
            existing = self.repository.get(evaluation_id)
            if existing is not None:
                if existing.candidate_id != candidate.candidate_id or existing.input_hash != candidate.input_hash:
                    raise EvaluationServiceError(
                        "registered evaluation does not match candidate input",
                        ErrorCode.CONFLICTING_ASSERTIONS.value,
                    )
                return existing

            result = self._call_provider(self.evaluator, candidate, policy)
            review = self._call_provider(self.semantic_reviewer, candidate, policy)
            merged = dict(result)
            if review:
                merged.update(review)
            duplicate_assessment = merged.get("duplicate")
            observation = self.duplicate_index.observe(
                candidate.candidate_id,
                payload_hash=candidate.payload_hash,
                assessment=duplicate_assessment if isinstance(duplicate_assessment, Mapping) else None,
            )
            reviewer_id = merged.get("semantic_reviewer")
            has_reviewer = bool(self.semantic_reviewer is not None and reviewer_id)
            if policy.require_semantic_reviewer and not has_reviewer:
                status = "INCOMPLETE"
                suitability = Suitability.UNKNOWN
            else:
                status = str(merged.get("status", "EVALUATED"))
                suitability = self._suitability(merged.get("suitability", Suitability.UNKNOWN))
            if merged.get("quarantine") is True:
                status = "QUARANTINED"
            manifest = EvaluationManifest(
                evaluation_id=evaluation_id,
                candidate_id=candidate.candidate_id,
                evaluator_id=policy.evaluator_id,
                evaluator_version=policy.evaluator_version,
                input_hash=candidate.input_hash,
                evidence_coverage=self._mapping(merged.get("evidence_coverage"), self._default_coverage(candidate)),
                duplicate_cluster_ref=observation.cluster_id,
                same_core=bool(merged.get("same_core", observation.same_core)),
                variant_of=merged.get("variant_of", observation.variant_of),
                surface_similarity_risk=merged.get("surface_similarity_risk", observation.surface_similarity_risk),
                suitability=suitability,
                required_adaptation=self._strings(merged.get("required_adaptation")),
                uncertainty=self._mapping(merged.get("uncertainty"), {}),
                conflicts=self._strings(merged.get("conflicts")),
                semantic_reviewer=reviewer_id if has_reviewer else None,
                status=status,
            )
            self.repository.save(manifest, policy)
            stored = self.repository.get(evaluation_id)
            if stored is None:
                raise EvaluationServiceError("evaluation registry did not retain manifest")
            return stored

    def get(self, evaluation_id: str) -> EvaluationManifest:
        manifest = self.repository.get(evaluation_id)
        if manifest is None:
            raise EvaluationServiceError(
                f"evaluation not found: {evaluation_id}", ErrorCode.NOT_FOUND.value
            )
        return manifest

    def get_for_candidate(self, candidate_id: str) -> tuple[EvaluationManifest, ...]:
        return tuple(item for item in self.repository.list() if item.candidate_id == candidate_id)

    def get_policy(self, evaluation_id: str) -> EvaluationPolicy:
        getter = getattr(self.repository, "get_policy", None)
        policy = getter(evaluation_id) if callable(getter) else None
        if policy is None:
            raise EvaluationServiceError(
                "evaluation policy is not registered", ErrorCode.NOT_FOUND.value
            )
        return policy

    @staticmethod
    def _evaluation_id(candidate: CandidateEnvelope, policy: EvaluationPolicy) -> str:
        seed = {
            "candidate_id": candidate.candidate_id,
            "input_hash": candidate.input_hash,
            "policy_hash": policy.policy_hash,
            "evaluator_id": policy.evaluator_id,
            "evaluator_version": policy.evaluator_version,
        }
        return f"evaluation-{sha256_hex(seed)[:48]}"

    @staticmethod
    def _call_provider(
        provider: Any,
        candidate: CandidateEnvelope,
        policy: EvaluationPolicy,
    ) -> dict[str, Any]:
        if provider is None:
            return {}
        method = getattr(provider, "evaluate", None) or getattr(provider, "review", None)
        value = method(candidate, policy) if callable(method) else provider(candidate, policy)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise EvaluationServiceError("evaluation provider must return a mapping")
        return dict(value)

    @staticmethod
    def _suitability(value: Any) -> Suitability:
        if isinstance(value, Suitability):
            return value
        try:
            return Suitability(str(value))
        except ValueError as exc:
            raise EvaluationServiceError("evaluation suitability is invalid") from exc

    @staticmethod
    def _mapping(value: Any, default: Mapping[str, Any]) -> Mapping[str, Any]:
        if value is None:
            return dict(default)
        if not isinstance(value, Mapping):
            raise EvaluationServiceError("evaluation mapping field is invalid")
        return dict(value)

    @staticmethod
    def _strings(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, Sequence):
            raise EvaluationServiceError("evaluation list field is invalid")
        return tuple(str(item) for item in value)

    @staticmethod
    def _default_coverage(candidate: CandidateEnvelope) -> Mapping[str, Any]:
        count = len(candidate.evidence_refs)
        return {"required": count, "covered": count, "complete": True}


__all__ = [
    "EvaluationProvider",
    "EvaluationRepository",
    "EvaluationService",
    "EvaluationServiceError",
    "InMemoryEvaluationRepository",
    "SemanticReviewer",
]
