"""Deletion-aware projections from approved generic kernel records."""

from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator

from fxi.knowledge.contracts import ContractModel, _stable_hash, _token
from fxi.knowledge.objects import Claim, KnowledgeObject, Relation

from .schemas import NovelClaim, NovelRelation


class ProjectionRecord(ContractModel):
    record_id: str
    work_id: str
    branch_id: str
    type_uri: str
    object_ref: str
    content: Any
    deletable: bool = True
    deletion_key: str
    record_hash: str = ""

    @field_validator("record_id", "work_id", "branch_id", "type_uri", "object_ref", "deletion_key")
    @classmethod
    def validate_projection_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @model_validator(mode="after")
    def calculate_record_hash(self) -> "ProjectionRecord":
        payload = {
            "record_id": self.record_id,
            "work_id": self.work_id,
            "branch_id": self.branch_id,
            "type_uri": self.type_uri,
            "object_ref": self.object_ref,
            "content": self.content,
            "deletable": self.deletable,
            "deletion_key": self.deletion_key,
        }
        expected = _stable_hash(payload)
        if self.record_hash not in ("", expected):
            raise ValueError("record_hash does not match canonical projection")
        object.__setattr__(self, "record_hash", expected)
        return self


def _record(obj: Any) -> tuple[str, str, str, str, Any]:
    if isinstance(obj, KnowledgeObject):
        if obj.status != "APPROVED":
            raise ValueError("only approved KnowledgeObject instances can be projected")
        return obj.object_id, obj.work_id, obj.branch_id, obj.type_uri, obj.model_dump(mode="json")
    if isinstance(obj, NovelClaim):
        return obj.claim_id, obj.scope.work_id, obj.scope.branch_id, obj.object_type_uri, obj.model_dump(mode="json")
    if isinstance(obj, Claim):
        view = NovelClaim.from_kernel(obj)
        return view.claim_id, view.scope.work_id, view.scope.branch_id, view.object_type_uri, view.model_dump(mode="json")
    if isinstance(obj, NovelRelation):
        return obj.relation_id, obj.scope.work_id, obj.scope.branch_id, "novel.relation@1", obj.model_dump(mode="json")
    if isinstance(obj, Relation):
        view = NovelRelation(
            relation_id=obj.relation_id,
            relation_type=obj.relation_type,
            subject_ref=obj.subject_ref,
            object_ref=obj.object_ref,
            scope=obj.scope,
            evidence_refs=obj.evidence_refs,
            status=obj.status,
        )
        return view.relation_id, view.scope.work_id, view.scope.branch_id, "novel.relation@1", view.model_dump(mode="json")
    raise TypeError("projection requires a validated KnowledgeObject, Claim, or Relation")


def project(obj: Any) -> tuple[ProjectionRecord, ...]:
    record_id, work_id, branch_id, type_uri, content = _record(obj)
    deletion_key = f"{work_id}:{branch_id}:{type_uri}:{record_id}"
    return (
        ProjectionRecord(
            record_id=f"projection-{record_id}",
            work_id=work_id,
            branch_id=branch_id,
            type_uri=type_uri,
            object_ref=record_id,
            content=content,
            deletion_key=deletion_key,
        ),
    )


__all__ = ["ProjectionRecord", "project"]
