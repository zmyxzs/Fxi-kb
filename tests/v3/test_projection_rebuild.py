"""Clean-room lifecycle tests for disposable v3 projections."""

from __future__ import annotations

from hashlib import sha256

from fxi.knowledge.contracts import EvidenceRef, Scope
from fxi.knowledge.objects import Claim, KnowledgeVersion
from fxi.knowledge.projection_contracts import ProjectionError, ProjectionStatus
from fxi.knowledge.projection_runner import LexicalProjectionProvider, ProjectionRunner, _manifest


def _scope(work_id: str = "work-projection") -> Scope:
    return Scope(work_id=work_id, branch_id="branch-main", as_of=1, purpose="synthetic-test", actor="actor-test")


def _evidence(scope: Scope, source: str = "source-synthetic") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"evidence-{source}",
        source_snapshot_ref=f"snapshot-{source}",
        source_id=source,
        source_version="version-1",
        document_id="document-synthetic",
        start=0,
        end=8,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="normalization-1",
        scope=scope,
    )


def _claim(scope: Scope) -> Claim:
    return Claim(
        claim_id="claim-projection",
        subject_ref="node-a",
        predicate="connects",
        value="node-b",
        object_type_uri="type-a@1",
        scope=scope,
        evidence_refs=(_evidence(scope),),
    )


def _version(scope: Scope, *, status: str = "APPROVED") -> KnowledgeVersion:
    claim = _claim(scope)
    return KnowledgeVersion(
        knowledge_version="knowledge-v1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        claim_refs=(claim.claim_id,),
        source_snapshot_refs=("snapshot-source-synthetic",),
        status=status,
        actor=scope.actor,
    )


def test_projection_drop_rebuild_and_stale_keep_authority_hashes() -> None:
    scope = _scope()
    claim = _claim(scope)
    version = _version(scope)
    authority_hashes = (claim.claim_hash, version.version_hash)
    provider = LexicalProjectionProvider(records=(claim,))
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))

    first = runner.run("fts", version.knowledge_version)
    assert first.status == ProjectionStatus.BUILT.value
    assert first.source_refs == version.source_snapshot_refs
    assert first.knowledge_version_hash == version.version_hash
    assert first.record_count == 1
    assert (claim.claim_hash, version.version_hash) == authority_hashes

    dropped = runner.drop("fts", version.knowledge_version)
    assert dropped is not None
    assert dropped.status == ProjectionStatus.DROPPED.value
    assert runner.health("fts", version.knowledge_version).ready is False

    rebuilt = runner.rebuild("fts", version.knowledge_version)
    assert rebuilt.status == ProjectionStatus.BUILT.value
    assert rebuilt.projection_hash == first.projection_hash
    assert (claim.claim_hash, version.version_hash) == authority_hashes

    stale = runner.mark_stale("fts", version.knowledge_version)
    assert stale is not None
    assert stale.status == ProjectionStatus.STALE.value
    assert runner.health("fts", version.knowledge_version).stale is True


class _AlwaysFailProvider:
    projection_id = "synthetic-failing"
    projection_version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    def build(self, snapshot: KnowledgeVersion):
        self.calls += 1
        raise ProjectionError("synthetic provider failure")

    def drop(self, manifest) -> None:
        return None

    def health(self, manifest):
        return LexicalProjectionProvider().health(manifest)


def test_projection_failure_has_bounded_retries_and_dead_letter() -> None:
    scope = _scope("work-failure")
    version = _version(scope)
    provider = _AlwaysFailProvider()
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,), max_retries=2)

    manifest = runner.run(provider.projection_id, version)
    assert manifest.status == ProjectionStatus.FAILED.value
    assert manifest.error_code == "PROJECTION_FAILED"
    assert manifest.dead_letter is True
    assert manifest.retry_count == 2
    assert provider.calls == 3
    assert runner.dead_letters == [manifest]


def test_projection_requires_approved_knowledge_version() -> None:
    scope = _scope("work-unapproved")
    version = _version(scope, status="CANDIDATE")
    provider = LexicalProjectionProvider(records=(_claim(scope),))
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))

    manifest = runner.run("fts", version)
    assert manifest.status == ProjectionStatus.FAILED.value
    assert manifest.error_code == "APPROVAL_REQUIRED"
    assert manifest.dead_letter is True
