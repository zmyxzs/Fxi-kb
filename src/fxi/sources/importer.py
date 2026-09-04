"""
fxi.sources.importer - 原始小说参考文本导入器
"""

import hashlib
import shutil
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel

from fxi.core.config import FxiConfig, load_config
from fxi.sources.segmenter import TextSegmenter
from fxi.index_retrieval.jieba_fts import ChineseFTS


class SourceManifest(BaseModel):
    source_id: str
    title: str
    file_name: str
    sha256: str
    total_chars: int
    total_scenes: int
    total_chapters: int = 1


class SourceImporter:
    """原始文本导入与切片处理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.segmenter = TextSegmenter()
        self.fts = ChineseFTS(self.config)

    def import_file(self, raw_file_path: Path, source_id: str, title: str) -> SourceManifest:
        """导入参考原著 txt/md，校验编码并计算哈希，存储原始不可变底本"""
        try:
            content = raw_file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = raw_file_path.read_text(encoding="gb18030", errors="replace")

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        target_dir = self.config.sources_dir / source_id
        target_dir.mkdir(parents=True, exist_ok=True)
        raw_target = target_dir / "raw.txt"
        raw_target.write_text(content, encoding="utf-8")

        # 切片处理
        scenes = self.segmenter.split_into_scenes(content, source_id=source_id, chapter_index=1)
        scenes_dir = target_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        for sc in scenes:
            sc_file = scenes_dir / f"{sc.scene_uuid}.md"
            sc_file.write_text(sc.content, encoding="utf-8")
            # 建立全文索引
            self.fts.index_scene(
                scene_uuid=sc.scene_uuid,
                work_id=source_id,
                chapter_index=sc.chapter_index,
                content=sc.content
            )

        manifest = SourceManifest(
            source_id=source_id,
            title=title,
            file_name=raw_file_path.name,
            sha256=digest,
            total_chars=len(content),
            total_scenes=len(scenes),
            total_chapters=1,
        )

        manifest_file = target_dir / "source.yaml"
        manifest_file.write_text(
            yaml.dump(manifest.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )
        return manifest

    def import_chapters_dir(
        self,
        chapters_dir: Path,
        source_id: str,
        title: str,
        max_chapters: Optional[int] = None
    ) -> SourceManifest:
        """
        批量导入章节文件目录 (如 ch001.md, ch002.md...)
        按章序号排序，逐章切分场景，建立场景 UUID，写入 scenes/ 并灌入 FTS5 全文索引。
        """
        import re
        from fxi.storage.sqlite_client import DatabaseClient, ensure_work

        all_files = [f for f in chapters_dir.iterdir() if f.is_file() and f.suffix.lower() in (".md", ".txt")]

        def extract_num(p: Path) -> int:
            m = re.search(r"\d+", p.stem)
            return int(m.group(0)) if m else 999999

        sorted_files = sorted(all_files, key=extract_num)
        if max_chapters is not None:
            sorted_files = sorted_files[:max_chapters]

        target_dir = self.config.sources_dir / source_id
        target_dir.mkdir(parents=True, exist_ok=True)
        chapters_store_dir = target_dir / "chapters"
        chapters_store_dir.mkdir(parents=True, exist_ok=True)
        scenes_dir = target_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        total_chars = 0
        total_scenes = 0
        chapters_meta = []
        hasher = hashlib.sha256()

        for ch_idx, ch_file in enumerate(sorted_files, start=1):
            try:
                content = ch_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = ch_file.read_text(encoding="gb18030", errors="replace")

            hasher.update(content.encode("utf-8"))
            total_chars += len(content)

            lines = [line.strip() for line in content.splitlines() if line.strip()]
            ch_title = lines[0] if lines else f"第{ch_idx}章"

            stored_ch_file = chapters_store_dir / f"ch{ch_idx:03d}.md"
            stored_ch_file.write_text(content, encoding="utf-8")

            scenes = self.segmenter.split_into_scenes(content, source_id=source_id, chapter_index=ch_idx)
            total_scenes += len(scenes)

            for sc in scenes:
                sc_file = scenes_dir / f"{sc.scene_uuid}.md"
                sc_file.write_text(sc.content, encoding="utf-8")
                self.fts.index_scene(
                    scene_uuid=sc.scene_uuid,
                    work_id=source_id,
                    chapter_index=sc.chapter_index,
                    content=sc.content
                )

            chapters_meta.append({
                "chapter_index": ch_idx,
                "title": ch_title,
                "file_name": ch_file.name,
                "chars": len(content),
                "scenes_count": len(scenes)
            })

        # 确保数据库 works 记录同步存在
        client = DatabaseClient(self.config.sqlite_path)
        with client.transaction() as cur:
            ensure_work(cur, source_id)
            cur.execute(
                "UPDATE works SET title = ?, updated_at = datetime('now') WHERE work_id = ?",
                (title, source_id)
            )

        manifest = SourceManifest(
            source_id=source_id,
            title=title,
            file_name=f"{len(sorted_files)} chapters from {chapters_dir.name}",
            sha256=hasher.hexdigest(),
            total_chars=total_chars,
            total_scenes=total_scenes,
            total_chapters=len(sorted_files),
        )

        manifest_dict = manifest.model_dump()
        manifest_dict["chapters"] = chapters_meta

        manifest_file = target_dir / "source.yaml"
        manifest_file.write_text(
            yaml.dump(manifest_dict, allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )
        return manifest
