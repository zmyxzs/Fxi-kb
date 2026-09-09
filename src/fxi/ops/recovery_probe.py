"""Read-only backup/restore verification for the Fxi v3 operations boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fxi.core.canonical import sha256_hex
from fxi.storage.backup import BackupManager, SnapshotValidation


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Evidence that a restored tree matches a validated backup."""

    status: str
    backup_dir: str
    target_dir: str
    backup_root_hash: str | None = None
    target_root_hash: str | None = None
    manifest_hash: str | None = None
    file_count: int = 0
    sqlite_integrity: str = "unknown"
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    report_hash: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"PASSED", "FAILED", "INCOMPLETE"}:
            raise ValueError("invalid recovery report status")
        if not self.report_hash:
            payload = {
                "status": self.status,
                "backup_dir": self.backup_dir,
                "target_dir": self.target_dir,
                "backup_root_hash": self.backup_root_hash,
                "target_root_hash": self.target_root_hash,
                "manifest_hash": self.manifest_hash,
                "file_count": self.file_count,
                "sqlite_integrity": self.sqlite_integrity,
                "errors": self.errors,
                "warnings": self.warnings,
            }
            object.__setattr__(self, "report_hash", sha256_hex(payload))

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    @property
    def success(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "backup_dir": self.backup_dir,
            "target_dir": self.target_dir,
            "backup_root_hash": self.backup_root_hash,
            "target_root_hash": self.target_root_hash,
            "manifest_hash": self.manifest_hash,
            "file_count": self.file_count,
            "sqlite_integrity": self.sqlite_integrity,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "report_hash": self.report_hash,
        }


class RecoveryProbe:
    """Compare a restored directory with the source backup without mutation."""

    @staticmethod
    def _validate(path: Path) -> SnapshotValidation:
        return BackupManager.validate_snapshot(path)

    def verify(self, backup_dir: Path, target_dir: Path) -> RecoveryReport:
        backup = Path(backup_dir)
        target = Path(target_dir)
        errors: list[str] = []
        backup_validation: SnapshotValidation | None = None
        target_validation: SnapshotValidation | None = None

        try:
            backup_validation = self._validate(backup)
        except Exception as exc:
            errors.append(f"backup validation failed: {type(exc).__name__}: {str(exc)[:400]}")
        try:
            target_validation = self._validate(target)
        except Exception as exc:
            errors.append(f"restore validation failed: {type(exc).__name__}: {str(exc)[:400]}")

        if backup_validation is not None and target_validation is not None:
            if backup_validation.root_hash != target_validation.root_hash:
                errors.append("restored root hash does not match backup")
            if backup_validation.manifest_hash != target_validation.manifest_hash:
                errors.append("restored manifest hash does not match backup")
            if dict(backup_validation.files) != dict(target_validation.files):
                errors.append("restored file metadata does not match backup")

        status = "PASSED" if not errors else "FAILED"
        return RecoveryReport(
            status=status,
            backup_dir=str(backup.resolve(strict=False)),
            target_dir=str(target.resolve(strict=False)),
            backup_root_hash=backup_validation.root_hash if backup_validation else None,
            target_root_hash=target_validation.root_hash if target_validation else None,
            manifest_hash=backup_validation.manifest_hash if backup_validation else None,
            file_count=target_validation.file_count if target_validation else 0,
            sqlite_integrity=target_validation.sqlite_integrity if target_validation else "failed",
            errors=tuple(errors),
        )


__all__ = ["RecoveryProbe", "RecoveryReport"]
