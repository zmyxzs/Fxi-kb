"""K3 场景标识、切片和版本化全文索引的回归测试。"""

from __future__ import annotations

import pytest

from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.sources.importer import SourceImporter
from fxi.sources.scene_id import generate_scene_uuid
from fxi.sources.segmenter import TextSegmenter
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


def test_scene_uuid_uses_content_anchor_not_sequence() -> None:
    first = generate_scene_uuid("source-a", 1, 1, semantic_slug="anchor", content="稳定正文")
    moved = generate_scene_uuid("source-a", 99, 7, semantic_slug="anchor", content="稳定正文")
    changed = generate_scene_uuid("source-a", 99, 7, semantic_slug="anchor", content="变化正文")

    assert first == moved
    assert first != changed
    assert "anchor" in first


def test_segmenter_splits_oversized_paragraph_and_anchors_each_chunk() -> None:
    segmenter = TextSegmenter(min_chars=0, max_chars=5)
    chunks = segmenter.split_into_scenes("abcdefghijk", "source-a", chapter_index=1)

    assert [chunk.content for chunk in chunks] == ["abcde", "fghij", "k"]
    assert all(chunk.char_count == len(chunk.content) <= 5 for chunk in chunks)
    assert all(
        chunk.scene_uuid
        == generate_scene_uuid("source-a", 1, chunk.scene_seq, content=chunk.content)
        for chunk in chunks
    )


def test_segmenter_reordering_keeps_content_anchored_ids() -> None:
    segmenter = TextSegmenter(min_chars=0, max_chars=6)
    first = segmenter.split_into_scenes("abcde\n\nvwxyz", "source-a", chapter_index=1)
    moved = segmenter.split_into_scenes("vwxyz\n\nabcde", "source-a", chapter_index=8)

    assert {chunk.scene_uuid for chunk in first} == {chunk.scene_uuid for chunk in moved}


def test_fts_versioned_replace_and_search_are_source_scoped(temp_workspace) -> None:
    fts = ChineseFTS(temp_workspace)
    fts.replace_source_index(
        work_id="work_a",
        source_id="source-a",
        source_version="version-a",
        scenes=[
            {"scene_uuid": "scene-a", "chapter_index": 1, "content": "稳定词"},
            {"scene_uuid": "scene-stale", "chapter_index": 2, "content": "旧词"},
        ],
    )
    fts.replace_source_index(
        work_id="work_a",
        source_id="source-a",
        source_version="version-a",
        scenes=[{"scene_uuid": "scene-a", "chapter_index": 1, "content": "稳定词"}],
    )

    hits = fts.search("稳定词", work_id="work_a", source_id="source-a", source_version="version-a")
    stale = fts.search("旧词", work_id="work_a", source_id="source-a", source_version="version-a")

    assert [item["scene_uuid"] for item in hits] == ["scene-a"]
    assert stale == []
    assert hits[0]["source_id"] == "source-a"
    assert hits[0]["source_version"] == "version-a"


def test_fts_versioned_search_requires_complete_binding(temp_workspace) -> None:
    fts = ChineseFTS(temp_workspace)

    with pytest.raises(ValueError, match="source_id 与 source_version"):
        fts.search("词", work_id="work_a", source_id="source-a")


def test_fts_versioned_replace_rolls_back_on_invalid_scene(temp_workspace) -> None:
    fts = ChineseFTS(temp_workspace)
    fts.replace_source_index(
        work_id="work_a",
        source_id="source-a",
        source_version="version-a",
        scenes=[{"scene_uuid": "scene-a", "chapter_index": 1, "content": "稳定词"}],
    )

    with pytest.raises(ValueError, match="scene_uuid"):
        fts.replace_source_index(
            work_id="work_a",
            source_id="source-a",
            source_version="version-a",
            scenes=[
                {"scene_uuid": "scene-b", "chapter_index": 2, "content": "新词"},
                {"scene_uuid": "../unsafe", "chapter_index": 3, "content": "非法词"},
            ],
        )

    hits = fts.search("稳定词", work_id="work_a", source_id="source-a", source_version="version-a")
    assert [item["scene_uuid"] for item in hits] == ["scene-a"]


def test_fts_replace_preserves_explicit_source_versions_for_rollback(temp_workspace) -> None:
    fts = ChineseFTS(temp_workspace)
    fts.replace_source_index(
        work_id="work_a",
        source_id="source-a",
        source_version="version-a",
        scenes=[{"scene_uuid": "scene-a", "chapter_index": 1, "content": "旧版本词"}],
    )
    fts.replace_source_index(
        work_id="work_a",
        source_id="source-a",
        source_version="version-b",
        scenes=[{"scene_uuid": "scene-b", "chapter_index": 1, "content": "新版本词"}],
    )

    assert [item["scene_uuid"] for item in fts.search(
        "旧版本词", work_id="work_a", source_id="source-a", source_version="version-a"
    )] == ["scene-a"]
    assert [item["scene_uuid"] for item in fts.search(
        "新版本词", work_id="work_a", source_id="source-a", source_version="version-b"
    )] == ["scene-b"]
    assert fts.search(
        "旧版本词", work_id="work_a", source_id="source-a", source_version="version-b"
    ) == []


def test_importer_reimport_replaces_visible_tree_and_versioned_fts(temp_workspace) -> None:
    with DatabaseClient(temp_workspace.sqlite_path).transaction() as cur:
        ensure_work(cur, "work_a")
    chapters_dir = temp_workspace.workspace_root / "chapters"
    chapters_dir.mkdir()
    chapter = chapters_dir / "ch001.md"
    chapter.write_text("第一章\n旧版本词", encoding="utf-8")
    importer = SourceImporter(temp_workspace)

    first = importer.import_chapters_dir(chapters_dir, "source-a", "测试来源", work_id="work_a")
    first_hits = importer.fts.search(
        "旧版本词", work_id="work_a", source_id="source-a", source_version=first.version
    )
    assert len(first_hits) == 1
    old_scene = first_hits[0]["scene_uuid"]

    chapter.write_text("第一章\n新版本词", encoding="utf-8")
    second = importer.import_chapters_dir(chapters_dir, "source-a", "测试来源", work_id="work_a")

    old_hits = importer.fts.search(
        "旧版本词", work_id="work_a", source_id="source-a", source_version=first.version
    )
    assert old_hits
    assert importer.fts.search(
        "新版本词", work_id="work_a", source_id="source-a", source_version=second.version
    )
    assert not (temp_workspace.sources_dir / "source-a" / "scenes" / f"{old_scene}.md").exists()


def test_importer_rolls_back_directory_and_binding_when_fts_publish_fails(temp_workspace, monkeypatch) -> None:
    with DatabaseClient(temp_workspace.sqlite_path).transaction() as cur:
        ensure_work(cur, "work_a")
    chapters_dir = temp_workspace.workspace_root / "chapters"
    chapters_dir.mkdir()
    chapter = chapters_dir / "ch001.md"
    chapter.write_text("第一章\n稳定旧词", encoding="utf-8")
    importer = SourceImporter(temp_workspace)
    first = importer.import_chapters_dir(chapters_dir, "source-a", "测试来源", work_id="work_a")
    old_text = (temp_workspace.sources_dir / "source-a" / "chapters" / "ch001.md").read_text(encoding="utf-8")

    chapter.write_text("第一章\n不应发布", encoding="utf-8")

    def fail_replace(**kwargs):
        raise RuntimeError("fts unavailable")

    monkeypatch.setattr(importer.fts, "replace_source_index", fail_replace)
    with pytest.raises(RuntimeError, match="fts unavailable"):
        importer.import_chapters_dir(chapters_dir, "source-a", "测试来源", work_id="work_a")

    assert (temp_workspace.sources_dir / "source-a" / "chapters" / "ch001.md").read_text(encoding="utf-8") == old_text
    with DatabaseClient(temp_workspace.sqlite_path).get_connection() as conn:
        row = conn.execute(
            "SELECT source_version FROM work_sources WHERE source_id = ? AND work_id = ?",
            ("source-a", "work_a"),
        ).fetchone()
    assert row["source_version"] == first.version
