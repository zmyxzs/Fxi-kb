"""Strict, domain-neutral selectors for compiled knowledge views.

Selectors are request metadata only.  They never contain a domain-specific
field and they do not read or mutate a repository.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from .as_of import AsOf, AsOfError, canonical_as_of, compare_as_of
from .branch_service import BranchContextRef
from .contracts import ContractModel, ViewSpec, _non_empty, _token
from .objects import KnowledgeHead
from .source_graph import SourceCompositeRef


def _refs(value: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a sequence of identifiers")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be a sequence of identifiers") from exc
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(_token(item, label) for item in values)


class SelectorError(ValueError):
    """Raised by selector helpers before a provider is consulted."""


class ViewSelector(ContractModel):
    """The complete, deterministic input to a context projection.

    The aliases keep the wire vocabulary readable (``source_composite`` and
    ``budget``) while retaining the same explicit names used by F1 contracts.
    Pydantic's ``extra='forbid'`` from ``ContractModel`` makes accidental
    domain-shaped request fields fail closed.
    """

    work_id: str
    branch_id: str
    as_of: int | str
    purpose: str
    type_uris: tuple[str, ...] = ()
    object_refs: tuple[str, ...] = ()
    required_claim_refs: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("required_claim_refs", "required_claims")
    )
    required_evidence_refs: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("required_evidence_refs", "required_evidence")
    )
    source_ids: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("capabilities", "required_capabilities")
    )
    pov_id: str | None = Field(default=None, validation_alias=AliasChoices("pov_id", "pov"))
    budget_tokens: int | None = Field(
        default=None, gt=0, validation_alias=AliasChoices("budget_tokens", "budget")
    )
    style_selection: Mapping[str, Any] | str | None = None
    source_composite_ref: SourceCompositeRef | None = Field(
        default=None,
        validation_alias=AliasChoices("source_composite_ref", "source_composite"),
    )
    branch_context_ref: BranchContextRef | None = Field(
        default=None,
        validation_alias=AliasChoices("branch_context_ref", "branch_context"),
    )
    knowledge_head: KnowledgeHead | None = Field(
        default=None, validation_alias=AliasChoices("knowledge_head", "knowledge_head_ref")
    )

    @field_validator("work_id", "branch_id")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        return _non_empty(value, "purpose")

    @field_validator("as_of", mode="before")
    @classmethod
    def validate_as_of(cls, value: Any) -> int | str:
        if isinstance(value, (AsOf,)) or hasattr(value, "value") and type(value).__name__ == "AsOf":
            value = value.value
        elif isinstance(value, Mapping) and set(value) <= {"value", "source_version", "as_of_hash"}:
            value = value.get("value")
        try:
            return canonical_as_of(value)
        except (AsOfError, TypeError, ValueError) as exc:
            raise ValueError("as_of must be a valid integer or ISO timestamp") from exc

    @field_validator("type_uris", "object_refs", "required_claim_refs", "required_evidence_refs", "source_ids", "capabilities")
    @classmethod
    def validate_ref_lists(cls, value: Sequence[str], info: Any) -> tuple[str, ...]:
        return _refs(value, info.field_name)

    @field_validator("pov_id")
    @classmethod
    def validate_pov(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "pov_id")

    @field_validator("style_selection")
    @classmethod
    def validate_style(cls, value: Mapping[str, Any] | str | None) -> Mapping[str, Any] | str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return _non_empty(value, "style_selection")
        if not isinstance(value, Mapping):
            raise ValueError("style_selection must be a mapping, string, or None")
        return dict(value)

    @model_validator(mode="after")
    def validate_cross_scope(self) -> "ViewSelector":
        composite = self.source_composite_ref
        if composite is not None:
            if (composite.work_id, composite.branch_id) != (self.work_id, self.branch_id):
                raise ValueError("source composite is outside selector scope")
            try:
                if compare_as_of(composite.as_of, self.as_of) != 0:
                    raise ValueError("source composite as_of does not match selector")
            except AsOfError as exc:
                raise ValueError("source composite and selector as_of are incomparable") from exc
            if self.source_ids and not set(self.source_ids).issubset(
                {item.source_id for item in composite.contributions}
            ):
                raise ValueError("selector source_ids are absent from source composite")
        head = self.knowledge_head
        if head is not None and (head.work_id, head.branch_id) != (self.work_id, self.branch_id):
            raise ValueError("knowledge head is outside selector scope")
        branch_context = self.branch_context_ref
        if branch_context is not None and (branch_context.work_id, branch_context.branch_id) != (
            self.work_id,
            self.branch_id,
        ):
            raise ValueError("branch context is outside selector scope")
        return self

    @property
    def source_composite(self) -> SourceCompositeRef | None:
        return self.source_composite_ref

    @property
    def pov(self) -> str | None:
        return self.pov_id

    @property
    def budget(self) -> int | None:
        return self.budget_tokens

    def to_view_spec(self) -> ViewSpec:
        """Project only the fields understood by the frozen F1 ``ViewSpec``."""

        return ViewSpec(
            purpose=self.purpose,
            type_uris=self.type_uris,
            object_refs=self.object_refs,
            required_claim_refs=self.required_claim_refs,
            source_ids=self.source_ids,
            pov_id=self.pov_id,
            budget_tokens=self.budget_tokens,
        )


__all__ = ["SelectorError", "ViewSelector"]
