"""
fxi.sources.importer - 原始小说参考文本导入器
"""

import hashlib
import re
import shutil
from uuid import uuid4
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_work_id
from fxi.sources.evidence_store import EvidenceStore
from fxi.sources.segmenter import TextSegmenter
from fxi.storage.text_io import calculate_content_hash
from fxi.storage.versioned_store import (
    DuplicateDocumentError,
    MissingChapterError,
    NORMALIZATION_VERSION,
    SourceDocumentInput,
    validate_object_segment,
)
from fxi.index_retrieval.jieba_fts import ChineseFTS


class SourceManifest(BaseModel):
    source_id: str
    work_id: Optional[str] = None
    title: str
    file_name: str
    sha256: str
    total_chars: int
    total_scenes: int
    total_chapters: int = 1
    version: Optional[str] = None
    normalization_version: str = NORMALIZATION_VERSION
    documents: list[dict] = Field(default_factory=list)


class SourceImporter:
    """原始文本导入与切片处理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.segmenter = TextSegmenter()
        self.fts = ChineseFTS(self.config)
        self.evidence_store = EvidenceStore(self.config.sources_dir)

    @staticmethod
    def _read_document(
        file_path: Path,
        *,
        document_id: str,
        chapter_index: int,
        relative_path: str,
    ) -> SourceDocumentInput:
        return SourceDocumentInput.from_file(
            file_path,
            document_id=document_id,
            chapter_index=chapter_index,
            relative_path=relative_path,
        )

    @staticmethod
    def _write_legacy_text(path: Path, content: str) -> None:
        """保留旧目录兼容性；历史正文已由 EvidenceStore 保存，不删除旧对象。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _manifest_documents(snapshot) -> list[dict]:
        return [document.to_dict() for document in snapshot.documents]

    @staticmethod
    def _assert_source_binding_available(sqlite_path: Path, source_id: str, work_id: str) -> None:
        """在创建不可变对象前拦截已绑定其他作品的来源。"""

        from fxi.storage.sqlite_client import DatabaseClient

        client = DatabaseClient(sqlite_path)
        with client.transaction() as cur:
            bindings = cur.execute(
                "SELECT DISTINCT work_id FROM work_sources WHERE source_id = ?",
                (source_id,),
            ).fetchall()
        if any(binding["work_id"] != work_id for binding in bindings):
            raise ValidationError("来源已绑定其他作品")

    def _staging_dir(self, source_id: str, version: str) -> Path:
        validate_object_segment(source_id, "source_id")
        validate_object_segment(version, "source_version")
        return self.config.sources_dir / ".staging" / source_id / f"{version}-{uuid4().hex}"

    @staticmethod
    def _publish_staged_tree(staging_dir: Path, target_dir: Path) -> Optional[Path]:
        """以目录重命名切换兼容视图，失败时保留旧目录可恢复。"""

        target_dir.parent.mkdir(parents=True, exist_ok=True)
        previous_dir: Optional[Path] = None
        if target_dir.exists():
            previous_dir = target_dir.parent / f".{target_dir.name}.previous-{uuid4().hex}"
            target_dir.rename(previous_dir)
        try:
            staging_dir.rename(target_dir)
        except OSError:
            if previous_dir is not None and not target_dir.exists():
                previous_dir.rename(target_dir)
            raise
        return previous_dir

    @staticmethod
    def _restore_published_tree(target_dir: Path, previous_dir: Optional[Path]) -> None:
        """恢复旧兼容视图；恢复失败必须向上暴露。"""

        if target_dir.exists():
            shutil.rmtree(target_dir)
        if previous_dir is not None and previous_dir.exists():
            previous_dir.rename(target_dir)

    @staticmethod
    def _discard_previous_tree(previous_dir: Optional[Path]) -> None:
        if previous_dir is not None and previous_dir.exists():
            shutil.rmtree(previous_dir)

    def _persist_source_binding(
        self,
        source_id: str,
        work_id: str,
        target_dir: Path,
        source_version: str,
        title: str,
    ) -> None:
        from fxi.storage.sqlite_client import DatabaseClient, ensure_work

        client = DatabaseClient(self.config.sqlite_path)
        with client.transaction() as cur:
            existing_work = cur.execute(
                "SELECT 1 FROM works WHERE work_id = ?",
                (work_id,),
            ).fetchone()
            if existing_work is None:
                if work_id != source_id:
                    raise ValidationError(f"作品未注册: {work_id}")
                ensure_work(cur, work_id)
            conflict = cur.execute(
                "SELECT DISTINCT work_id FROM work_sources WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            if any(row["work_id"] != work_id for row in conflict):
                raise ValidationError("来源已绑定其他作品")
            cur.execute(
                """
                INSERT INTO work_sources (source_id, work_id, source_dir, source_version, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_id, work_id) DO UPDATE SET
                    source_dir = excluded.source_dir,
                    source_version = excluded.source_version
                """,
                (source_id, work_id, str(target_dir), source_version),
            )
            cur.execute(
                "UPDATE works SET title = ?, updated_at = datetime('now') WHERE work_id = ?",
                (title, work_id),
            )

    def _read_source_binding(
        self,
        source_id: str,
        work_id: str,
    ) -> Optional[dict[str, object]]:
        """读取发布前绑定，用于跨文件/数据库失败时恢复旧指针。"""

        from fxi.storage.sqlite_client import DatabaseClient

        client = DatabaseClient(self.config.sqlite_path)
        with client.get_connection() as conn:
            row = conn.execute(
                """
                SELECT source_id, work_id, source_dir, source_version, created_at
                FROM work_sources
                WHERE source_id = ? AND work_id = ?
                """,
                (source_id, work_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def _restore_source_binding(
        self,
        source_id: str,
        work_id: str,
        previous: Optional[dict[str, object]],
    ) -> None:
        """恢复发布前来源指针；恢复失败不得被吞掉。"""

        from fxi.storage.sqlite_client import DatabaseClient

        client = DatabaseClient(self.config.sqlite_path)
        with client.transaction() as cur:
            if previous is None:
                cur.execute(
                    "DELETE FROM work_sources WHERE source_id = ? AND work_id = ?",
                    (source_id, work_id),
                )
                return
            cur.execute(
                """
                INSERT INTO work_sources
                    (source_id, work_id, source_dir, source_version, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, work_id) DO UPDATE SET
                    source_dir = excluded.source_dir,
                    source_version = excluded.source_version,
                    created_at = excluded.created_at
                """,
                (
                    previous["source_id"],
                    previous["work_id"],
                    previous.get("source_dir"),
                    previous.get("source_version"),
                    previous["created_at"],
                ),
            )

    def _publish_staged_source(
        self,
        *,
        source_id: str,
        work_id: str,
        target_dir: Path,
        staging_dir: Path,
        source_version: str,
        title: str,
        scenes: list[dict[str, object]],
    ) -> None:
        """发布目录、来源指针和 FTS；任一步失败都恢复旧可见版本。"""

        previous_binding = self._read_source_binding(source_id, work_id)
        previous_dir: Optional[Path] = None
        published = False
        binding_persisted = False
        try:
            previous_dir = self._publish_staged_tree(staging_dir, target_dir)
            published = True
            self._persist_source_binding(
                source_id,
                work_id,
                target_dir,
                source_version,
                title,
            )
            binding_persisted = True
            self.fts.replace_source_index(
                work_id=work_id,
                source_id=source_id,
                source_version=source_version,
                scenes=scenes,
            )
        except Exception as exc:
            rollback_errors: list[BaseException] = []
            if binding_persisted:
                try:
                    self._restore_source_binding(source_id, work_id, previous_binding)
                except BaseException as rollback_error:
                    rollback_errors.append(rollback_error)
            if published:
                try:
                    self._restore_published_tree(target_dir, previous_dir)
                except BaseException as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                details = "; ".join(type(error).__name__ for error in rollback_errors)
                raise ValidationError(f"来源发布失败且回滚不完整: {details}") from exc
            raise
        self._discard_previous_tree(previous_dir)

    def import_file(
        self,
        raw_file_path: Path,
        source_id: str,
        title: str,
        *,
        work_id: Optional[str] = None,
    ) -> SourceManifest:
        """导入参考原著 txt/md，严格解码并创建不可变来源快照。"""
        validate_object_segment(source_id, "source_id")
        resolved_work_id = validate_work_id(work_id or source_id)
        raw_file_path = Path(raw_file_path)
        document = self._read_document(
            raw_file_path,
            document_id="raw",
            chapter_index=1,
            relative_path="raw.txt",
        )
        self._assert_source_binding_available(self.config.sqlite_path, source_id, resolved_work_id)
        snapshot = self.evidence_store.create_snapshot(
            source_id,
            [document],
            metadata={"title": title, "file_name": raw_file_path.name},
        )
        content = document.text
        digest = calculate_content_hash(content)

        target_dir = self.config.sources_dir / source_id
        staging_dir = self._staging_dir(source_id, snapshot.version)
        self._write_legacy_text(staging_dir / "raw.txt", content)

        # 切片处理
        scenes = self.segmenter.split_into_scenes(content, source_id=source_id, chapter_index=1)
        scenes_dir = staging_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        scenes_for_index: list[dict[str, object]] = []
        for sc in scenes:
            sc_file = scenes_dir / f"{sc.scene_uuid}.md"
            self._write_legacy_text(sc_file, sc.content)
            scenes_for_index.append(
                {
                    "scene_uuid": sc.scene_uuid,
                    "chapter_index": sc.chapter_index,
                    "content": sc.content,
                }
            )

        manifest = SourceManifest(
            source_id=source_id,
            work_id=resolved_work_id,
            title=title,
            file_name=raw_file_path.name,
            sha256=digest,
            total_chars=len(content),
            total_scenes=len(scenes),
            total_chapters=1,
            version=snapshot.version,
            documents=self._manifest_documents(snapshot),
        )
        (staging_dir / "source.yaml").write_text(
            yaml.safe_dump(
                manifest.model_dump(exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8"
        )
        self._publish_staged_source(
            source_id=source_id,
            work_id=resolved_work_id,
            target_dir=target_dir,
            staging_dir=staging_dir,
            source_version=snapshot.version,
            title=title,
            scenes=scenes_for_index,
        )
        return manifest
    def import_chapters_dir(
        self,
        chapters_dir: Path,
        source_id: str,
        title: str,
        max_chapters: Optional[int] = None,
        *,
        work_id: Optional[str] = None,
    ) -> SourceManifest:
        """严格读取章节，创建不可变快照，再生成兼容旧目录的派生文件。"""

        validate_object_segment(source_id, "source_id")
        resolved_work_id = validate_work_id(work_id or source_id)
        chapters_dir = Path(chapters_dir)
        if not chapters_dir.is_dir():
            raise ValidationError(f"章节目录不存在: {chapters_dir}")
        if max_chapters is not None and (
            isinstance(max_chapters, bool)
            or not isinstance(max_chapters, int)
            or max_chapters < 1
        ):
            raise ValidationError("max_chapters 必须是正整数")
        all_files = [
            path for path in chapters_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}
        ]
        if not all_files:
            raise MissingChapterError(f"章节目录为空: {chapters_dir}")

        def extract_num(path: Path) -> Optional[int]:
            match = re.search(r"\d+", path.stem)
            return int(match.group(0)) if match else None

        numbered = [(extract_num(path), path) for path in all_files]
        numeric_values = [number for number, _ in numbered if number is not None]
        if len(numeric_values) != len(set(numeric_values)):
            raise DuplicateDocumentError("章节文件序号重复")
        if numeric_values and len(numeric_values) != len(all_files):
            raise MissingChapterError("章节文件名必须统一包含章节序号")
        sorted_files = [path for _, path in sorted(numbered, key=lambda item: (item[0] is None, item[0] or 0, item[1].name))]
        if max_chapters is not None:
            sorted_files = sorted_files[:max_chapters]
        expected_numbers = list(range(1, len(sorted_files) + 1))
        actual_numbers = [extract_num(path) for path in sorted_files]
        if actual_numbers != expected_numbers:
            raise MissingChapterError(f"章节序号不连续: expected={expected_numbers}, actual={actual_numbers}")

        documents: list[SourceDocumentInput] = []
        for chapter_index, chapter_path in enumerate(sorted_files, start=1):
            documents.append(
                self._read_document(
                    chapter_path,
                    document_id=f"ch{chapter_index:03d}",
                    chapter_index=chapter_index,
                    relative_path=f"chapters/ch{chapter_index:03d}.md",
                )
            )
        self._assert_source_binding_available(self.config.sqlite_path, source_id, resolved_work_id)
        corpus_hash = hashlib.sha256()
        for document in documents:
            corpus_hash.update(document.text.encode("utf-8"))
        source_version = corpus_hash.hexdigest()
        snapshot = self.evidence_store.create_snapshot(
            source_id,
            documents,
            version=source_version,
            metadata={"title": title, "file_name": f"{len(documents)} chapters from {chapters_dir.name}"},
        )

        target_dir = self.config.sources_dir / source_id
        staging_dir = self._staging_dir(source_id, snapshot.version)
        chapters_store_dir = staging_dir / "chapters"
        scenes_dir = staging_dir / "scenes"
        chapters_store_dir.mkdir(parents=True, exist_ok=True)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        total_chars = 0
        total_scenes = 0
        chapters_meta: list[dict] = []
        scenes_for_index: list[dict[str, object]] = []
        for document in documents:
            content = document.text
            total_chars += len(content)
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            chapter_title = lines[0] if lines else f"第{document.chapter_index}章"
            self._write_legacy_text(chapters_store_dir / document.relative_path.split("/", 1)[1], content)
            scenes = self.segmenter.split_into_scenes(content, source_id=source_id, chapter_index=document.chapter_index)
            total_scenes += len(scenes)
            for scene in scenes:
                self._write_legacy_text(scenes_dir / f"{scene.scene_uuid}.md", scene.content)
                scenes_for_index.append(
                    {
                        "scene_uuid": scene.scene_uuid,
                        "chapter_index": scene.chapter_index,
                        "content": scene.content,
                    }
                )
            chapters_meta.append({
                "chapter_index": document.chapter_index,
                "title": chapter_title,
                "file_name": document.relative_path.rsplit("/", 1)[-1],
                "chars": len(content),
                "scenes_count": len(scenes),
            })

        manifest = SourceManifest(
            source_id=source_id,
            work_id=resolved_work_id,
            title=title,
            file_name=f"{len(documents)} chapters from {chapters_dir.name}",
            sha256=source_version,
            total_chars=total_chars,
            total_scenes=total_scenes,
            total_chapters=len(documents),
            version=snapshot.version,
            documents=self._manifest_documents(snapshot),
        )
        manifest_dict = manifest.model_dump(exclude_none=True)
        manifest_dict["chapters"] = chapters_meta
        (staging_dir / "source.yaml").write_text(
            yaml.safe_dump(manifest_dict, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._publish_staged_source(
            source_id=source_id,
            work_id=resolved_work_id,
            target_dir=target_dir,
            staging_dir=staging_dir,
            source_version=snapshot.version,
            title=title,
            scenes=scenes_for_index,
        )
        return manifest
