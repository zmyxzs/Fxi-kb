"""
fxi.core - 基础内核模块
"""

from .config import FxiConfig, load_config
from .canonical import canonical_json, sha256_hex
from .context import AppContext, UnitOfWork
from .constants import DEFAULT_CONTEXT_BUDGET, MAX_CONTEXT_BUDGET, VRAM_SAFE_LIMIT_MB
from .exceptions import (
    CausalConflictError,
    CorruptedDataError,
    FileLockedError,
    FxiError,
    GatewayError,
    ModelTimeoutError,
    NotFoundError,
    OOCConflictError,
    OwnershipConflictError,
    ResourceDeficitError,
    RuleViolationError,
    SchemaRepairFailedError,
    SkillCastIllegalError,
    StorageError,
    ValidationError,
)
from .types import (
    CausalStatus,
    ForeshadowingStatus,
    LifecycleAction,
    MetricStatus,
    SceneType,
    TaskType,
)
from .identifiers import validate_identifier, validate_segment, validate_work_id

__all__ = [
    "FxiConfig",
    "load_config",
    "AppContext",
    "UnitOfWork",
    "canonical_json",
    "sha256_hex",
    "validate_identifier",
    "validate_segment",
    "validate_work_id",
    "DEFAULT_CONTEXT_BUDGET",
    "MAX_CONTEXT_BUDGET",
    "VRAM_SAFE_LIMIT_MB",
    "FxiError",
    "StorageError",
    "FileLockedError",
    "CorruptedDataError",
    "ValidationError",
    "GatewayError",
    "ModelTimeoutError",
    "SchemaRepairFailedError",
    "RuleViolationError",
    "OOCConflictError",
    "CausalConflictError",
    "ResourceDeficitError",
    "SkillCastIllegalError",
    "OwnershipConflictError",
    "NotFoundError",
    "SceneType",
    "MetricStatus",
    "CausalStatus",
    "LifecycleAction",
    "ForeshadowingStatus",
    "TaskType",
]
