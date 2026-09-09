"""章节写作审核的显式、可验证裁决服务。

审核器负责产生检查结果，WritingReviewService 负责验证审核器身份、输入范围和
检查完整性。没有配置可信 reviewer 时，结果只能是 INCOMPLETE，不能因为外部
报告声称 PASSED 就放行。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence
from uuid import uuid4

from fxi.core.exceptions import ValidationError
from fxi.core.canonical import sha256_hex


_PASSED = {"PASS", "PASSED", "OK", "VALID", "SUCCESS"}
_REVISION = {"NEEDS_REVISION", "REVISION", "WARN", "WARNING"}
_REJECTED = {"REJECTED", "FAIL", "FAILED", "INVALID", "CONFLICT"}
_INCOMPLETE = {"INCOMPLETE", "PENDING", "UNKNOWN", "UNAVAILABLE"}


class ReviewRejectedError(ValidationError):
    """审核不存在可提交的、由可信 reviewer 产生的 PASSED 结论。"""


class Reviewer(Protocol):
    """可注入的审核器协议。

    reviewer_id 和 reviewer_version 是审核器身份的一部分，不能从请求体
    自报。审核器必须根据传入的固定输入返回带 checks 的对象。
    """

    reviewer_id: str
    reviewer_version: str

    def review(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ReviewCheckResult:
    check_id: str
    status: str
    required: bool = True
    input_text_hash: Optional[str] = None
    coverage_spans: tuple[tuple[int, int], ...] = ()
    event_results: tuple[dict[str, Any], ...] = ()
    findings: tuple[dict[str, Any], ...] = ()
    error_code: Optional[str] = None
    checker_version: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["coverage_spans"] = [list(span) for span in self.coverage_spans]
        data["event_results"] = [dict(item) for item in self.event_results]
        data["findings"] = [dict(item) for item in self.findings]
        return data


@dataclass(frozen=True)
class ReviewDecision:
    status: str
    checks: tuple[ReviewCheckResult, ...]
    blocking_findings: tuple[dict[str, Any], ...] = ()
    report_id: Optional[str] = None
    work_id: Optional[str] = None
    source_id: Optional[str] = None
    source_version: Optional[str] = None
    knowledge_version: Optional[str] = None
    text_hash: Optional[str] = None
    plan_hash: Optional[str] = None
    context_hash: Optional[str] = None
    reviewer_id: Optional[str] = None
    reviewer_version: Optional[str] = None
    input_fingerprint: Optional[str] = None
    required_checks: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    coverage_required: tuple[str, ...] = ()
    text_length: Optional[int] = None
    dependency_versions: tuple[tuple[str, Optional[str]], ...] = ()
    style_package_version: Optional[str] = None
    mode: Optional[str] = None
    scene_id: Optional[str] = None
    chapter_index: Optional[int] = None
    sacred_whitelist: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "overall_status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "blocking_findings": [dict(item) for item in self.blocking_findings],
        }
        for name in (
            "report_id",
            "work_id",
            "source_id",
            "source_version",
            "knowledge_version",
            "text_hash",
            "plan_hash",
            "context_hash",
            "reviewer_id",
            "reviewer_version",
            "input_fingerprint",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        data["required_checks"] = list(self.required_checks)
        data["event_ids"] = list(self.event_ids)
        data["coverage_required"] = list(self.coverage_required)
        if self.text_length is not None:
            data["text_length"] = self.text_length
        data["dependency_versions"] = dict(self.dependency_versions)
        for name in ("style_package_version", "mode", "scene_id", "chapter_index"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        data["sacred_whitelist"] = list(self.sacred_whitelist)
        return data


def _value(item: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _status(value: Any) -> str:
    if not isinstance(value, str):
        return "INCOMPLETE"
    upper = value.upper()
    if upper in _PASSED:
        return "PASSED"
    if upper in _REVISION:
        return "NEEDS_REVISION"
    if upper in _REJECTED:
        return "REJECTED"
    if upper in _INCOMPLETE:
        return "INCOMPLETE"
    return "INCOMPLETE"


def _mapping_list(value: Any, field_name: str) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} 必须是列表")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValidationError(f"{field_name}[{index}] 必须是对象")
        result.append(item)
    return result


def normalize_dependency_versions(value: Any) -> dict[str, Optional[str]]:
    """Normalize dependency bindings without accepting caller-controlled junk."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("dependency_versions 必须是对象")
    result: dict[str, Optional[str]] = {}
    for key, version in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError("dependency_versions 的键必须是非空标识符")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise ValidationError(f"dependency_versions[{key}] 必须是非空字符串或 null")
        result[key] = version
    return dict(sorted(result.items()))


def dependency_hash(value: Mapping[str, Optional[str]] | None) -> str:
    """Return the stable hash used to bind a review to its dependencies."""

    normalized = normalize_dependency_versions(value)
    return sha256_hex(normalized)


def review_input_fingerprint(data: Mapping[str, Any]) -> str:
    """Fingerprint all review inputs that can change a semantic decision."""

    stable = {
        key: data.get(key)
        for key in (
            "work_id",
            "source_id",
            "source_version",
            "knowledge_version",
            "text_hash",
            "plan_hash",
            "context_hash",
            "required_checks",
            "event_ids",
            "coverage_required",
            "text_length",
            "dependency_versions",
            "style_package_version",
            "mode",
            "scene_id",
            "chapter_index",
            "sacred_whitelist",
        )
    }
    stable["dependency_versions"] = normalize_dependency_versions(stable.get("dependency_versions"))
    return sha256_hex(stable)


def _input_fingerprint(data: Mapping[str, Any]) -> str:
    """Backward-compatible private alias for callers inside this module."""

    return review_input_fingerprint(data)


class WritingReviewService:
    """验证可信 reviewer 输出，并对检查结果作确定性裁决。"""

    def __init__(
        self,
        reviewer: Optional[Reviewer | Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
        *,
        reviewer_id: Optional[str] = None,
        reviewer_version: Optional[str] = None,
        id_factory: Callable[[], str] = lambda: f"review-{uuid4().hex}",
    ):
        self.reviewer = reviewer
        self.reviewer_id = reviewer_id or getattr(reviewer, "reviewer_id", None)
        self.reviewer_version = reviewer_version or getattr(reviewer, "reviewer_version", None)
        self.id_factory = id_factory

    def adjudicate(
        self,
        checks: Iterable[Mapping[str, Any] | Any],
        *,
        required_checks: Sequence[str],
        text_hash: str,
        event_ids: Sequence[str] = (),
        coverage_required: Sequence[str] = (),
        text_length: Optional[int] = None,
        work_id: Optional[str] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        knowledge_version: Optional[str] = None,
        plan_hash: Optional[str] = None,
        context_hash: Optional[str] = None,
        reviewer_id: Optional[str] = None,
        reviewer_version: Optional[str] = None,
        input_fingerprint: Optional[str] = None,
        report_id: Optional[str] = None,
        dependency_versions: Optional[Mapping[str, Optional[str]]] = None,
        style_package_version: Optional[str] = None,
        mode: Optional[str] = None,
        scene_id: Optional[str] = None,
        chapter_index: Optional[int] = None,
        sacred_whitelist: Sequence[str] = (),
    ) -> ReviewDecision:
        if not isinstance(text_hash, str) or not text_hash:
            raise ValidationError("审核裁决必须提供 text_hash")
        required = self._identifiers(required_checks, "required_checks")
        coverage_required_values = self._identifiers(coverage_required, "coverage_required")
        coverage_required_ids = set(coverage_required_values)
        expected_events = self._identifiers(event_ids, "event_ids")
        sacred_whitelist_values = self._identifiers(sacred_whitelist, "sacred_whitelist")
        normalized_dependencies = normalize_dependency_versions(dependency_versions)

        normalized: list[ReviewCheckResult] = []
        seen: set[str] = set()
        blocking: list[dict[str, Any]] = []
        raw_checks = list(checks)
        if not raw_checks:
            blocking.append({"check_id": "reviewer", "code": "REVIEW_CHECKS_MISSING"})
        for raw in raw_checks:
            check_id = _value(raw, "check_id")
            check_id = self._identifier(check_id, "check_id")
            if check_id in seen:
                blocking.append({"check_id": check_id, "code": "DUPLICATE_CHECK"})
                continue
            seen.add(check_id)
            normalized_check, findings = self._normalize_check(
                raw,
                check_id=check_id,
                text_hash=text_hash,
                expected_events=expected_events,
                coverage_required=check_id in coverage_required_ids,
                text_length=text_length,
                default_checker_version=reviewer_version,
            )
            normalized.append(normalized_check)
            blocking.extend(findings)

        missing = [check_id for check_id in required if check_id not in seen]
        blocking.extend({"check_id": check_id, "code": "REQUIRED_CHECK_MISSING"} for check_id in missing)

        for check in normalized:
            is_required = check.check_id in required or check.required
            if not is_required:
                continue
            if check.status == "REJECTED":
                blocking.append({"check_id": check.check_id, "code": check.error_code or "CHECK_REJECTED"})
            elif check.status == "NEEDS_REVISION":
                blocking.append({"check_id": check.check_id, "code": check.error_code or "CHECK_NEEDS_REVISION"})
            elif check.status == "INCOMPLETE":
                blocking.append({"check_id": check.check_id, "code": check.error_code or "CHECK_INCOMPLETE"})

        if any(check.status == "REJECTED" for check in normalized):
            overall = "REJECTED"
        elif any(
            item.get("code") == "CHECK_NEEDS_REVISION"
            or item.get("code") == "EVENT_REJECTED"
            or item.get("code") == "CHECK_ERROR_PRESENT"
            for item in blocking
        ):
            overall = "NEEDS_REVISION"
        elif blocking or any(check.status == "INCOMPLETE" for check in normalized):
            overall = "INCOMPLETE"
        elif any(check.status == "NEEDS_REVISION" for check in normalized):
            overall = "NEEDS_REVISION"
        else:
            overall = "PASSED"
        return ReviewDecision(
            status=overall,
            checks=tuple(normalized),
            blocking_findings=tuple(blocking),
            report_id=report_id,
            work_id=work_id,
            source_id=source_id,
            source_version=source_version,
            knowledge_version=knowledge_version,
            text_hash=text_hash,
            plan_hash=plan_hash,
            context_hash=context_hash,
            reviewer_id=reviewer_id,
            reviewer_version=reviewer_version,
            input_fingerprint=input_fingerprint,
            required_checks=tuple(required),
            event_ids=tuple(expected_events),
            coverage_required=tuple(coverage_required_values),
            text_length=text_length,
            dependency_versions=tuple(normalized_dependencies.items()),
            style_package_version=style_package_version,
            mode=mode,
            scene_id=scene_id,
            chapter_index=chapter_index,
            sacred_whitelist=tuple(sacred_whitelist_values),
        )

    def review(self, request: Optional[Mapping[str, Any] | Any] = None, **kwargs: Any) -> ReviewDecision:
        """调用已配置 reviewer 并验证其输出。

        该方法同时接受一个映射请求或关键字字段，便于 API/CLI 适配。
        reviewer 的 overall_status 不被信任，最终状态只从 checks 重新计算。
        """

        data: dict[str, Any] = {}
        if request is not None:
            if isinstance(request, Mapping):
                data.update(request)
            else:
                for key in (
                    "work_id",
                    "source_id",
                    "source_version",
                    "knowledge_version",
                    "text",
                    "text_hash",
                    "plan_hash",
                    "context_hash",
                    "required_checks",
                    "event_ids",
                    "coverage_required",
                    "text_length",
                    "dependency_versions",
                    "style_package_version",
                    "mode",
                    "scene_id",
                    "chapter_index",
                    "sacred_whitelist",
                ):
                    value = _value(request, key)
                    if value is not None:
                        data[key] = value
        data.update(kwargs)
        self._require_request_fields(data)

        required_checks = self._identifiers(data.get("required_checks", []), "required_checks")
        event_ids = self._identifiers(data.get("event_ids", []), "event_ids")
        coverage_required = self._identifiers(data.get("coverage_required", []), "coverage_required")
        dependency_versions = normalize_dependency_versions(data.get("dependency_versions"))
        fingerprint = _input_fingerprint(
            {
                **data,
                "required_checks": required_checks,
                "event_ids": event_ids,
                "coverage_required": coverage_required,
                "dependency_versions": dependency_versions,
                "sacred_whitelist": self._identifiers(data.get("sacred_whitelist", []), "sacred_whitelist"),
            }
        )
        metadata = {
            "work_id": data["work_id"],
            "source_id": data["source_id"],
            "source_version": data["source_version"],
            "knowledge_version": data["knowledge_version"],
            "text_hash": data["text_hash"],
            "plan_hash": data["plan_hash"],
            "context_hash": data["context_hash"],
            "required_checks": required_checks,
            "event_ids": event_ids,
            "coverage_required": coverage_required,
            "text_length": data.get("text_length"),
            "dependency_versions": dependency_versions,
            "style_package_version": data.get("style_package_version"),
            "mode": data.get("mode"),
            "scene_id": data.get("scene_id"),
            "chapter_index": data.get("chapter_index"),
            "sacred_whitelist": self._identifiers(data.get("sacred_whitelist", []), "sacred_whitelist"),
            "input_fingerprint": fingerprint,
        }
        if not self.reviewer or not self.reviewer_id or not self.reviewer_version:
            return self._incomplete_reviewer_decision(
                metadata,
                "REVIEWER_NOT_CONFIGURED",
                "没有配置带身份和版本的 reviewer",
            )

        reviewer_request = dict(metadata)
        if "text" in data:
            reviewer_request["text"] = data["text"]
        for key in ("scene_id", "chapter_index", "sacred_whitelist", "mode"):
            if key in data:
                reviewer_request[key] = data[key]
        try:
            raw = self._invoke_reviewer(reviewer_request)
        except Exception as exc:
            return self._incomplete_reviewer_decision(
                metadata,
                "REVIEWER_EXECUTION_FAILED",
                f"reviewer 执行失败: {type(exc).__name__}",
            )
        try:
            output = self._reviewer_output(raw)
        except ValidationError as exc:
            return self._incomplete_reviewer_decision(metadata, "REVIEWER_OUTPUT_INVALID", str(exc))

        identity_errors = self._identity_errors(output, metadata)
        if identity_errors:
            return self._incomplete_reviewer_decision(
                metadata,
                "REVIEWER_ATTESTATION_INVALID",
                "; ".join(identity_errors),
            )
        try:
            raw_checks = _mapping_list(output.get("checks"), "reviewer.checks")
        except ValidationError as exc:
            return self._incomplete_reviewer_decision(metadata, "REVIEWER_OUTPUT_INVALID", str(exc))
        try:
            decision = self.adjudicate(
                raw_checks,
                required_checks=required_checks,
                text_hash=metadata["text_hash"],
                event_ids=event_ids,
                coverage_required=coverage_required,
                text_length=metadata["text_length"],
                work_id=metadata["work_id"],
                source_id=metadata["source_id"],
                source_version=metadata["source_version"],
                knowledge_version=metadata["knowledge_version"],
                plan_hash=metadata["plan_hash"],
                context_hash=metadata["context_hash"],
                reviewer_id=self.reviewer_id,
                reviewer_version=self.reviewer_version,
                input_fingerprint=fingerprint,
                report_id=str(output.get("report_id") or self.id_factory()),
                dependency_versions=dependency_versions,
                style_package_version=metadata["style_package_version"],
                mode=metadata["mode"],
                scene_id=metadata["scene_id"],
                chapter_index=metadata["chapter_index"],
                sacred_whitelist=metadata["sacred_whitelist"],
            )
        except ValidationError as exc:
            return self._incomplete_reviewer_decision(metadata, "REVIEWER_OUTPUT_INVALID", str(exc))
        return decision

    def assert_passed(
        self,
        report: Mapping[str, Any] | Any,
        *,
        text_hash: str,
        plan_hash: str,
        context_hash: str,
        work_id: Optional[str] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        knowledge_version: Optional[str] = None,
        dependency_versions: Optional[Mapping[str, Optional[str]]] = None,
    ) -> ReviewDecision:
        """重新裁决报告并验证 reviewer、范围和输入哈希。"""

        if not self.reviewer or not self.reviewer_id or not self.reviewer_version:
            raise ReviewRejectedError("当前服务未配置可验证 reviewer")
        report_text_hash = _value(report, "text_hash")
        report_plan_hash = _value(report, "plan_hash")
        report_context_hash = _value(report, "context_hash")
        if (report_text_hash, report_plan_hash, report_context_hash) != (text_hash, plan_hash, context_hash):
            raise ReviewRejectedError("审核报告与提交的 text/plan/context hash 不匹配")
        for field, expected in (
            ("work_id", work_id),
            ("source_id", source_id),
            ("source_version", source_version),
            ("knowledge_version", knowledge_version),
        ):
            actual = _value(report, field)
            if not isinstance(actual, str) or not actual.strip():
                raise ReviewRejectedError(f"审核报告缺少有效 {field}")
            if expected is not None and actual != expected:
                raise ReviewRejectedError(f"审核报告 {field} 与提交范围不匹配")
        if _value(report, "reviewer_id") != self.reviewer_id or _value(report, "reviewer_version") != self.reviewer_version:
            raise ReviewRejectedError("审核报告 reviewer 身份或版本不匹配")

        report_dependencies = normalize_dependency_versions(_value(report, "dependency_versions", {}))
        if dependency_versions is not None and report_dependencies != normalize_dependency_versions(dependency_versions):
            raise ReviewRejectedError("审核报告 dependency_versions 不匹配")

        raw_checks = _mapping_list(_value(report, "checks", []), "checks")
        if not raw_checks:
            raise ReviewRejectedError("审核报告缺少 checks")
        required_value = _value(report, "required_checks")
        required = (
            self._identifiers(required_value, "required_checks")
            if required_value is not None
            else [
                str(_value(check, "check_id"))
                for check in raw_checks
                if bool(_value(check, "required", True))
            ]
        )
        if not required:
            raise ReviewRejectedError("审核报告缺少 required_checks")
        event_ids = _value(report, "event_ids", []) or []
        coverage_required = _value(report, "coverage_required", []) or []
        expected_fingerprint = _input_fingerprint(
            {
                "work_id": _value(report, "work_id", work_id),
                "source_id": _value(report, "source_id", source_id),
                "source_version": _value(report, "source_version", source_version),
                "knowledge_version": _value(report, "knowledge_version", knowledge_version),
                "text_hash": text_hash,
                "plan_hash": plan_hash,
                "context_hash": context_hash,
                "required_checks": required,
                "event_ids": event_ids,
                "coverage_required": coverage_required,
                "text_length": _value(report, "text_length"),
                "dependency_versions": report_dependencies,
                "style_package_version": _value(report, "style_package_version"),
                "mode": _value(report, "mode"),
                "scene_id": _value(report, "scene_id"),
                "chapter_index": _value(report, "chapter_index"),
                "sacred_whitelist": _value(report, "sacred_whitelist", []) or [],
            }
        )
        report_fingerprint = _value(report, "input_fingerprint")
        if not isinstance(report_fingerprint, str) or not report_fingerprint:
            raise ReviewRejectedError("审核报告缺少 input_fingerprint")
        if report_fingerprint != expected_fingerprint:
            raise ReviewRejectedError("审核报告 input_fingerprint 不匹配")
        decision = self.adjudicate(
            raw_checks,
            required_checks=required,
            text_hash=text_hash,
            event_ids=event_ids,
            coverage_required=coverage_required,
            text_length=_value(report, "text_length"),
            work_id=_value(report, "work_id", work_id),
            source_id=_value(report, "source_id", source_id),
            source_version=_value(report, "source_version", source_version),
            knowledge_version=_value(report, "knowledge_version", knowledge_version),
            plan_hash=plan_hash,
            context_hash=context_hash,
            reviewer_id=self.reviewer_id,
            reviewer_version=self.reviewer_version,
            input_fingerprint=expected_fingerprint,
            report_id=_value(report, "report_id", _value(report, "review_id")),
            dependency_versions=report_dependencies,
            style_package_version=_value(report, "style_package_version"),
            mode=_value(report, "mode"),
            scene_id=_value(report, "scene_id"),
            chapter_index=_value(report, "chapter_index"),
            sacred_whitelist=_value(report, "sacred_whitelist", []) or [],
        )
        if not decision.passed:
            raise ReviewRejectedError(f"审核状态为 {decision.status}，不能提交")
        return decision

    def evaluate(self, *args: Any, **kwargs: Any) -> ReviewDecision:
        """adjudicate 的语义别名，保留旧适配器接口。"""

        return self.adjudicate(*args, **kwargs)

    def _invoke_reviewer(self, request: Mapping[str, Any]) -> Any:
        review_method = getattr(self.reviewer, "review", None)
        if callable(review_method):
            return review_method(request)
        if callable(self.reviewer):
            return self.reviewer(request)
        raise ValidationError("reviewer 必须提供 review 方法或可调用对象")

    @staticmethod
    def _reviewer_output(raw: Any) -> dict[str, Any]:
        if isinstance(raw, ReviewDecision):
            return raw.to_dict()
        model_dump = getattr(raw, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, Mapping):
                return {"checks": [dict(dumped)]}
        if isinstance(raw, Mapping):
            output = dict(raw)
            if "checks" not in output and "check_id" in output:
                return {"checks": [output]}
            return output
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return {"checks": list(raw)}
        raise ValidationError("reviewer 必须返回对象或检查列表")

    def _identity_errors(self, output: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in ("work_id", "source_id", "source_version", "knowledge_version", "text_hash", "plan_hash", "context_hash"):
            value = output.get(field)
            if value is not None and value != metadata[field]:
                errors.append(f"{field} 不匹配")
        output_reviewer_id = output.get("reviewer_id")
        output_reviewer_version = output.get("reviewer_version")
        if output_reviewer_id is not None and output_reviewer_id != self.reviewer_id:
            errors.append("reviewer_id 不匹配")
        if output_reviewer_version is not None and output_reviewer_version != self.reviewer_version:
            errors.append("reviewer_version 不匹配")
        output_fingerprint = output.get("input_fingerprint")
        if output_fingerprint is not None and output_fingerprint != metadata["input_fingerprint"]:
            errors.append("input_fingerprint 不匹配")
        return errors

    def _incomplete_reviewer_decision(
        self,
        metadata: Mapping[str, Any],
        code: str,
        message: str,
    ) -> ReviewDecision:
        check = ReviewCheckResult(
            check_id="reviewer",
            status="INCOMPLETE",
            required=True,
            input_text_hash=metadata["text_hash"],
            error_code=code,
            findings=[{"code": code, "message": message}],
            checker_version=self.reviewer_version,
        )
        return ReviewDecision(
            status="INCOMPLETE",
            checks=(check,),
            blocking_findings=({"check_id": "reviewer", "code": code},),
            report_id=self.id_factory(),
            work_id=metadata["work_id"],
            source_id=metadata["source_id"],
            source_version=metadata["source_version"],
            knowledge_version=metadata["knowledge_version"],
            text_hash=metadata["text_hash"],
            plan_hash=metadata["plan_hash"],
            context_hash=metadata["context_hash"],
            reviewer_id=self.reviewer_id,
            reviewer_version=self.reviewer_version,
            input_fingerprint=metadata["input_fingerprint"],
            required_checks=metadata["required_checks"],
            event_ids=metadata["event_ids"],
            coverage_required=metadata["coverage_required"],
            text_length=metadata["text_length"],
            dependency_versions=normalize_dependency_versions(metadata.get("dependency_versions")),
            style_package_version=metadata.get("style_package_version"),
            mode=metadata.get("mode"),
            scene_id=metadata.get("scene_id"),
            chapter_index=metadata.get("chapter_index"),
            sacred_whitelist=tuple(metadata.get("sacred_whitelist", [])),
        )

    @staticmethod
    def _require_request_fields(data: Mapping[str, Any]) -> None:
        for field in (
            "work_id",
            "source_id",
            "source_version",
            "knowledge_version",
            "text_hash",
            "plan_hash",
            "context_hash",
        ):
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"审核请求缺少有效 {field}")

    @staticmethod
    def _identifier(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{field_name} 必须是非空标识符")
        return value

    @classmethod
    def _identifiers(cls, values: Sequence[str], field_name: str) -> list[str]:
        if isinstance(values, (str, bytes)) or values is None:
            raise ValidationError(f"{field_name} 必须是列表")
        result: list[str] = []
        for value in values:
            identifier = cls._identifier(value, field_name)
            if identifier not in result:
                result.append(identifier)
        return result

    def _normalize_check(
        self,
        raw: Mapping[str, Any] | Any,
        *,
        check_id: str,
        text_hash: str,
        expected_events: Sequence[str],
        coverage_required: bool,
        text_length: Optional[int],
        default_checker_version: Optional[str],
    ) -> tuple[ReviewCheckResult, list[dict[str, Any]]]:
        status = _status(_value(raw, "status"))
        required = bool(_value(raw, "required", True))
        input_hash = _value(raw, "input_text_hash", _value(raw, "text_hash"))
        findings = [dict(item) for item in _mapping_list(_value(raw, "findings", []), f"{check_id}.findings")]
        blocking: list[dict[str, Any]] = []
        if input_hash != text_hash:
            status = "REJECTED"
            blocking.append({"check_id": check_id, "code": "CHECK_INPUT_HASH_MISMATCH"})

        spans = self._spans(_value(raw, "coverage_spans", []), check_id, text_length)
        if coverage_required and not spans:
            status = "INCOMPLETE"
            blocking.append({"check_id": check_id, "code": "COVERAGE_MISSING"})

        raw_events = _mapping_list(_value(raw, "event_results", []), f"{check_id}.event_results")
        event_results = tuple(dict(item) for item in raw_events)
        result_event_ids: set[str] = set()
        for event in raw_events:
            event_id = event.get("event_id")
            if event_id is None:
                continue
            event_key = str(event_id)
            if event_key in result_event_ids:
                status = "INCOMPLETE"
                blocking.append({"check_id": check_id, "code": "DUPLICATE_EVENT_RESULT", "event_id": event_key})
            result_event_ids.add(event_key)
        missing_events = [event_id for event_id in expected_events if event_id not in result_event_ids]
        if expected_events and missing_events:
            status = "INCOMPLETE"
            blocking.append({"check_id": check_id, "code": "EVENT_RESULT_MISSING", "event_ids": missing_events})
        for event in raw_events:
            event_status = _status(event.get("status", event.get("result")))
            if event_status == "REJECTED":
                status = "REJECTED"
                blocking.append({"check_id": check_id, "code": "EVENT_REJECTED", "event_id": event.get("event_id")})
            elif event_status in {"INCOMPLETE", "NEEDS_REVISION"}:
                if status != "REJECTED":
                    status = "INCOMPLETE" if event_status == "INCOMPLETE" else "NEEDS_REVISION"
                blocking.append({"check_id": check_id, "code": "EVENT_RESULT_INCOMPLETE", "event_id": event.get("event_id")})
        error_code = _value(raw, "error_code")
        if error_code and status == "PASSED":
            status = "INCOMPLETE"
            error_code = "CHECK_ERROR_PRESENT"
            blocking.append({"check_id": check_id, "code": error_code})
        if status == "INCOMPLETE" and not any(item.get("check_id") == check_id for item in blocking):
            blocking.append({"check_id": check_id, "code": "CHECK_INCOMPLETE"})
        return (
            ReviewCheckResult(
                check_id=check_id,
                status=status,
                required=required,
                input_text_hash=input_hash,
                coverage_spans=tuple(spans),
                event_results=event_results,
                findings=tuple(findings),
                error_code=error_code or (blocking[0]["code"] if blocking else None),
                checker_version=_value(raw, "checker_version", default_checker_version),
            ),
            blocking,
        )

    @staticmethod
    def _spans(value: Any, check_id: str, text_length: Optional[int]) -> list[tuple[int, int]]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValidationError(f"{check_id}.coverage_spans 必须是列表")
        spans: list[tuple[int, int]] = []
        for index, span in enumerate(value):
            if not isinstance(span, Sequence) or isinstance(span, (str, bytes)) or len(span) != 2:
                raise ValidationError(f"{check_id}.coverage_spans[{index}] 必须是 [start, end]")
            start, end = span
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end < start
            ):
                raise ValidationError(f"{check_id}.coverage_spans[{index}] 坐标非法")
            if text_length is not None and end > text_length:
                raise ValidationError(f"{check_id}.coverage_spans[{index}] 超出正文长度")
            spans.append((start, end))
        return spans


__all__ = [
    "ReviewCheckResult",
    "ReviewDecision",
    "ReviewRejectedError",
    "Reviewer",
    "WritingReviewService",
    "dependency_hash",
    "normalize_dependency_versions",
    "review_input_fingerprint",
]
