"""
fxi.core.exceptions - 统一全局异常继承树
"""


class FxiError(Exception):
    """Fxi 顶层基类异常"""
    def __init__(self, message: str, code: str = "FXI_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class StorageError(FxiError):
    """存储层读写与并发异常"""
    def __init__(self, message: str, code: str = "STORAGE_ERROR"):
        super().__init__(message, code)


class FileLockedError(StorageError):
    """文件并发锁定或已被外部程序修改冲突"""
    def __init__(self, message: str):
        super().__init__(message, code="FILE_LOCKED_ERROR")


class CorruptedDataError(StorageError):
    """数据损坏或非法格式"""
    def __init__(self, message: str):
        super().__init__(message, code="CORRUPTED_DATA_ERROR")


class ValidationError(FxiError):
    """数据结构强契约校验失败"""
    def __init__(self, message: str):
        super().__init__(message, code="VALIDATION_ERROR")


class GatewayError(FxiError):
    """大模型网关调用失败"""
    def __init__(self, message: str, code: str = "GATEWAY_ERROR"):
        super().__init__(message, code)


class ModelTimeoutError(GatewayError):
    """模型调用超时"""
    def __init__(self, message: str):
        super().__init__(message, code="MODEL_TIMEOUT_ERROR")


class SchemaRepairFailedError(GatewayError):
    """模型输出结构化修复重试后依然无法解析"""
    def __init__(self, message: str):
        super().__init__(message, code="SCHEMA_REPAIR_FAILED")


class RuleViolationError(FxiError):
    """业务与故事世界规则校验失败"""
    def __init__(self, message: str, code: str = "RULE_VIOLATION"):
        super().__init__(message, code)


class OOCConflictError(RuleViolationError):
    """角色言行与性格/认知阶段严重背离"""
    def __init__(self, message: str):
        super().__init__(message, code="OOC_CONFLICT")


class CausalConflictError(RuleViolationError):
    """试图沿用已在同人中失效的原著因果"""
    def __init__(self, message: str):
        super().__init__(message, code="CAUSAL_CONFLICT")


class ResourceDeficitError(RuleViolationError):
    """领地、组织或据点资源不足导致透支"""
    def __init__(self, message: str):
        super().__init__(message, code="RESOURCE_DEFICIT")


class SkillCastIllegalError(RuleViolationError):
    """技能释放非法 (冷却中/蓝量透支/前置未解锁)"""
    def __init__(self, message: str):
        super().__init__(message, code="SKILL_CAST_ILLEGAL")


class OwnershipConflictError(RuleViolationError):
    """试图操作已遗失、被夺或未持有的物品"""
    def __init__(self, message: str):
        super().__init__(message, code="OWNERSHIP_CONFLICT")


class NotFoundError(FxiError):
    """查询的实体、作品、章节、时间线或快照不存在"""
    def __init__(self, message: str):
        super().__init__(message, code="NOT_FOUND")
