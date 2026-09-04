"""
fxi.core - 基础内核模块
"""

from .config import FxiConfig, load_config
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

__all__ = [
    "FxiConfig",
    "load_config",
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
