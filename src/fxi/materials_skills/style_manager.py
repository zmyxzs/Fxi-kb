"""
fxi.materials_skills.style_manager - 文笔风格与修辞比喻管理器
"""

from pathlib import Path
import json
import re
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.materials_skills.candidate_store import package_hash
from fxi.storage.sqlite_client import DatabaseClient


class StyleManager:
    """作品文笔风格、具象比喻偏好、句式节奏与负向禁令管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    @staticmethod
    def _validate_work_id(work_id: str) -> str:
        if not isinstance(work_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_id):
            raise ValueError("work_id 必须是安全的非空作品标识符")
        return work_id

    def _get_style_path(self, work_id: str) -> Path:
        work_id = self._validate_work_id(work_id)
        return self.config.projects_dir / work_id / "style" / "style_profile.yaml"

    def save_style_profile(self, work_id: str, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError("style profile 必须是对象")
        target_file = self._get_style_path(work_id)
        stored = dict(data)
        declared_work_id = stored.get("work_id")
        if declared_work_id not in (None, work_id):
            raise ValueError("style profile 的 work_id 与目标作品不匹配")
        stored["work_id"] = work_id
        stored["status"] = "AVAILABLE"
        serialized = yaml.safe_dump(stored, allow_unicode=True, sort_keys=False)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_file.write_text(serialized, encoding="utf-8")
            if target_file.read_text(encoding="utf-8") != serialized:
                raise OSError("风格档案读回内容与写入内容不一致")
        except OSError as exc:
            raise RuntimeError(f"无法保存作品 [{work_id}] 的风格档案") from exc

    def get_approved_style_head(self, work_id: str) -> Optional[dict[str, Any]]:
        """Read the approved unified style head for exactly one work.

        A present but malformed head is returned as ``INCOMPLETE`` so callers do
        not silently fall back to an older local profile or another work.
        """
        work_id = self._validate_work_id(work_id)
        client = DatabaseClient(self.config.sqlite_path)
        with client.get_connection() as conn:
            head = conn.execute(
                "SELECT active_version FROM v2_style_heads WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if not head or not head["active_version"]:
                return None
            candidate = conn.execute(
                """
                SELECT candidate_id, work_id, version, package_hash, status, payload_json
                FROM v2_style_candidates
                WHERE work_id = ? AND version = ?
                """,
                (work_id, head["active_version"]),
            ).fetchone()

        if not candidate or candidate["status"] != "APPROVED":
            return {
                "work_id": work_id,
                "version": head["active_version"],
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_HEAD_UNAVAILABLE",
                "message": "正式风格 head 指向的候选不存在或未获批准。",
            }
        try:
            payload = json.loads(candidate["payload_json"])
        except (TypeError, json.JSONDecodeError):
            return {
                "work_id": work_id,
                "version": candidate["version"],
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_HEAD_CORRUPT",
                "message": "正式风格候选的持久化数据不是有效 JSON。",
            }
        if not isinstance(payload, dict):
            return {
                "work_id": work_id,
                "version": candidate["version"],
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_HEAD_CORRUPT",
                "message": "正式风格候选的持久化数据必须是对象。",
            }
        package = payload.get("package", payload)
        if not isinstance(package, dict):
            return {
                "work_id": work_id,
                "version": candidate["version"],
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_HEAD_CORRUPT",
                "message": "正式风格候选的 package 必须是对象。",
            }
        if candidate["package_hash"] != package_hash(package):
            return {
                "work_id": work_id,
                "version": candidate["version"],
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_HEAD_HASH_MISMATCH",
                "message": "正式风格候选的 package_hash 校验失败。",
            }
        result: dict[str, Any] = {
            "work_id": work_id,
            "version": candidate["version"],
            "candidate_id": candidate["candidate_id"],
            "package_hash": candidate["package_hash"],
            "status": "AVAILABLE",
            "source": "v2_style_head",
            "package": package,
        }
        for target, aliases in {
            "style_rules": ("style_rules", "rules"),
            "style_examples": ("style_examples", "positive_exemplars", "examples"),
            "exact_text_rules": ("exact_text_rules", "negative_rules"),
        }.items():
            for key in aliases:
                if key in package:
                    result[target] = package[key]
                    if target not in package:
                        package[target] = package[key]
                    break
        return result

    def get_style_profile(self, work_id: str, scene_type: Optional[str] = None) -> dict[str, Any]:
        work_id = self._validate_work_id(work_id)
        approved_head = self.get_approved_style_head(work_id)
        if approved_head is not None:
            if scene_type:
                package = approved_head.get("package")
                examples = package.get("style_examples") if isinstance(package, dict) else None
                if isinstance(examples, list):
                    matched = [ex for ex in examples if isinstance(ex, dict) and ex.get("scene_type") == scene_type]
                    others = [ex for ex in examples if not isinstance(ex, dict) or ex.get("scene_type") != scene_type]
                    package["style_examples"] = matched + others
                    approved_head["style_examples"] = package["style_examples"]
            return approved_head
        target_file = self._get_style_path(work_id)
        if not target_file.is_file():
            return {
                "work_id": work_id,
                "status": "UNAVAILABLE",
                "error_code": "WORK_STYLE_PROFILE_MISSING",
                "message": "未找到该作品自己的 style_profile.yaml；不会继承其他作品或全局默认风格。",
            }

        try:
            data = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return {
                "work_id": work_id,
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_PROFILE_UNREADABLE",
                "message": f"无法读取该作品的风格档案: {type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "work_id": work_id,
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_PROFILE_INVALID",
                "message": "该作品的风格档案必须是对象。",
            }
        declared_work_id = data.get("work_id")
        if declared_work_id not in (None, work_id):
            return {
                "work_id": work_id,
                "status": "INCOMPLETE",
                "error_code": "WORK_STYLE_PROFILE_SCOPE_MISMATCH",
                "message": "风格档案属于其他作品，已拒绝跨作品读取。",
            }
        data["work_id"] = work_id
        data.setdefault("status", "AVAILABLE")
        if scene_type and isinstance(data.get("positive_exemplars"), list):
            matched = [ex for ex in data["positive_exemplars"] if isinstance(ex, dict) and ex.get("scene_type") == scene_type]
            others = [ex for ex in data["positive_exemplars"] if not isinstance(ex, dict) or ex.get("scene_type") != scene_type]
            data["positive_exemplars"] = matched + others
        return data
