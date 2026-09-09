"""Novel domain package public surface."""

from .conflicts import (
    ClaimLifecycle,
    ConflictCode,
    ConflictDiagnostic,
    ConflictResult,
    NovelConflictPolicy,
    classify_claim_conflict,
    diagnose_temporal_order,
    validate_claim_transition,
)
from .extractors import (
    ClaimExtractionInput,
    ExtractionInput,
    NovelExtractor,
    RelationExtractionInput,
    extract_candidate,
    extract_claim,
    extract_claim_view,
    extract_relation,
)
from .manifest import (
    PACKAGE_ID,
    PACKAGE_VERSION,
    SUPPORTED_TYPE_URIS,
    NovelDomainPackage,
    NovelPackageManifest,
    register_novel,
)
from .schemas import *

__all__ = [
    "NovelDomainPackage",
    "NovelPackageManifest",
    "PACKAGE_ID",
    "PACKAGE_VERSION",
    "SUPPORTED_TYPE_URIS",
    "register_novel",
    "NovelExtractor",
    "ClaimLifecycle",
    "ExtractionInput",
    "ClaimExtractionInput",
    "RelationExtractionInput",
    "extract_candidate",
    "extract_claim",
    "extract_claim_view",
    "extract_relation",
    "ConflictCode",
    "ConflictDiagnostic",
    "ConflictResult",
    "NovelConflictPolicy",
    "classify_claim_conflict",
    "validate_claim_transition",
    "diagnose_temporal_order",
    *[name for name in globals() if name.endswith("Payload") or name in {"NovelClaim", "NovelRelation", "NovelPayloadEnvelope", "ClaimStatus", "ClaimPerspective", "StateRule"}],
]
