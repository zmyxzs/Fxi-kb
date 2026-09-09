"""Registration manifest and package implementation for the first novel domain."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from fxi.knowledge.contracts import CONTRACT_REVISION, ContractModel, SCHEMA_HASH
from fxi.knowledge.registry import DomainRegistry

from .conflicts import NovelConflictPolicy
from .context import render_context
from .projections import project
from .schemas import NOVEL_SCHEMA_VERSION, PAYLOAD_MODELS


PACKAGE_ID = "novel"
PACKAGE_VERSION = "1"
SUPPORTED_TYPE_URIS = tuple(f"novel.{name}@1" for name in PAYLOAD_MODELS)


class NovelPackageManifest(ContractModel):
    package_id: str = PACKAGE_ID
    package_version: str = PACKAGE_VERSION
    schema_version: str = NOVEL_SCHEMA_VERSION
    contract_revision: str = CONTRACT_REVISION
    schema_hash: str = SCHEMA_HASH
    supported_type_uris: tuple[str, ...] = SUPPORTED_TYPE_URIS
    capabilities: tuple[str, ...] = ("validate", "migrate", "extract", "conflict", "context", "projection")

class NovelDomainPackage:
    """A domain package with no changes to the kernel lifecycle services."""

    package_id = PACKAGE_ID
    package_version = PACKAGE_VERSION
    supported_type_uris = SUPPORTED_TYPE_URIS
    schema_version = NOVEL_SCHEMA_VERSION

    def __init__(self) -> None:
        self._models = {
            type_uri: PAYLOAD_MODELS[type_uri.removeprefix("novel.").removesuffix("@1")]
            for type_uri in self.supported_type_uris
        }

    def validate(self, type_uri: str, payload: Mapping[str, Any]) -> None:
        model = self._models.get(type_uri)
        if model is None:
            raise ValueError(f"unregistered novel type URI: {type_uri}")
        if not isinstance(payload, Mapping):
            raise TypeError("novel payload must be a mapping")
        model.model_validate(payload)

    def migrate(self, type_uri: str, payload: Mapping[str, Any], from_version: str) -> Mapping[str, Any]:
        model = self._models.get(type_uri)
        if model is None:
            raise ValueError(f"unregistered novel type URI: {type_uri}")
        if not isinstance(payload, Mapping):
            raise TypeError("novel payload must be a mapping")
        if from_version not in {"0", self.schema_version}:
            raise ValueError(f"unsupported novel schema version: {from_version}")
        migrated = dict(payload)
        if from_version == "0" and "id" in migrated and not any(
            key in migrated for key in ("record_id", "entity_id", "character_id", "item_id", "location_id", "faction_id")
        ):
            migrated["record_id"] = migrated.pop("id")
        validated = model.model_validate(migrated)
        return validated.model_dump(mode="json")

    def extract_contract(self) -> Mapping[str, Any]:
        manifest = NovelPackageManifest()
        return {
            **manifest.model_dump(mode="json"),
            "schemas": {uri: self._models[uri].model_json_schema() for uri in self.supported_type_uris},
        }

    def conflict_policy(self, type_uri: str) -> NovelConflictPolicy:
        if type_uri not in self.supported_type_uris:
            raise ValueError(f"unregistered novel type URI: {type_uri}")
        return NovelConflictPolicy(type_uri=type_uri)

    def render_context(self, obj: Any, view: Any) -> Any:
        return render_context(obj, view)

    def project(self, obj: Any) -> Sequence[Any]:
        return project(obj)


def register_novel(registry: DomainRegistry) -> NovelDomainPackage:
    """Instantiate and register the package, returning the registered instance."""

    package = NovelDomainPackage()
    registry.register(package)
    return package


__all__ = [
    "NovelDomainPackage",
    "NovelPackageManifest",
    "PACKAGE_ID",
    "PACKAGE_VERSION",
    "SUPPORTED_TYPE_URIS",
    "register_novel",
]
