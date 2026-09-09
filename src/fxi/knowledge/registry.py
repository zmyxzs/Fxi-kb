"""Domain package registry for the generic Fxi v3 kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from fxi.core.exceptions import FxiError

from .contracts import CapabilityManifest, ContractModel, ErrorCode, SCHEMA_VERSION, contract_schema_hash


class RegistryError(FxiError):
    """A package registration or resolution failure."""

    def __init__(self, message: str, code: str = ErrorCode.UNREGISTERED_TYPE.value):
        super().__init__(message, code=code)


@runtime_checkable
class DomainPackage(Protocol):
    package_id: str
    package_version: str
    supported_type_uris: tuple[str, ...]

    def validate(self, type_uri: str, payload: Mapping[str, Any]) -> None: ...

    def migrate(self, type_uri: str, payload: Mapping[str, Any], from_version: str) -> Mapping[str, Any]: ...

    def extract_contract(self) -> Any: ...

    def conflict_policy(self, type_uri: str) -> Any: ...

    def render_context(self, obj: Any, view: Any) -> Any: ...

    def project(self, obj: Any) -> Sequence[Any]: ...


class PluginManifest(ContractModel):
    package_id: str
    package_version: str
    supported_type_uris: tuple[str, ...]
    code_hash: str
    signature: str


@dataclass(frozen=True)
class QuarantineRecord:
    type_uri: str
    payload: Mapping[str, Any]
    reason: str
    candidate_ref: str | None = None


class DomainRegistry:
    """Resolve domain behavior without baking domain types into the kernel."""

    def __init__(self, *, allowed_plugin_hashes: Mapping[str, str] | None = None):
        self._packages: dict[tuple[str, str], DomainPackage] = {}
        self._type_index: dict[str, list[tuple[str, str]]] = {}
        self._allowed_plugin_hashes = dict(allowed_plugin_hashes or {})
        self._quarantine: list[QuarantineRecord] = []

    def register(self, package: DomainPackage, *, manifest: PluginManifest | None = None) -> None:
        package_id = self._required_attr(package, "package_id")
        package_version = self._required_attr(package, "package_version")
        type_uris = tuple(self._required_attr(package, "supported_type_uris"))
        if not package_id or not package_version or not type_uris:
            raise RegistryError("domain package manifest is incomplete", ErrorCode.INVALID_SCHEMA.value)
        if len(set(type_uris)) != len(type_uris) or any(not isinstance(item, str) or not item for item in type_uris):
            raise RegistryError("domain package type URI list is invalid", ErrorCode.INVALID_SCHEMA.value)
        if manifest is not None:
            if (manifest.package_id, manifest.package_version) != (package_id, package_version):
                raise RegistryError("plugin manifest does not match package", ErrorCode.INVALID_SCHEMA.value)
            if tuple(manifest.supported_type_uris) != type_uris:
                raise RegistryError("plugin manifest type URI list does not match package", ErrorCode.INVALID_SCHEMA.value)
            expected_hash = self._allowed_plugin_hashes.get(package_id)
            if expected_hash is None or expected_hash != manifest.code_hash:
                raise RegistryError("plugin code hash is not allow-listed", ErrorCode.AUTHORIZATION_FAILED.value)
            if not manifest.signature:
                raise RegistryError("plugin signature is required", ErrorCode.AUTHORIZATION_FAILED.value)
        key = (package_id, package_version)
        existing = self._packages.get(key)
        if existing is not None and existing is not package:
            raise RegistryError("domain package version is already registered", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        self._packages[key] = package
        for type_uri in type_uris:
            versions = self._type_index.setdefault(type_uri, [])
            if key not in versions:
                versions.append(key)
                versions.sort()

    def resolve(self, type_uri: str, package_version: str | None = None) -> DomainPackage:
        keys = self._type_index.get(type_uri, ())
        if package_version is not None:
            package = self._packages.get((self._package_for(type_uri, package_version), package_version))
            if package is None:
                raise RegistryError(f"unregistered type URI: {type_uri}")
            return package
        if not keys:
            raise RegistryError(f"unregistered type URI: {type_uri}")
        return self._packages[keys[-1]]

    def validate(self, type_uri: str, payload: Mapping[str, Any], *, package_version: str | None = None) -> None:
        try:
            package = self.resolve(type_uri, package_version)
            package.validate(type_uri, payload)
        except RegistryError:
            self.quarantine(type_uri, payload, "UNREGISTERED_TYPE")
            raise
        except Exception as exc:
            self.quarantine(type_uri, payload, "INVALID_SCHEMA")
            raise RegistryError(f"domain package rejected payload: {type(exc).__name__}", ErrorCode.INVALID_SCHEMA.value) from exc

    def quarantine(self, type_uri: str, payload: Mapping[str, Any], reason: str, candidate_ref: str | None = None) -> QuarantineRecord:
        record = QuarantineRecord(type_uri=type_uri, payload=dict(payload), reason=reason, candidate_ref=candidate_ref)
        self._quarantine.append(record)
        return record

    def quarantine_records(self) -> tuple[QuarantineRecord, ...]:
        return tuple(self._quarantine)

    def registered_type_uris(self) -> tuple[str, ...]:
        return tuple(sorted(self._type_index))

    def capabilities(self, work_id: str) -> CapabilityManifest:
        if not isinstance(work_id, str) or not work_id:
            raise RegistryError("work_id is required", ErrorCode.INVALID_SCOPE.value)
        package_ids = tuple(sorted({package.package_id for package in self._packages.values()}))
        return CapabilityManifest(
            schema_hash=contract_schema_hash(),
            registered_type_uris=self.registered_type_uris(),
            views=("context", "query"),
            projections=("fts",),
            model_capabilities=(),
            feature_flags={"domain_registry": True, "work_scoped": True},
        )

    def _package_for(self, type_uri: str, version: str) -> str:
        for package_id, package_version in self._type_index.get(type_uri, ()):
            if package_version == version:
                return package_id
        return ""

    @staticmethod
    def _required_attr(package: DomainPackage, name: str) -> Any:
        value = getattr(package, name, None)
        if value is None:
            raise RegistryError(f"domain package missing {name}", ErrorCode.INVALID_SCHEMA.value)
        return value


__all__ = ["DomainPackage", "DomainRegistry", "PluginManifest", "QuarantineRecord", "RegistryError"]
