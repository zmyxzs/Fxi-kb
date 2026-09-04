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
        )

        manifest_file = target_dir / "source.yaml"
        manifest_file.write_text(
            yaml.dump(manifest.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8"
        )
        return manifest
