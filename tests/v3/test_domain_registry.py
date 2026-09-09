"""Synthetic registry tests; no domain/business assets are used."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from fxi.knowledge import DomainRegistry, RegistryError


class SyntheticPackage:
    package_id = "synthetic-package"
    package_version = "1"
    supported_type_uris = ("example.note@1", "example.link@1")

    def validate(self, type_uri: str, payload: Mapping[str, Any]) -> None:
        if type_uri == "example.note@1" and "value" not in payload:
            raise ValueError("value is required")

    def migrate(self, type_uri: str, payload: Mapping[str, Any], from_version: str) -> Mapping[str, Any]:
        return dict(payload)

    def extract_contract(self) -> Mapping[str, Any]:
        return {"package": self.package_id}

    def conflict_policy(self, type_uri: str) -> Mapping[str, Any]:
        return {"type_uri": type_uri}

    def render_context(self, obj: Any, view: Any) -> Mapping[str, Any]:
        return {"object": obj, "view": view}

    def project(self, obj: Any) -> Sequence[Any]:
        return (obj,)


def test_registry_resolves_registered_types_and_reports_capabilities() -> None:
    registry = DomainRegistry()
    package = SyntheticPackage()
    registry.register(package)
    assert registry.resolve("example.note@1") is package
    manifest = registry.capabilities("work-synthetic")
    assert "example.note@1" in manifest.registered_type_uris
    assert manifest.contract_revision == "studio-fxi-v3.20260909"
    assert len(manifest.schema_hash) == 64


def test_registry_quarantines_unregistered_and_invalid_payloads() -> None:
    registry = DomainRegistry()
    registry.register(SyntheticPackage())
    with pytest.raises(RegistryError) as missing:
        registry.validate("example.unknown@1", {"value": "synthetic"})
    assert missing.value.code == "UNREGISTERED_TYPE"
    with pytest.raises(RegistryError) as invalid:
        registry.validate("example.note@1", {})
    assert invalid.value.code == "INVALID_SCHEMA"
    reasons = tuple(record.reason for record in registry.quarantine_records())
    assert reasons == ("UNREGISTERED_TYPE", "INVALID_SCHEMA")


def test_plugin_manifest_requires_allowlisted_hash_and_matching_package() -> None:
    package = SyntheticPackage()
    registry = DomainRegistry(allowed_plugin_hashes={package.package_id: "plugin-hash"})
    from fxi.knowledge.registry import PluginManifest

    manifest = PluginManifest(
        package_id=package.package_id,
        package_version=package.package_version,
        supported_type_uris=package.supported_type_uris,
        code_hash="plugin-hash",
        signature="synthetic-signature",
    )
    registry.register(package, manifest=manifest)
    assert registry.resolve("example.link@1", package_version="1") is package
