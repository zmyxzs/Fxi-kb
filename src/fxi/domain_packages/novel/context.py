"""Side-effect-free rendering of validated novel objects into F1 context blocks."""

from __future__ import annotations

from typing import Any, Mapping

from fxi.knowledge.contracts import ContextBlock, ContextView, Scope, ViewSpec, _stable_hash, _token
from fxi.knowledge.objects import Claim, KnowledgeObject, Relation

from .schemas import NovelClaim, NovelRelation


def _validated_record(obj: Any) -> tuple[str, str, tuple[Any, ...], Scope, Mapping[str, Any]]:
    if isinstance(obj, KnowledgeObject):
        if obj.status != "APPROVED":
            raise ValueError("only approved KnowledgeObject instances can be rendered")
        return obj.object_id, obj.type_uri, obj.evidence_refs, obj.scope, obj.model_dump(mode="json")
    if isinstance(obj, NovelClaim):
        return obj.claim_id, obj.object_type_uri, obj.evidence_refs, obj.scope, obj.model_dump(mode="json")
    if isinstance(obj, Claim):
        view = NovelClaim.from_kernel(obj)
        return view.claim_id, view.object_type_uri, view.evidence_refs, view.scope, view.model_dump(mode="json")
    if isinstance(obj, NovelRelation):
        return obj.relation_id, "novel.relation@1", obj.evidence_refs, obj.scope, obj.model_dump(mode="json")
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
        return view.relation_id, "novel.relation@1", view.evidence_refs, view.scope, view.model_dump(mode="json")
    raise TypeError("context rendering requires a validated KnowledgeObject, Claim, or Relation")


def _view_scope(view: Any, fallback: Scope) -> Scope:
    if isinstance(view, ContextView):
        return view.scope
    if isinstance(view, ViewSpec):
        return fallback
    if isinstance(view, Mapping) and "scope" in view:
        return Scope.model_validate(view["scope"])
    if view is None or isinstance(view, Mapping):
        return fallback
    raise TypeError("view must be a validated ViewSpec/ContextView or mapping")


def render_context(obj: Any, view: Any = None) -> ContextBlock:
    """Render only validated, in-scope data; no claims are inferred."""

    object_ref, type_uri, evidence_refs, object_scope, content = _validated_record(obj)
    scope = _view_scope(view, object_scope)
    if scope.work_id != object_scope.work_id or scope.branch_id != object_scope.branch_id:
        raise ValueError("context view scope does not match object scope")
    if isinstance(view, ViewSpec) and view.type_uris and type_uri not in view.type_uris:
        raise ValueError("object type is not requested by the context view")
    if isinstance(view, ContextView) and (view.work_id != object_scope.work_id or view.branch_id != object_scope.branch_id):
        raise ValueError("context view work and branch do not match object")
    block_id = f"context-{_stable_hash({'object_ref': object_ref, 'type_uri': type_uri, 'scope': scope})[:40]}"
    return ContextBlock(
        block_id=_token(block_id, "block_id"),
        type_uri=type_uri,
        object_refs=(object_ref,),
        content=content,
        evidence_refs=evidence_refs,
        scope=scope,
    )


__all__ = ["render_context"]
