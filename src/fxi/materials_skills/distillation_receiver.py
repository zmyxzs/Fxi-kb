"""接收外部学习成果为候选包。

接收器是生成边界：它只创建隔离的 candidate 记录，绝不把模型或外部
学习结果直接写入正式 ``skills`` 目录。正式发布只能走 CandidateStore 的
显式审批路径。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ValidationError
from fxi.materials_skills.candidate_store import CandidateStore


class DistillationReceiver:
    """将 novel-Skill 学习成果规范化后保存为待审核候选。"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        *,
        candidate_store: Optional[CandidateStore] = None,
        require_scope: bool = True,
    ):
        self.config = config or load_config()
        self.candidate_store = candidate_store or CandidateStore(self.config, require_scope=require_scope)

    def ingest_skill_package(
        self,
        payload: Mapping[str, Any],
        *,
        actor_id: Optional[str] = None,
        work_id: Optional[str] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        input_hash: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """接收学习结果并返回候选引用，不产生正式知识副作用。

        ``actor_id`` 必须由受控调用边界显式传入，不能从 payload 中读取。
        work/source/hash 同时保存在候选记录中，缺失或互相矛盾时拒绝接收。
        """

        if not isinstance(payload, Mapping):
            raise ValidationError("技法包必须是对象")
        if payload.get("actor_id") is not None or payload.get("submitted_by") is not None:
            raise ValidationError("提交身份必须通过 actor_id 参数提供，不能由 payload 自报")
        if actor_id is None or not isinstance(actor_id, str) or not actor_id.strip():
            raise ValidationError("接收候选必须提供显式 actor_id")

        candidate_key = idempotency_key or payload.get("idempotency_key")
        existing = None
        repository = getattr(self.candidate_store, "repository", None)
        if isinstance(candidate_key, str) and candidate_key.strip() and repository is not None:
            existing = repository.get_by_idempotency(candidate_key)
        record = self.candidate_store.submit(
            payload,
            work_id=work_id,
            source_id=source_id,
            source_version=source_version,
            input_hash=input_hash,
            submitted_by=actor_id,
            idempotency_key=idempotency_key,
            require_scope=True,
        )
        return {
            "status": record.status,
            "candidate_id": record.candidate_id,
            "skill_slug": record.slug,
            "candidate_version": record.version,
            "candidate_hash": record.package_hash,
            "work_id": record.work_id,
            "source_id": record.source_id,
            "source_version": record.source_version,
            "input_hash": record.input_hash,
            "submitted_by": record.submitted_by,
            "formal_knowledge_written": False,
            "requires_review": True,
            "binding_complete": record.binding_complete,
            "rules_count": len(record.package.get("rules", [])),
            "anti_patterns_count": len(record.package.get("anti_patterns", [])),
            "idempotent_replay": existing is not None,
        }


__all__ = ["DistillationReceiver"]
