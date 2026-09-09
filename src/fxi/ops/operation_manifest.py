"""Durable, idempotent operation records for the Fxi v3 boundary.

The manifest stores only request/result digests and the public result.  It is
not a source of knowledge facts and never stores authentication credentials.
Writes use a same-directory temporary file followed by ``os.replace`` so an
API process restart cannot expose a partially written manifest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError
from fxi.core.identifiers import validate_segment


class OperationManifestError(FxiError):
    """A durable operation record could not be read or replayed."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    kind: str
    status: str
    idempotency_key: str
    request_hash: str
    result_hash: str | None = None
    code: str = "OK"
    message: str = "OK"
    result: Any = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "result_hash": self.result_hash,
            "code": self.code,
            "message": self.message,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exception_fields(error: BaseException) -> tuple[str, str]:
    """Extract stable operation fields from domain and HTTP boundary errors."""

    code = getattr(error, "code", None)
    message: Any = str(error).strip() or type(error).__name__
    detail = getattr(error, "detail", None)
    if isinstance(detail, Mapping):
        code = code or detail.get("code")
        message = detail.get("message") or message
    return str(code or "INTERNAL_ERROR"), str(message).strip() or type(error).__name__


def _safe_key(value: str, label: str) -> str:
    try:
        return validate_segment(value, label)
    except Exception as exc:
        raise OperationManifestError(f"{label} is invalid", "INVALID_SCHEMA") from exc


def _record_from(value: Any) -> OperationRecord:
    if not isinstance(value, Mapping):
        raise OperationManifestError("operation manifest record is invalid", "CORRUPTED_DATA")
    required = ("operation_id", "kind", "status", "idempotency_key", "request_hash")
    if any(name not in value for name in required):
        raise OperationManifestError("operation manifest record is incomplete", "CORRUPTED_DATA")
    return OperationRecord(
        operation_id=str(value["operation_id"]),
        kind=str(value["kind"]),
        status=str(value["status"]),
        idempotency_key=str(value["idempotency_key"]),
        request_hash=str(value["request_hash"]),
        result_hash=None if value.get("result_hash") is None else str(value["result_hash"]),
        code=str(value.get("code", "OK")),
        message=str(value.get("message", "OK")),
        result=value.get("result"),
        created_at=str(value.get("created_at", "")),
        updated_at=str(value.get("updated_at", "")),
    )


class OperationManifest:
    """A small durable idempotency ledger for API operations.

    A ``PENDING`` record is deliberately not replayed as success.  Replaying
    it returns ``UNKNOWN_OUTCOME`` so callers can inspect or reconcile the
    operation instead of silently executing a possibly non-idempotent write a
    second time.
    """

    schema_version = "operation-manifest.v3"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.path.is_file():
            raise OperationManifestError("operation manifest path is not a file", "INVALID_SCOPE")
        if not self.path.exists():
            self._write_payload({"schema_version": self.schema_version, "operations": {}})

    def _read_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OperationManifestError("operation manifest is unreadable", "CORRUPTED_DATA") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            raise OperationManifestError("operation manifest schema is invalid", "CORRUPTED_DATA")
        operations = payload.get("operations")
        if not isinstance(operations, dict):
            raise OperationManifestError("operation manifest operations are invalid", "CORRUPTED_DATA")
        return payload

    def _write_payload(self, payload: Mapping[str, Any]) -> None:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        temporary: str | None = None
        operation_error: OperationManifestError | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
            ) as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = handle.name
            os.replace(temporary, self.path)
            temporary = None
            if self.path.read_bytes() != serialized:
                raise OperationManifestError("operation manifest write verification failed", "STORAGE_ERROR")
        except Exception as exc:
            if isinstance(exc, OperationManifestError):
                operation_error = exc
                raise
            operation_error = OperationManifestError("operation manifest write failed", "STORAGE_ERROR")
            raise operation_error from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError as cleanup_error:
                    message = f"operation manifest temporary cleanup failed: {cleanup_error}"
                    if operation_error is not None:
                        operation_error.add_note(message)
                    else:
                        raise OperationManifestError(message, "STORAGE_ERROR") from cleanup_error

    @staticmethod
    def _operation_id(kind: str, idempotency_key: str) -> str:
        return f"operation-{sha256_hex({'kind': kind, 'idempotency_key': idempotency_key})[:48]}"

    @staticmethod
    def _result_hash(result: Any) -> str:
        return sha256_hex(result)

    def get(self, kind: str, idempotency_key: str) -> OperationRecord | None:
        kind = _safe_key(kind, "kind")
        idempotency_key = _safe_key(idempotency_key, "idempotency_key")
        with self._lock:
            operations = self._read_payload()["operations"]
            value = operations.get(f"{kind}:{idempotency_key}")
            return None if value is None else _record_from(value)

    def list(self) -> tuple[OperationRecord, ...]:
        with self._lock:
            records = tuple(_record_from(item) for item in self._read_payload()["operations"].values())
        return tuple(sorted(records, key=lambda item: (item.created_at, item.operation_id)))

    def execute(
        self,
        kind: str,
        idempotency_key: str,
        request_payload: Any,
        handler: Callable[[], Any],
    ) -> OperationRecord:
        """Run one operation once, or return its durable replay."""

        kind = _safe_key(kind, "kind")
        idempotency_key = _safe_key(idempotency_key, "idempotency_key")
        request_hash = sha256_hex(request_payload)
        lookup_key = f"{kind}:{idempotency_key}"
        with self._lock:
            payload = self._read_payload()
            operations = payload["operations"]
            existing_value = operations.get(lookup_key)
            if existing_value is not None:
                existing = _record_from(existing_value)
                if existing.request_hash != request_hash:
                    raise OperationManifestError(
                        "idempotency key is already bound to different content",
                        "IDEMPOTENCY_CONFLICT",
                    )
                if existing.status == "SUCCEEDED":
                    return existing
                if existing.status == "PENDING":
                    raise OperationManifestError(
                        "operation outcome is unknown; reconcile the pending operation",
                        "UNKNOWN_OUTCOME",
                    )
                return existing

            timestamp = _now()
            pending = OperationRecord(
                operation_id=self._operation_id(kind, idempotency_key),
                kind=kind,
                status="PENDING",
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                code="UNKNOWN_OUTCOME",
                message="operation is pending",
                created_at=timestamp,
                updated_at=timestamp,
            )
            operations[lookup_key] = pending.to_dict()
            self._write_payload(payload)

        try:
            result = handler()
        except Exception as exc:
            code, message = _exception_fields(exc)
            with self._lock:
                payload = self._read_payload()
                failed = OperationRecord(
                    **{
                        **pending.to_dict(),
                        "status": "FAILED",
                        "code": code,
                        "message": message[:500],
                        "updated_at": _now(),
                    }
                )
                payload["operations"][lookup_key] = failed.to_dict()
                self._write_payload(payload)
            raise

        result_hash = self._result_hash(result)
        with self._lock:
            payload = self._read_payload()
            succeeded = OperationRecord(
                **{
                    **pending.to_dict(),
                    "status": "SUCCEEDED",
                    "code": "OK",
                    "message": "OK",
                    "result_hash": result_hash,
                    "result": result,
                    "updated_at": _now(),
                }
            )
            payload["operations"][lookup_key] = succeeded.to_dict()
            self._write_payload(payload)
            return succeeded


def openapi_schema_hash(schema: Mapping[str, Any]) -> str:
    """Hash the final OpenAPI JSON shape, including paths and components."""

    if not isinstance(schema, Mapping):
        raise OperationManifestError("OpenAPI schema must be a mapping", "INVALID_SCHEMA")
    return sha256_hex(schema)


__all__ = [
    "OperationManifest",
    "OperationManifestError",
    "OperationRecord",
    "openapi_schema_hash",
]
