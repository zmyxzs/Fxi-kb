"""兼容/测试用的章节提交服务。

生产章节提交唯一入口是 :func:`fxi.api.router_v2.commit_chapter`，它负责
SQLite 事务、版本 CAS、审批消费和正式投影。本模块保留小型协议实现供
单元测试和无数据库适配使用，不参与生产 API 路由，也不应被当作第二条
生产提交链路。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from uuid import uuid4

from fxi.core.exceptions import StorageError, ValidationError
from fxi.core.canonical import canonical_json, sha256_hex
from fxi.timeline.writing_review import (
    ReviewRejectedError,
    WritingReviewService,
    normalize_dependency_versions,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return canonical_json(value)


def content_hash(value: Any) -> str:
    return sha256_hex(value)


class ChapterCommitError(ValidationError):
    """章节提交没有满足完整性、审批或投影契约。"""


class CommitConflictError(ChapterCommitError):
    """幂等键、版本或目标绑定发生冲突。"""


@dataclass(frozen=True)
class CommitRecord:
    commit_id: str
    work_id: str
    proposal_id: str
    proposal_hash: str
    source_id: str
    source_version: str
    chapter_index: int
    chapter_version: str
    text: str
    text_hash: str
    plan_hash: str
    context_hash: str
    review_id: str
    approval_id: str
    actor_id: str
    expected_knowledge_version: str
    new_knowledge_version: str
    idempotency_key: str
    request_fingerprint: str
    chapter: dict[str, Any]
    state_changes: tuple[dict[str, Any], ...] = ()
    causal_events: tuple[dict[str, Any], ...] = ()
    character_knowledge: tuple[dict[str, Any], ...] = ()
    created_at: str = ""
    dependency_versions: tuple[tuple[str, Optional[str]], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state_changes"] = [dict(item) for item in self.state_changes]
        data["causal_events"] = [dict(item) for item in self.causal_events]
        data["character_knowledge"] = [dict(item) for item in self.character_knowledge]
        data["dependency_versions"] = dict(self.dependency_versions)
        return data


@dataclass(frozen=True)
class CommitReceipt:
    commit_id: str
    new_knowledge_version: str
    chapter_version: str
    text_hash: str
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CommitProjection(Protocol):
    """正文、状态、因果和角色认知投影的原子边界。"""

    def apply_commit(self, record: CommitRecord) -> None: ...


class CommitStore(Protocol):
    """提交记录及知识版本头的最小持久化协议。"""

    def get_by_idempotency(self, work_id: str, idempotency_key: str) -> Optional[CommitRecord]: ...

    def get_by_commit_id(self, commit_id: str) -> Optional[CommitRecord]: ...

    def current_version(self, work_id: str) -> Optional[str]: ...

    def apply_commit(
        self,
        record: CommitRecord,
        apply_projection: Callable[[CommitRecord], None],
    ) -> CommitRecord: ...


class InMemoryCommitProjection:
    """测试和本地适配用的原子投影；不连接 SQLite。"""

    def __init__(self) -> None:
        self.commits: dict[str, CommitRecord] = {}
        self.chapters: dict[tuple[str, int], dict[str, Any]] = {}
        self.state_changes: list[dict[str, Any]] = []
        self.causal_events: list[dict[str, Any]] = []
        self.character_knowledge: list[dict[str, Any]] = []

    def apply_commit(self, record: CommitRecord) -> None:
        existing = self.commits.get(record.commit_id)
        if existing:
            if existing.request_fingerprint != record.request_fingerprint:
                raise CommitConflictError("commit_id 已绑定不同提交载荷")
            return
        chapter_key = (record.work_id, record.chapter_index)
        if chapter_key in self.chapters:
            existing_chapter = self.chapters[chapter_key]
            if existing_chapter.get("text_hash") != record.text_hash:
                raise CommitConflictError("同作品同章节已存在不同正文")
            return
        # 先在局部副本完成所有结构化校验，再一次性替换集合，避免部分投影。
        next_state = list(self.state_changes)
        next_causal = list(self.causal_events)
        next_knowledge = list(self.character_knowledge)
        next_state.extend(dict(item) for item in record.state_changes)
        next_causal.extend(dict(item) for item in record.causal_events)
        next_knowledge.extend(dict(item) for item in record.character_knowledge)
        self.chapters[chapter_key] = dict(record.chapter)
        self.state_changes = next_state
        self.causal_events = next_causal
        self.character_knowledge = next_knowledge
        self.commits[record.commit_id] = record


class InMemoryCommitStore:
    """带锁的幂等提交仓库，专供单元测试和无数据库适配。"""

    def __init__(self) -> None:
        self._records_by_key: dict[tuple[str, str], CommitRecord] = {}
        self._records_by_id: dict[str, CommitRecord] = {}
        self._heads: dict[str, str] = {}
        self._lock = threading.RLock()

    def get_by_idempotency(self, work_id: str, idempotency_key: str) -> Optional[CommitRecord]:
        with self._lock:
            return self._records_by_key.get((work_id, idempotency_key))

    def get_by_commit_id(self, commit_id: str) -> Optional[CommitRecord]:
        with self._lock:
            return self._records_by_id.get(commit_id)

    def current_version(self, work_id: str) -> Optional[str]:
        with self._lock:
            return self._heads.get(work_id)

    def apply_commit(
        self,
        record: CommitRecord,
        apply_projection: Callable[[CommitRecord], None],
    ) -> CommitRecord:
        with self._lock:
            existing = self._records_by_key.get((record.work_id, record.idempotency_key))
            if existing:
                if existing.request_fingerprint != record.request_fingerprint:
                    raise CommitConflictError("同幂等键绑定了不同提交载荷")
                return existing
            existing_id = self._records_by_id.get(record.commit_id)
            if existing_id:
                if existing_id.request_fingerprint != record.request_fingerprint:
                    raise CommitConflictError("commit_id 已绑定不同提交载荷")
                return existing_id
            current = self._heads.get(record.work_id)
            if current is not None and current != record.expected_knowledge_version:
                raise CommitConflictError("知识库版本在提交期间发生变化")
            # 只有投影完全成功后才登记提交和新版本；异常向上抛出，不返回成功。
            apply_projection(record)
            self._records_by_key[(record.work_id, record.idempotency_key)] = record
            self._records_by_id[record.commit_id] = record
            self._heads[record.work_id] = record.new_knowledge_version
            return record


class ChapterCommitService:
    """测试/兼容适配：校验后以 ``commit_id`` 应用内存投影。

    该服务不是生产提交入口；生产调用必须经过
    :func:`fxi.api.router_v2.commit_chapter`。保留它是为了维持无数据库
    适配器和现有单元测试的最小协议，不应在新的生产调用方中直接实例化。
    """

    compatibility_only = True

    def __init__(
        self,
        *,
        store: CommitStore,
        projection: Optional[CommitProjection] = None,
        review_service: Optional[WritingReviewService] = None,
        clock: Callable[[], str] = _now,
        id_factory: Callable[[], str] = lambda: f"commit-{uuid4().hex}",
    ):
        self.store = store
        self.projection = projection or InMemoryCommitProjection()
        self.review_service = review_service or WritingReviewService()
        self.clock = clock
        self.id_factory = id_factory

    def commit(
        self,
        request: Mapping[str, Any] | Any,
        *,
        review: Mapping[str, Any] | Any,
        approval: Mapping[str, Any] | Any,
        actor_id: str,
        proposal: Optional[Mapping[str, Any] | Any] = None,
        approval_consumer: Optional[Callable[[str, str], None]] = None,
    ) -> CommitReceipt:
        normalized = self._normalize_request(request, proposal)
        if not actor_id or not isinstance(actor_id, str):
            raise ChapterCommitError("章节提交必须提供 actor_id")
        self._validate_review(normalized, review)
        self._validate_approval(normalized, approval, actor_id)

        fingerprint = content_hash(
            {
                "work_id": normalized["work_id"],
                "proposal_id": normalized["proposal_id"],
                "proposal_hash": normalized["proposal_hash"],
                "source_id": normalized["source_id"],
                "source_version": normalized["source_version"],
                "chapter_index": normalized["chapter_index"],
                "chapter_version": normalized["chapter_version"],
                "text_hash": normalized["text_hash"],
                "text": normalized["text"],
                "plan_hash": normalized["plan_hash"],
                "context_hash": normalized["context_hash"],
                "review_id": normalized["review_id"],
                "approval_id": normalized["approval_id"],
                "actor_id": actor_id,
                "accepted_state_change_ids": normalized["accepted_state_change_ids"],
                "state_changes": normalized["state_changes"],
                "causal_events": normalized["causal_events"],
                "character_knowledge": normalized["character_knowledge"],
                "dependency_versions": normalized["dependency_versions"],
                "commit_id": normalized.get("commit_id"),
            }
        )
        existing = self.store.get_by_idempotency(normalized["work_id"], normalized["idempotency_key"])
        if existing:
            return self._replay_or_conflict(existing, fingerprint)
        requested_commit_id = normalized.get("commit_id")
        if requested_commit_id:
            existing_by_id = self.store.get_by_commit_id(requested_commit_id)
            if existing_by_id:
                return self._replay_or_conflict(existing_by_id, fingerprint)

        # An exact idempotent replay is already committed; only new work needs
        # the current-head precheck. The store repeats this CAS under its lock.
        self._validate_version(normalized)

        new_version = self._next_version(normalized["expected_knowledge_version"])
        record = CommitRecord(
            commit_id=requested_commit_id or self.id_factory(),
            work_id=normalized["work_id"],
            proposal_id=normalized["proposal_id"],
            proposal_hash=normalized["proposal_hash"],
            source_id=normalized["source_id"],
            source_version=normalized["source_version"],
            chapter_index=normalized["chapter_index"],
            chapter_version=normalized["chapter_version"],
            text=normalized["text"],
            text_hash=normalized["text_hash"],
            plan_hash=normalized["plan_hash"],
            context_hash=normalized["context_hash"],
            review_id=normalized["review_id"],
            approval_id=normalized["approval_id"],
            actor_id=actor_id,
            expected_knowledge_version=normalized["expected_knowledge_version"],
            new_knowledge_version=new_version,
            idempotency_key=normalized["idempotency_key"],
            request_fingerprint=fingerprint,
            chapter={
                "commit_id": requested_commit_id or "pending",
                "work_id": normalized["work_id"],
                "source_id": normalized["source_id"],
                "source_version": normalized["source_version"],
                "chapter_index": normalized["chapter_index"],
                "chapter_version": normalized["chapter_version"],
                "text": normalized["text"],
                "text_hash": normalized["text_hash"],
                "plan_hash": normalized["plan_hash"],
                "context_hash": normalized["context_hash"],
                "proposal_id": normalized["proposal_id"],
                "review_id": normalized["review_id"],
            },
            state_changes=tuple(normalized["state_changes"]),
            causal_events=tuple(normalized["causal_events"]),
            character_knowledge=tuple(normalized["character_knowledge"]),
            created_at=self.clock(),
            dependency_versions=tuple(normalized["dependency_versions"].items()),
        )
        # commit_id 需要在正文对象和所有结构化投影中稳定可见。
        chapter = dict(record.chapter)
        chapter["commit_id"] = record.commit_id
        record = CommitRecord(**{**asdict(record), "chapter": chapter})

        def apply(record_to_apply: CommitRecord) -> None:
            self._apply_projection(record_to_apply)
            if approval_consumer:
                approval_consumer(record_to_apply.approval_id, record_to_apply.commit_id)

        stored = self.store.apply_commit(record, apply)
        replay = stored.commit_id != record.commit_id or stored.request_fingerprint != fingerprint
        if replay and stored.request_fingerprint != fingerprint:
            raise CommitConflictError("幂等提交返回了不同载荷")
        return CommitReceipt(
            commit_id=stored.commit_id,
            new_knowledge_version=stored.new_knowledge_version,
            chapter_version=stored.chapter_version,
            text_hash=stored.text_hash,
            idempotent_replay=replay,
        )

    def _apply_projection(self, record: CommitRecord) -> None:
        try:
            self.projection.apply_commit(record)
        except Exception as exc:
            # 保留真实失败原因，绝不把部分投影伪装为成功。
            if isinstance(exc, (ChapterCommitError, ValidationError, StorageError)):
                raise
            raise StorageError(f"章节知识投影失败: {exc}") from exc

    def _validate_review(self, data: Mapping[str, Any], review: Mapping[str, Any] | Any) -> None:
        review_id = self._field(review, "report_id", self._field(review, "review_id", self._field(review, "id")))
        if review_id != data["review_id"]:
            raise ChapterCommitError("final_review_reference 与审核报告不匹配")
        try:
            self.review_service.assert_passed(
                review,
                text_hash=data["text_hash"],
                plan_hash=data["plan_hash"],
                context_hash=data["context_hash"],
                work_id=data["work_id"],
                source_id=data["source_id"],
                source_version=data["source_version"],
                knowledge_version=data["expected_knowledge_version"],
                dependency_versions=data["dependency_versions"],
            )
        except ReviewRejectedError:
            raise
        review_version = self._field(review, "knowledge_version")
        if review_version is not None and review_version != data["expected_knowledge_version"]:
            raise ChapterCommitError("审核报告知识版本与提交目标不匹配")

    def _validate_approval(self, data: Mapping[str, Any], approval: Mapping[str, Any] | Any, actor_id: str) -> None:
        approval_actor = self._field(approval, "actor_id")
        action = self._field(approval, "action")
        if approval_actor != actor_id:
            raise ChapterCommitError("审批 actor_id 与当前调用者不匹配")
        if action not in {"chapter_commit", "commit"}:
            raise ChapterCommitError("审批动作不是章节提交")
        if self._field(approval, "approval_id") != data["approval_id"]:
            raise ChapterCommitError("approval_id 不匹配")
        if self._field(approval, "target_id") != data["proposal_id"]:
            raise ChapterCommitError("审批 target_id 不是当前 proposal")
        if self._field(approval, "target_hash") != data["proposal_hash"]:
            raise ChapterCommitError("审批 target_hash 与 proposal_hash 不匹配")
        if self._field(approval, "expected_version") != data["expected_knowledge_version"]:
            raise ChapterCommitError("审批 expected_version 与提交版本不匹配")
        if self._field(approval, "work_id") not in (None, data["work_id"]):
            raise ChapterCommitError("审批 work_id 与提交作品不匹配")
        if self._field(approval, "validity", "VALID") != "VALID":
            raise ChapterCommitError("审批记录不是 VALID")
        if self._field(approval, "consumed_by"):
            raise ChapterCommitError("审批记录已被消费")

    def _validate_version(self, data: Mapping[str, Any]) -> None:
        current = self.store.current_version(data["work_id"])
        if current is not None and current != data["expected_knowledge_version"]:
            raise CommitConflictError("expected_knowledge_version 已过期")

    @classmethod
    def _normalize_request(
        cls,
        request: Mapping[str, Any] | Any,
        proposal: Optional[Mapping[str, Any] | Any],
    ) -> dict[str, Any]:
        proposal_obj = proposal or cls._field(request, "proposal", {}) or {}
        def merged(name: str, *, required: bool = True, default: Any = None) -> Any:
            value = cls._field(request, name)
            if value is None:
                value = cls._field(proposal_obj, name, default)
            if required and (value is None or value == ""):
                raise ChapterCommitError(f"提交缺少 {name}")
            return value

        text = merged("pending_text", required=False)
        if text is None:
            text = merged("text")
        text_hash = merged("text_hash")
        if content_hash(text) != text_hash:
            raise ChapterCommitError("text_hash 与提交正文不匹配")
        plan_hash = merged("plan_hash")
        context_hash = merged("context_hash")
        plan = cls._field(request, "plan", cls._field(proposal_obj, "plan"))
        context = cls._field(request, "context", cls._field(proposal_obj, "context"))
        if plan is not None and content_hash(plan) != plan_hash:
            raise ChapterCommitError("plan_hash 与 plan 内容不匹配")
        if context is not None and content_hash(context) != context_hash:
            raise ChapterCommitError("context_hash 与 context 内容不匹配")
        dependency_versions = normalize_dependency_versions(
            cls._field(request, "dependency_versions", cls._field(proposal_obj, "dependency_versions", {}))
        )
        state_proposals = cls._structured_list(
            cls._field(request, "state_change_proposals", cls._field(proposal_obj, "state_change_proposals", [])),
            "state_change_proposals",
        )
        accepted = cls._field(request, "accepted_state_change_ids", []) or []
        if isinstance(accepted, (str, bytes)) or not isinstance(accepted, Sequence):
            raise ChapterCommitError("accepted_state_change_ids 必须是列表")
        accepted_ids = [str(item) for item in accepted]
        by_id: dict[str, dict[str, Any]] = {}
        for item in state_proposals:
            item_id = item.get("id", item.get("state_change_id"))
            if not item_id:
                raise ChapterCommitError("状态变更缺少 id/state_change_id")
            if str(item_id) in by_id:
                raise ChapterCommitError(f"状态变更 ID 重复: {item_id}")
            by_id[str(item_id)] = dict(item)
        unknown = [item_id for item_id in accepted_ids if item_id not in by_id]
        if unknown:
            raise ChapterCommitError(f"提交包含未在提议中的状态变更: {unknown}")
        return {
            "work_id": cls._required(request, proposal_obj, "work_id"),
            "source_id": cls._required(request, proposal_obj, "source_id"),
            "source_version": cls._required(request, proposal_obj, "source_version"),
            "chapter_index": cls._positive_int(cls._required(request, proposal_obj, "chapter_index"), "chapter_index"),
            "chapter_version": cls._required(request, proposal_obj, "chapter_version"),
            "proposal_id": cls._required(request, proposal_obj, "proposal_id"),
            "proposal_hash": cls._required(request, proposal_obj, "proposal_hash"),
            "text": text,
            "text_hash": text_hash,
            "plan_hash": plan_hash,
            "context_hash": context_hash,
            "dependency_versions": dependency_versions,
            "review_id": cls._required(request, proposal_obj, "final_review_reference", aliases=("final_review_ref",)),
            "approval_id": cls._required(request, {}, "approval_id"),
            "expected_knowledge_version": cls._required(request, {}, "expected_knowledge_version"),
            "idempotency_key": cls._required(request, {}, "idempotency_key"),
            "commit_id": cls._field(request, "commit_id"),
            "accepted_state_change_ids": accepted_ids,
            "state_changes": [by_id[item_id] for item_id in accepted_ids],
            "causal_events": cls._structured_list(cls._field(request, "causal_events", cls._field(proposal_obj, "causal_events", [])), "causal_events"),
            "character_knowledge": cls._knowledge_list(cls._field(request, "character_knowledge", cls._field(proposal_obj, "character_knowledge", []))),
        }

    @staticmethod
    def _required(request: Mapping[str, Any] | Any, proposal: Mapping[str, Any] | Any, name: str, aliases: Sequence[str] = ()) -> Any:
        value = ChapterCommitService._field(request, name)
        if value is None:
            for alias in aliases:
                value = ChapterCommitService._field(request, alias)
                if value is not None:
                    break
        if value is None:
            value = ChapterCommitService._field(proposal, name)
        if value is None:
            for alias in aliases:
                value = ChapterCommitService._field(proposal, alias)
                if value is not None:
                    break
        if value is None or value == "":
            raise ChapterCommitError(f"提交缺少 {name}")
        return value

    @staticmethod
    def _field(item: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
        if isinstance(item, Mapping):
            return item.get(name, default)
        return getattr(item, name, default)

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ChapterCommitError(f"{field_name} 必须是正整数")
        return value

    @staticmethod
    def _structured_list(value: Any, field_name: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ChapterCommitError(f"{field_name} 必须是对象列表")
        result: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise ChapterCommitError(f"{field_name}[{index}] 必须是对象")
            result.append(dict(item))
        return result

    @staticmethod
    def _knowledge_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            return [{"character_id": key, "claims": claims} for key, claims in value.items()]
        return ChapterCommitService._structured_list(value, "character_knowledge")

    @staticmethod
    def _next_version(current: str) -> str:
        if not isinstance(current, str) or not current:
            raise ChapterCommitError("expected_knowledge_version 必须是非空字符串")
        prefix = current
        suffix = ""
        index = len(current)
        while index and current[index - 1].isdigit():
            index -= 1
        if index < len(current):
            prefix, suffix = current[:index], current[index:]
            return f"{prefix}{int(suffix) + 1}"
        return f"{current}.1"

    @staticmethod
    def _replay_or_conflict(existing: CommitRecord, fingerprint: str) -> CommitReceipt:
        if existing.request_fingerprint != fingerprint:
            raise CommitConflictError("幂等键绑定了不同提交载荷")
        return CommitReceipt(
            commit_id=existing.commit_id,
            new_knowledge_version=existing.new_knowledge_version,
            chapter_version=existing.chapter_version,
            text_hash=existing.text_hash,
            idempotent_replay=True,
        )


__all__ = [
    "ChapterCommitError",
    "ChapterCommitService",
    "CommitConflictError",
    "CommitProjection",
    "CommitReceipt",
    "CommitRecord",
    "CommitStore",
    "InMemoryCommitProjection",
    "InMemoryCommitStore",
    "content_hash",
]
