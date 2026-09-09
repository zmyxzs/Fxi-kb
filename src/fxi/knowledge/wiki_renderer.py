"""Deterministic, read-only renderer for approved knowledge pages."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


class WikiRenderError(ValueError):
    """Raised when a page cannot be rendered from a structured record."""


def _json(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError) as exc:
        raise WikiRenderError("wiki content is not serializable") from exc
    return text.replace("```", "` ` `")


def _refs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (str(value),)
    if isinstance(value, Mapping):
        return tuple(str(key) for key in value)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


class WikiRenderer:
    """Render metadata and payload without invoking a model or writing facts."""

    renderer_id = "wiki-renderer"
    renderer_version = "v3-wiki-renderer-1"

    def render(
        self,
        record: Mapping[str, Any],
        *,
        knowledge_version: str | None = None,
        knowledge_version_hash: str | None = None,
        decision: str | None = None,
        conflicts: Sequence[str] = (),
        staleness: str = "FRESH",
    ) -> str:
        if not isinstance(record, Mapping):
            raise WikiRenderError("wiki record must be a mapping")
        object_ref = str(record.get("object_ref", record.get("record_id", "unknown")))
        type_uri = str(record.get("type_uri", "unknown"))
        version = knowledge_version or str(record.get("knowledge_version", "unknown"))
        version_hash = knowledge_version_hash or str(record.get("version_hash", "unknown"))
        evidence = _refs(record.get("evidence_refs", ()))
        decision_value = decision or str(record.get("decision", "NONE"))
        conflict_values = tuple(conflicts) or _refs(record.get("conflicts", record.get("conflict_refs", ())))
        status = str(record.get("status", "APPROVED"))
        payload = record.get("content", record.get("payload", record))
        scope = record.get("scope", {})
        return "\n".join(
            (
                f"# Knowledge object `{object_ref}`",
                "",
                f"- Type: `{type_uri}`",
                f"- Status: `{status}`",
                f"- Work: `{record.get('work_id', scope.get('work_id', 'unknown') if isinstance(scope, Mapping) else 'unknown')}`",
                f"- Branch: `{record.get('branch_id', scope.get('branch_id', 'unknown') if isinstance(scope, Mapping) else 'unknown')}`",
                f"- Knowledge version: `{version}`",
                f"- Knowledge version hash: `{version_hash}`",
                f"- Evidence: `{', '.join(evidence) if evidence else 'NONE'}`",
                f"- Decision: `{decision_value}`",
                f"- Conflict: `{', '.join(conflict_values) if conflict_values else 'NONE'}`",
                f"- Staleness: `{staleness}`",
                "",
                "## Structured content",
                "",
                "```json",
                _json(payload),
                "```",
            )
        )

    render_page = render


__all__ = ["WikiRenderError", "WikiRenderer"]
