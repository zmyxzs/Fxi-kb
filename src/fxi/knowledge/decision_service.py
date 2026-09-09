"""Permission-aware decisions over registered evaluation manifests."""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Protocol, Sequence

from pydantic import ValidationError as PydanticValidationError

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .candidate_service import CandidateService
from .contracts import Actor, ErrorCode
from .decisions import Decision, DecisionAction
from .evaluation_service import EvaluationService


class DecisionServiceError(FxiError):
    """A decision failed authorization or lifecycle validation."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class DecisionRepository(Protocol):
    def get(self, decision_id: str) -> Decision | None: ...

    def save(self, decision: Decision) -> None: ...

    def list(self) -> Sequence[Decision]: ...


class InMemoryDecisionRepository:
    def __init__(self) -> None:
        self._decisions: dict[str, Decision] = {}
        self._lock = RLock()

    def get(self, decision_id: str) -> Decision | None:
        with self._lock:
            decision = self._decisions.get(decision_id)
            return None if decision is None else decision.model_copy(deep=True)

    def save(self, decision: Decision) -> None:
        with self._lock:
            existing = self._decisions.get(decision.decision_id)
            if existing is not None and existing.decision_hash != decision.decision_hash:
                raise DecisionServiceError(
                    "decision_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self._decisions[decision.decision_id] = decision.model_copy(deep=True)

    def list(self) -> tuple[Decision, ...]:
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._decisions.values())


class DecisionAuthorizer(Protocol):
    def authorize(self, actor: Actor, candidate: Any, action: DecisionAction) -> bool: ...


class DecisionService:
    """Record a decision only against a manifest present in the evaluation registry."""

    def __init__(
        self,
        candidate_service: CandidateService,
        evaluation_service: EvaluationService,
        *,
        repository: DecisionRepository | None = None,
        authorizer: DecisionAuthorizer | Callable[[Actor, Any, DecisionAction], bool] | None = None,
    ) -> None:
        self.candidate_service = candidate_service
        self.evaluation_service = evaluation_service
        self.repository = repository or InMemoryDecisionRepository()
        self.authorizer = authorizer
        self._lock = RLock()

    def decide(self, candidate_id: str, action: DecisionAction, actor: Actor) -> Decision:
        try:
            action = action if isinstance(action, DecisionAction) else DecisionAction(action)
            actor = actor if isinstance(actor, Actor) else Actor.model_validate(actor)
        except (ValueError, PydanticValidationError) as exc:
            raise DecisionServiceError("decision action or actor is invalid") from exc
        candidate = self.candidate_service.get(candidate_id)
        self._validate_actor(actor, candidate.work_id, candidate.branch_id)
        if self.authorizer is not None:
            method = getattr(self.authorizer, "authorize", None)
            allowed = method(actor, candidate, action) if callable(method) else self.authorizer(actor, candidate, action)
            if not allowed:
                raise DecisionServiceError(
                    "actor is not authorized for this decision",
                    ErrorCode.AUTHORIZATION_FAILED.value,
                )
        manifests = self.evaluation_service.get_for_candidate(candidate_id)
        if not manifests:
            raise DecisionServiceError(
                "decision requires a registered evaluation",
                ErrorCode.EVALUATION_REQUIRED.value,
            )
        manifest = manifests[-1]
        if action is DecisionAction.APPROVE and manifest.status == "INCOMPLETE":
            raise DecisionServiceError(
                "incomplete evaluation cannot be approved",
                ErrorCode.EVALUATION_REQUIRED.value,
            )
        decision_id = self._decision_id(candidate_id, manifest.evaluation_id, action, actor)
        decision = Decision(
            decision_id=decision_id,
            candidate_id=candidate_id,
            evaluation_ref=manifest.evaluation_id,
            action=action,
            actor=actor,
        )
        with self._lock:
            existing = self.repository.get(decision_id)
            if existing is not None:
                return existing
            self.repository.save(decision)
            stored = self.repository.get(decision_id)
            if stored is None:
                raise DecisionServiceError("decision repository did not retain decision")
            return stored

    def get(self, decision_id: str) -> Decision:
        decision = self.repository.get(decision_id)
        if decision is None:
            raise DecisionServiceError(
                f"decision not found: {decision_id}", ErrorCode.NOT_FOUND.value
            )
        return decision

    def get_for_candidate(self, candidate_id: str) -> tuple[Decision, ...]:
        return tuple(item for item in self.repository.list() if item.candidate_id == candidate_id)

    @staticmethod
    def _validate_actor(actor: Actor, work_id: str, branch_id: str) -> None:
        if actor.scope is None:
            raise DecisionServiceError(
                "decision actor must carry a bound scope",
                ErrorCode.INVALID_SCOPE.value,
            )
        if actor.scope.work_id != work_id or actor.scope.branch_id != branch_id:
            raise DecisionServiceError(
                "decision actor scope does not match candidate",
                ErrorCode.AUTHORIZATION_FAILED.value,
            )

    @staticmethod
    def _decision_id(candidate_id: str, evaluation_id: str, action: DecisionAction, actor: Actor) -> str:
        return f"decision-{sha256_hex({'candidate_id': candidate_id, 'evaluation_id': evaluation_id, 'action': action.value, 'actor': actor.model_dump(mode='json')})[:48]}"


__all__ = [
    "DecisionAuthorizer",
    "DecisionRepository",
    "DecisionService",
    "DecisionServiceError",
    "InMemoryDecisionRepository",
]
