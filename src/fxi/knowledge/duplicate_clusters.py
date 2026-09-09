"""Domain-neutral duplicate and variant cluster index for candidate evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from fxi.core.canonical import sha256_hex

from .contracts import _hash, _stable_hash, _token


@dataclass(frozen=True)
class DuplicateCluster:
    """A queryable group of candidates with a duplicate/variant relationship."""

    cluster_id: str
    candidate_ids: tuple[str, ...]
    representative_id: str
    fingerprint: str
    variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateObservation:
    """The generic duplicate facts attached to one evaluation."""

    cluster_id: str | None
    variant_of: str | None
    same_core: bool
    surface_similarity_risk: float | None


class DuplicateClusterIndex:
    """Keep duplicate information without understanding any domain payload.

    A caller may provide an evaluator-produced ``assessment`` mapping.  The
    mapping is treated as opaque metadata except for the generic relationship
    fields below.  Without one, exact canonical payload hashes form a cluster;
    near-duplicate decisions are never guessed by this index.
    """

    def __init__(self) -> None:
        self._clusters: dict[str, DuplicateCluster] = {}
        self._candidate_to_cluster: dict[str, str] = {}
        self._observations: dict[str, DuplicateObservation] = {}
        self._lock = RLock()

    def observe(
        self,
        candidate_id: str,
        *,
        payload_hash: str,
        assessment: Mapping[str, Any] | None = None,
    ) -> DuplicateObservation:
        candidate_id = _token(candidate_id, "candidate_id")
        _hash(payload_hash, "payload_hash")
        if assessment is not None and not isinstance(assessment, Mapping):
            raise TypeError("duplicate assessment must be a mapping")

        supplied_cluster = assessment.get("cluster_id") if assessment else None
        supplied_fingerprint = assessment.get("fingerprint") if assessment else None
        fingerprint = supplied_fingerprint or payload_hash
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("duplicate fingerprint must be a non-empty string")
        cluster_id = supplied_cluster or f"cluster-{sha256_hex(fingerprint)[:48]}"
        cluster_id = _token(cluster_id, "cluster_id")

        variant_of = assessment.get("variant_of") if assessment else None
        if variant_of is not None:
            variant_of = _token(variant_of, "variant_of")
        same_core = bool(assessment.get("same_core", False)) if assessment else False
        risk = assessment.get("surface_similarity_risk") if assessment else None
        if risk is not None:
            if not isinstance(risk, (int, float)) or isinstance(risk, bool) or not 0 <= risk <= 1:
                raise ValueError("surface_similarity_risk must be between 0 and 1")
            risk = float(risk)

        with self._lock:
            existing = self._clusters.get(cluster_id)
            if existing is None:
                representative = variant_of or candidate_id
                variants = (candidate_id,) if variant_of else ()
                existing = DuplicateCluster(
                    cluster_id=cluster_id,
                    candidate_ids=(candidate_id,),
                    representative_id=representative,
                    fingerprint=fingerprint,
                    variants=variants,
                )
            elif candidate_id not in existing.candidate_ids:
                representative = existing.representative_id
                variants = existing.variants
                if candidate_id != representative and candidate_id not in variants:
                    variants = (*variants, candidate_id)
                existing = DuplicateCluster(
                    cluster_id=existing.cluster_id,
                    candidate_ids=(*existing.candidate_ids, candidate_id),
                    representative_id=representative,
                    fingerprint=existing.fingerprint,
                    variants=variants,
                )
            self._clusters[cluster_id] = existing
            self._candidate_to_cluster[candidate_id] = cluster_id
            observation = DuplicateObservation(
                cluster_id=cluster_id,
                variant_of=variant_of or (
                    existing.representative_id if existing.representative_id != candidate_id else None
                ),
                same_core=same_core or existing.candidate_ids != (candidate_id,),
                surface_similarity_risk=risk if risk is not None else (1.0 if existing.candidate_ids != (candidate_id,) else None),
            )
            self._observations[candidate_id] = observation
            return observation

    def get(self, cluster_id: str) -> DuplicateCluster:
        cluster_id = _token(cluster_id, "cluster_id")
        with self._lock:
            cluster = self._clusters.get(cluster_id)
            if cluster is None:
                raise KeyError(cluster_id)
            return cluster

    def for_candidate(self, candidate_id: str) -> DuplicateObservation | None:
        candidate_id = _token(candidate_id, "candidate_id")
        with self._lock:
            return self._observations.get(candidate_id)

    def list(self) -> tuple[DuplicateCluster, ...]:
        with self._lock:
            return tuple(self._clusters.values())

    def fingerprint(self, value: Any) -> str:
        """Expose the same canonical hashing primitive used by the index."""

        return _stable_hash(value)


__all__ = ["DuplicateCluster", "DuplicateClusterIndex", "DuplicateObservation"]
