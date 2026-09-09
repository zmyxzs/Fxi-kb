"""In-memory branch/POD service for the domain-neutral F5 boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from pydantic import Field, model_validator

from fxi.core.exceptions import NotFoundError, ValidationError

from .as_of import AsOfError
from .branches import Branch, BranchPolicy, DivergenceAnchor
from .contracts import ContractModel, SourceSnapshotRef, _hash, _stable_hash, _token
from .objects import Claim, KnowledgeHead, KnowledgeVersion


class BranchServiceError(ValidationError):
    def __init__(self, message: str, *, code: str = "INVALID_SCOPE") -> None:
        super().__init__(message)
        self.code = code


BranchRef = Branch


class BranchContextRef(ContractModel):
    """Selector metadata proving that POD filtering was applied explicitly."""

    work_id: str
    branch_id: str
    parent_branch: str | None = None
    divergence_anchor_hash: str | None = None
    post_divergence_filtered: bool
    retained_claim_refs: tuple[str, ...] = ()
    filtered_claim_refs: tuple[str, ...] = ()
    context_hash: str = ""

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in (
            "work_id", "branch_id", "parent_branch", "divergence_anchor_hash",
            "post_divergence_filtered", "retained_claim_refs", "filtered_claim_refs",
        )}
        expected = _stable_hash(payload)
        supplied = result.get("context_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("context_hash does not match canonical branch context")
        result["context_hash"] = expected
        return result

    @property
    def branch_ref(self) -> str:
        return self.branch_id


def _copy_model(model: ContractModel) -> Any:
    return model.model_copy(deep=True)


def filter_post_divergence_claims(
    claims: Sequence[Claim],
    branch: BranchRef,
    *,
    narrative_orders: Mapping[str, int] | None = None,
    dynamic_claim_refs: Iterable[str] | None = None,
    static_claim_refs: Iterable[str] | None = None,
) -> tuple[Claim, ...]:
    """Filter post-POD dynamic claims while leaving static policy to callers.

    Claims without an explicit static marker are treated as dynamic.  This is
    deliberately fail-closed: a domain package must opt a claim into static
    inheritance instead of the generic kernel guessing from its predicate.
    """

    if not isinstance(branch, Branch):
        raise BranchServiceError("branch must be a BranchRef")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        raise BranchServiceError("claims must be a sequence")
    dynamic = None if dynamic_claim_refs is None else set(dynamic_claim_refs)
    static = set(static_claim_refs or ())
    if dynamic is not None and dynamic & static:
        raise BranchServiceError("a claim cannot be both static and dynamic")

    if branch.divergence is None or not branch.policy.filter_post_divergence:
        return tuple(claims)

    orders = dict(narrative_orders or {})
    retained: list[Claim] = []
    for claim in claims:
        if not isinstance(claim, Claim):
            raise BranchServiceError("claims must contain Claim instances")
        if claim.scope.work_id != branch.work_id:
            raise BranchServiceError("claim work_id does not match branch", code="INVALID_SCOPE")
        order = orders.get(claim.claim_id)
        if order is None and isinstance(claim.scope.as_of, int) and not isinstance(claim.scope.as_of, bool):
            order = claim.scope.as_of
        if order is None:
            raise BranchServiceError("post-POD filtering requires a narrative order", code="INVALID_SCOPE")
        if order <= branch.divergence.narrative_order:
            retained.append(claim)
            continue
        if claim.claim_id in static:
            retained.append(claim)
            continue
        if dynamic is None or claim.claim_id in dynamic:
            continue
        # Unknown post-POD claims remain blocked by default.
    return tuple(retained)


class BranchService:
    """Manage immutable branch refs and compare-and-set knowledge heads."""

    def __init__(
        self,
        *,
        created_by: str = "system",
        branches: dict[str, BranchRef] | None = None,
        heads: dict[tuple[str, str], KnowledgeHead] | None = None,
        versions: dict[str, KnowledgeVersion] | None = None,
        snapshot_resolver: Callable[[str], SourceSnapshotRef] | Any | None = None,
    ) -> None:
        try:
            self._created_by = _token(created_by, "created_by")
        except ValueError as exc:
            raise BranchServiceError("invalid created_by") from exc
        self._branches = branches if branches is not None else {}
        self._heads = heads if heads is not None else {}
        self._versions = versions if versions is not None else {}
        self._snapshot_resolver = snapshot_resolver

    def _validate_work(self, work_id: str) -> str:
        try:
            return _token(work_id, "work_id")
        except ValueError as exc:
            raise BranchServiceError("invalid work_id") from exc

    def _validate_divergence(self, work_id: str, divergence: DivergenceAnchor | None) -> None:
        if divergence is None or self._snapshot_resolver is None:
            return
        try:
            snapshot = (
                self._snapshot_resolver(divergence.source_snapshot_ref)
                if callable(self._snapshot_resolver)
                else self._snapshot_resolver.get(divergence.source_snapshot_ref)
            )
        except (KeyError, NotFoundError) as exc:
            raise BranchServiceError("divergence snapshot not found", code="NOT_FOUND") from exc
        if not isinstance(snapshot, SourceSnapshotRef):
            raise BranchServiceError("divergence snapshot not found", code="NOT_FOUND")
        if snapshot.work_id != work_id or snapshot.status.upper() != "PUBLISHED":
            raise BranchServiceError("divergence snapshot is outside work scope", code="INVALID_SCOPE")

    def create(
        self,
        work_id: str,
        parent_branch: str | None,
        divergence: DivergenceAnchor | None,
    ) -> BranchRef:
        work_id = self._validate_work(work_id)
        if parent_branch is not None:
            try:
                parent_branch = _token(parent_branch, "parent_branch")
            except ValueError as exc:
                raise BranchServiceError("invalid parent_branch") from exc
            parent = self._branches.get(parent_branch)
            if parent is None:
                raise BranchServiceError("parent branch not found", code="NOT_FOUND")
            if parent.work_id != work_id:
                raise BranchServiceError("parent branch is outside work scope", code="INVALID_SCOPE")
        if divergence is not None and not isinstance(divergence, DivergenceAnchor):
            raise BranchServiceError("divergence must be a DivergenceAnchor")
        if parent_branch is None and divergence is not None:
            raise BranchServiceError("a divergence anchor requires a parent branch")
        self._validate_divergence(work_id, divergence)
        seed = {
            "work_id": work_id,
            "parent_branch": parent_branch,
            "divergence": None if divergence is None else divergence.model_dump(mode="json"),
        }
        branch_id = "branch-main" if parent_branch is None else "branch-" + _stable_hash(seed)
        existing = self._branches.get(branch_id)
        if existing is not None:
            if existing.work_id != work_id or existing.model_dump(mode="json") != {
                **existing.model_dump(mode="json"),
            }:
                raise BranchServiceError("branch identity collision", code="IDEMPOTENCY_CONFLICT")
            return _copy_model(existing)
        branch = Branch(
            branch_id=branch_id,
            work_id=work_id,
            parent_branch=parent_branch,
            divergence=divergence,
            policy=BranchPolicy(),
            created_by=self._created_by,
        )
        self._branches[branch_id] = branch
        parent_version = None
        if parent_branch is not None:
            parent_version = self.head(work_id, parent_branch).knowledge_version
        version_id = "version-" + branch_id
        version = KnowledgeVersion(
            knowledge_version=version_id,
            work_id=work_id,
            branch_id=branch_id,
            parent_version=parent_version,
            actor=self._created_by,
        )
        self._versions[version_id] = version
        self._heads[(work_id, branch_id)] = KnowledgeHead(
            work_id=work_id,
            branch_id=branch_id,
            knowledge_version=version.knowledge_version,
            version_hash=version.version_hash,
            cas_revision=0,
        )
        return _copy_model(branch)

    def head(self, work_id: str, branch_id: str) -> KnowledgeHead:
        work_id = self._validate_work(work_id)
        try:
            branch_id = _token(branch_id, "branch_id")
        except ValueError as exc:
            raise BranchServiceError("invalid branch_id") from exc
        branch = self._branches.get(branch_id)
        if branch is None or branch.work_id != work_id:
            raise BranchServiceError("branch not found", code="NOT_FOUND")
        try:
            return _copy_model(self._heads[(work_id, branch_id)])
        except KeyError as exc:
            raise BranchServiceError("branch head not found", code="NOT_FOUND") from exc

    def compare_and_set_head(
        self,
        work_id: str,
        branch_id: str,
        expected: KnowledgeHead | int,
        replacement: KnowledgeHead,
    ) -> KnowledgeHead:
        current = self.head(work_id, branch_id)
        if isinstance(expected, KnowledgeHead):
            matches = expected == current
        elif isinstance(expected, int) and not isinstance(expected, bool):
            matches = expected == current.cas_revision
        else:
            matches = False
        if not matches:
            raise BranchServiceError("knowledge head CAS mismatch", code="STALE_VERSION")
        if not isinstance(replacement, KnowledgeHead):
            raise BranchServiceError("replacement must be a KnowledgeHead")
        if replacement.work_id != work_id or replacement.branch_id != branch_id:
            raise BranchServiceError("replacement head is outside scope", code="INVALID_SCOPE")
        if replacement.cas_revision != current.cas_revision + 1:
            raise BranchServiceError("replacement CAS revision is not monotonic", code="STALE_VERSION")
        self._heads[(work_id, branch_id)] = _copy_model(replacement)
        return _copy_model(replacement)

    cas_head = compare_and_set_head
    update_head = compare_and_set_head

    def context_ref(
        self,
        work_id: str,
        branch_id: str,
        claims: Sequence[Claim],
        **filter_options: Any,
    ) -> BranchContextRef:
        branch = self._branches.get(branch_id)
        if branch is None or branch.work_id != work_id:
            raise BranchServiceError("branch not found", code="NOT_FOUND")
        retained = filter_post_divergence_claims(claims, branch, **filter_options)
        retained_ids = {claim.claim_id for claim in retained}
        return BranchContextRef(
            work_id=work_id,
            branch_id=branch_id,
            parent_branch=branch.parent_branch,
            divergence_anchor_hash=None if branch.divergence is None else branch.divergence.anchor_hash,
            post_divergence_filtered=branch.divergence is not None and branch.policy.filter_post_divergence,
            retained_claim_refs=tuple(claim.claim_id for claim in retained),
            filtered_claim_refs=tuple(claim.claim_id for claim in claims if claim.claim_id not in retained_ids),
        )


__all__ = [
    "BranchContextRef",
    "BranchRef",
    "BranchService",
    "BranchServiceError",
    "DivergenceAnchor",
    "filter_post_divergence_claims",
]
