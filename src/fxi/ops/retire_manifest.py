"""Fail-closed dry-run and execution manifest for disposable legacy paths."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Iterable, Mapping

from fxi.core.canonical import sha256_hex


_DEFAULT_PROTECTED = frozenset(
    {
        ".codex",
        ".git",
        "data",
        "materials",
        "projects",
        "sources",
        "snapshot.manifest.json",
    }
)


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & 0x400)


def _tree_entries(root: Path) -> dict[str, dict[str, Any]]:
    if not root.exists() or not root.is_dir() or _is_reparse_point(root):
        raise ValueError(f"退役根目录无效或包含 reparse point: {root}")
    entries: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if _is_reparse_point(path):
            raise OSError(f"退役根目录包含 symlink/junction/reparse point: {path}")
        relative = path.relative_to(root).as_posix()
        folded_name = relative.casefold()
        prior = folded.get(folded_name)
        if prior is not None and prior != relative:
            raise ValueError(f"退役根目录包含大小写别名: {prior}, {relative}")
        folded[folded_name] = relative
        if path.is_dir():
            entries[relative] = {"kind": "dir"}
        elif path.is_file():
            metadata = path.stat()
            entries[relative] = {
                "kind": "file",
                "sha256": sha256_hex(path.read_bytes()),
                "size": metadata.st_size,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        else:
            raise ValueError(f"退役根目录包含不支持的文件类型: {path}")
    return entries


def _entries_hash(entries: Mapping[str, Mapping[str, Any]]) -> str:
    return sha256_hex({"entries": dict(sorted(entries.items()))})


@dataclass(frozen=True, slots=True)
class RetirePlan:
    root: Path
    status: str
    root_hash: str
    target_set_hash: str
    sentinel_hash: str
    targets: tuple[str, ...] = ()
    sentinel_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    recovery_verified: bool = False
    recovery_report_hash: str | None = None

    @property
    def executable(self) -> bool:
        return self.status == "READY" and bool(self.targets) and self.recovery_verified

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "status": self.status,
            "root_hash": self.root_hash,
            "target_set_hash": self.target_set_hash,
            "sentinel_hash": self.sentinel_hash,
            "targets": list(self.targets),
            "sentinel_paths": list(self.sentinel_paths),
            "protected_paths": list(self.protected_paths),
            "blockers": list(self.blockers),
            "recovery_verified": self.recovery_verified,
            "recovery_report_hash": self.recovery_report_hash,
        }


@dataclass(frozen=True, slots=True)
class RetireConfirmation:
    root_hash: str
    target_set_hash: str
    sentinel_hash: str
    recovery_report_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RetireReport:
    status: str
    root: Path
    removed: tuple[str, ...] = ()
    root_hash: str | None = None
    target_set_hash: str | None = None
    sentinel_hash: str | None = None
    blockers: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    report_hash: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"NOOP", "BLOCKED", "EXECUTED", "PARTIAL", "FAILED"}:
            raise ValueError("invalid retire report status")
        if not self.report_hash:
            payload = {
                "status": self.status,
                "root": str(self.root),
                "removed": self.removed,
                "root_hash": self.root_hash,
                "target_set_hash": self.target_set_hash,
                "sentinel_hash": self.sentinel_hash,
                "blockers": self.blockers,
                "errors": self.errors,
            }
            object.__setattr__(self, "report_hash", sha256_hex(payload))

    @property
    def success(self) -> bool:
        return self.status in {"NOOP", "EXECUTED"} and not self.errors and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root": str(self.root),
            "removed": list(self.removed),
            "root_hash": self.root_hash,
            "target_set_hash": self.target_set_hash,
            "sentinel_hash": self.sentinel_hash,
            "blockers": list(self.blockers),
            "errors": list(self.errors),
            "report_hash": self.report_hash,
        }


class RetireManifest:
    """Build and execute a hash-confirmed set of explicitly disposable paths."""

    def __init__(
        self,
        targets: Iterable[str | Path] = (),
        *,
        protected_paths: Iterable[str | Path] = (),
        recovery_verified: bool = False,
        recovery_report: Any = None,
    ) -> None:
        self.targets = tuple(Path(item) for item in targets)
        self.protected_paths = frozenset(
            str(Path(item)).replace("\\", "/").strip("/").casefold()
            for item in (*_DEFAULT_PROTECTED, *protected_paths)
            if str(item).strip()
        )
        self.recovery_verified = bool(
            recovery_verified
            or (recovery_report is not None and bool(getattr(recovery_report, "success", False)))
        )
        self.recovery_report_hash = (
            str(getattr(recovery_report, "report_hash"))
            if recovery_report is not None and getattr(recovery_report, "report_hash", None)
            else None
        )

    @staticmethod
    def _root_path(root: Path) -> Path:
        supplied = Path(root)
        if supplied.is_symlink():
            raise OSError(f"退役根目录不能是 symlink: {supplied}")
        return supplied.resolve()

    def _normalize_target(self, root: Path, value: Path) -> tuple[str | None, str | None]:
        if value.is_absolute():
            candidate = value
        else:
            if ".." in value.parts:
                return None, f"目标路径禁止包含 ..: {value}"
            candidate = root / value
        if candidate.is_symlink():
            return None, f"目标路径不能是 symlink: {candidate}"
        resolved = candidate.resolve(strict=False)
        if resolved == root or root not in resolved.parents:
            return None, f"目标路径越界: {value}"
        relative = resolved.relative_to(root).as_posix()
        if not relative or relative.casefold() in self.protected_paths:
            return None, f"目标路径受保护: {relative}"
        first = relative.split("/", 1)[0].casefold()
        if first in self.protected_paths:
            return None, f"目标路径位于受保护目录: {relative}"
        if not resolved.exists():
            return None, f"目标路径不存在: {relative}"
        if _is_reparse_point(resolved):
            return None, f"目标路径包含 reparse point: {relative}"
        return relative, None

    def _snapshot(self, root: Path, targets: tuple[str, ...], entries: Mapping[str, Mapping[str, Any]]) -> RetirePlan:
        target_set = {
            relative: value
            for relative, value in entries.items()
            if any(relative == target or relative.startswith(f"{target}/") for target in targets)
        }
        sentinel = {relative: value for relative, value in entries.items() if relative not in target_set}
        return RetirePlan(
            root=root,
            status="READY" if targets and not any(False for _ in ()) else "NOOP",
            root_hash=_entries_hash(entries),
            target_set_hash=_entries_hash(target_set),
            sentinel_hash=_entries_hash(sentinel),
            targets=targets,
            sentinel_paths=tuple(sorted(sentinel)),
            protected_paths=tuple(sorted(self.protected_paths)),
            recovery_verified=self.recovery_verified,
            recovery_report_hash=self.recovery_report_hash,
        )

    def dry_run(self, root: Path) -> RetirePlan:
        try:
            resolved_root = self._root_path(Path(root))
            entries = _tree_entries(resolved_root)
        except Exception as exc:
            return RetirePlan(
                root=Path(root).resolve(strict=False),
                status="BLOCKED",
                root_hash="0" * 64,
                target_set_hash="0" * 64,
                sentinel_hash="0" * 64,
                blockers=(f"{type(exc).__name__}: {str(exc)[:400]}",),
                recovery_verified=self.recovery_verified,
                recovery_report_hash=self.recovery_report_hash,
            )

        normalized: list[str] = []
        blockers: list[str] = []
        for value in self.targets:
            relative, error = self._normalize_target(resolved_root, value)
            if error:
                blockers.append(error)
            elif relative is not None:
                normalized.append(relative)
        folded = [value.casefold() for value in normalized]
        if len(folded) != len(set(folded)):
            blockers.append("目标集合包含大小写别名")
        ordered = tuple(sorted(set(normalized)))
        for index, target in enumerate(ordered):
            if any(target.startswith(f"{prior}/") for prior in ordered[:index]):
                blockers.append(f"目标集合包含嵌套路径: {target}")
        plan = self._snapshot(resolved_root, ordered, entries)
        if blockers:
            return replace(plan, status="BLOCKED", blockers=tuple(blockers))
        if not ordered:
            return plan
        return replace(plan, status="READY")

    def execute(self, plan: RetirePlan, *, confirm_hashes: RetireConfirmation) -> RetireReport:
        if plan.status == "NOOP":
            return RetireReport(status="NOOP", root=plan.root)
        if plan.status != "READY":
            return RetireReport(status="BLOCKED", root=plan.root, blockers=plan.blockers or ("retire plan is not ready",))
        if not plan.recovery_verified:
            return RetireReport(status="BLOCKED", root=plan.root, blockers=("RECOVERY_REQUIRED",))
        if plan.recovery_report_hash and confirm_hashes.recovery_report_hash != plan.recovery_report_hash:
            return RetireReport(status="BLOCKED", root=plan.root, blockers=("RECOVERY_HASH_MISMATCH",))
        try:
            entries = _tree_entries(plan.root)
        except Exception as exc:
            return RetireReport(status="FAILED", root=plan.root, errors=(f"{type(exc).__name__}: {str(exc)[:400]}",))
        current = self._snapshot(plan.root, plan.targets, entries)
        if (
            current.root_hash != plan.root_hash
            or current.target_set_hash != plan.target_set_hash
            or current.sentinel_hash != plan.sentinel_hash
        ):
            return RetireReport(status="BLOCKED", root=plan.root, blockers=("RETIRE_PLAN_STALE",))
        if (
            confirm_hashes.root_hash != plan.root_hash
            or confirm_hashes.target_set_hash != plan.target_set_hash
            or confirm_hashes.sentinel_hash != plan.sentinel_hash
        ):
            return RetireReport(status="BLOCKED", root=plan.root, blockers=("RETIRE_CONFIRMATION_MISMATCH",))

        removed: list[str] = []
        errors: list[str] = []
        for relative in plan.targets:
            target = plan.root / relative
            try:
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.is_file() and not target.is_symlink():
                    target.unlink()
                else:
                    raise OSError(f"目标类型在执行前发生变化: {target}")
                removed.append(relative)
            except (OSError, ValueError) as exc:
                errors.append(f"{relative}: {type(exc).__name__}: {str(exc)[:300]}")
                break
        status = "EXECUTED" if not errors else ("PARTIAL" if removed else "FAILED")
        return RetireReport(
            status=status,
            root=plan.root,
            removed=tuple(removed),
            root_hash=plan.root_hash,
            target_set_hash=plan.target_set_hash,
            sentinel_hash=plan.sentinel_hash,
            errors=tuple(errors),
        )


__all__ = ["RetireConfirmation", "RetireManifest", "RetirePlan", "RetireReport"]
