"""检索来源绑定与失败可诊断性的最小回归覆盖。"""

import shutil
import uuid
from pathlib import Path

import pytest

from fxi.core.canonical import sha256_hex
from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.index_retrieval.query_engine import QueryDecomposition, QueryEngine
from fxi.sources.evidence_store import EvidenceStore
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


@pytest.fixture
def temp_workspace():
    root = Path("D:/Code/Fxi/.codex") / f"source-scope-{uuid.uuid4().hex}"
    data_dir = root / "data"
    config = FxiConfig(
        workspace_root=root,
        data_dir=data_dir,
        projects_dir=root / "projects",
        skills_dir=root / "skills",
        sources_dir=root / "sources",
        materials_dir=root / "materials",
        sqlite_path=data_dir / "manifest.sqlite",
        cache_db_path=data_dir / "cache.sqlite",
        jieba_custom_dict_path=data_dir / "project_lexicon.txt",
    )
    config.ensure_directories()
    client = DatabaseClient(config.sqlite_path)
    client.init_db()
    from fxi.storage.sqlite_client import ensure_work

    with client.transaction() as cur:
        for work_id in ("work_a", "work_b"):
            ensure_work(cur, work_id)
    try:
        yield config
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _insert_shared_source_claim(config) -> None:
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        cur.executemany(
            """
            INSERT INTO work_sources
                (source_id, work_id, source_dir, source_version, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("shared-source", "work_a", "work_a", "version-a", "now"),
                ("shared-source", "work_b", "work_b", "version-b", "now"),
            ],
        )
        cur.execute(
            """
            INSERT INTO claim_families
                (family_key, claim_family_id, work_id, owner_id, scope, created_at)
            VALUES ('shared-family', 'shared-family', 'work_a', 'test-owner', 'public', 'now')
            """
        )
        cur.execute(
            """
            INSERT INTO claim_versions
                (family_key, claim_id, claim_family_id, version, work_id,
                 status, statement, semantic_hash, created_at)
            VALUES ('shared-family', 'shared-claim', 'shared-family', 1, 'work_a',
                    'accepted', '作品 A 的来源事实', 'hash-shared-claim', 'now')
            """
        )
        cur.execute(
            """
            INSERT INTO claim_evidence
                (claim_id, source_id, scene_uuid, line_start, line_end, quote, created_at)
            VALUES ('shared-claim', 'shared-source', 'scene-a', 0, 4, '作品 A 引文', 'now')
            """
        )


def _candidate_binding(config, *, text: str = "synthetic extraction evidence"):
    """Create the immutable synthetic binding required by extractor entrypoints."""
    input_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        "synthetic-source",
        [
            SourceDocumentInput(
                document_id="synthetic-document",
                chapter_index=1,
                raw_bytes=text.encode("utf-8"),
                text=text,
                relative_path="synthetic.txt",
                expected_content_hash=input_hash,
            )
        ],
        version="synthetic-v1",
    )
    return {
        "source_id": "synthetic-source",
        "source_version": "synthetic-v1",
        "input_hash": input_hash,
        "evidence_refs": [
            {
                "evidence_id": "synthetic-evidence",
                "source_id": "synthetic-source",
                "source_version": "synthetic-v1",
                "document_id": "synthetic-document",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": input_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "synthetic-evaluation",
        "submitted_by": "synthetic-extractor",
    }


def test_source_binding_is_scoped_by_work_for_query_and_context(temp_workspace):
    _insert_shared_source_claim(temp_workspace)
    engine = QueryEngine(temp_workspace)
    from fxi.index_retrieval.context_pruner import ContextPruner

    pruner = ContextPruner(temp_workspace)

    with DatabaseClient(temp_workspace.sqlite_path).get_connection() as conn:
        query_a = engine._load_claims(
            conn, "work_a", source_id="shared-source", source_version="version-a"
        )
        query_b_version = engine._load_claims(
            conn, "work_a", source_id="shared-source", source_version="version-b"
        )
        query_unversioned = engine._load_claims(
            conn, "work_a", source_id="shared-source"
        )

    context_a = pruner._load_claims(
        "work_a", source_id="shared-source", source_version="version-a"
    )
    context_b_version = pruner._load_claims(
        "work_a", source_id="shared-source", source_version="version-b"
    )
    context_unversioned = pruner._load_claims(
        "work_a", source_id="shared-source"
    )

    assert query_a[0]["evidence"][0]["source_version"] == "version-a"
    assert query_b_version[0]["evidence"] == []
    assert [item["source_version"] for item in query_unversioned[0]["evidence"]] == ["version-a"]
    assert context_a[0]["evidence"][0]["source_version"] == "version-a"
    assert context_b_version[0]["evidence"] == []
    assert [item["source_version"] for item in context_unversioned[0]["evidence"]] == ["version-a"]


def test_model_decomposition_failure_is_returned_as_diagnostic(temp_workspace, monkeypatch):
    engine = QueryEngine(temp_workspace)

    def fail(*args, **kwargs):
        raise RuntimeError("gateway unavailable")

    monkeypatch.setattr(engine.gateway, "complete", fail)
    result = engine.decompose_query("work_a", "which unknown entity is referenced?")

    assert result.target_entities == []
    assert result.diagnostics[0]["code"] == "MODEL_DECOMPOSITION_FAILED"
    assert result.diagnostics[0]["retryable"] is True


def test_project_document_failure_is_returned_as_diagnostic(temp_workspace, monkeypatch):
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "doc-character", name="文档角色", category="character"
    )
    doc_path = temp_workspace.projects_dir / "work_a" / "entities" / "characters" / "doc-character.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("---\nname: 文档角色\n---\n\n档案正文", encoding="utf-8")

    import fxi.index_retrieval.query_engine as query_engine_module

    def fail(*args, **kwargs):
        raise OSError("document unavailable")

    monkeypatch.setattr(query_engine_module, "read_markdown_frontmatter", fail)
    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a", QueryDecomposition(target_entities=["文档角色"], keywords=[])
    )

    assert evidence["entities"][0]["bio"] == ""
    assert evidence["entities"][0]["bio_source"] == "unavailable"
    diagnostic = next(item for item in evidence["diagnostics"] if item["code"] == "PROJECT_DOCUMENT_READ_FAILED")
    assert diagnostic["retryable"] is False


def test_scene_document_failure_is_returned_as_diagnostic(temp_workspace, monkeypatch):
    scene_uuid = "scene-document"
    ChineseFTS(temp_workspace).index_scene(scene_uuid, "work_a", 1, "场景检索词")
    scene_path = temp_workspace.sources_dir / "work_a" / "scenes" / f"{scene_uuid}.md"
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text("场景正文", encoding="utf-8")

    original_read_text = Path.read_text

    def fail_scene(path, *args, **kwargs):
        if path == scene_path:
            raise OSError("scene document unavailable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_scene)
    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a", QueryDecomposition(target_entities=["主角"], keywords=["场景检索词"])
    )

    scene = evidence["scenes"][0]
    assert scene["content_source"] == "fts_snippet"
    assert scene["document_available"] is False
    diagnostic = next(item for item in evidence["diagnostics"] if item["code"] == "SCENE_DOCUMENT_READ_FAILED")
    assert diagnostic["retryable"] is False


def test_source_scoped_scene_uses_immutable_object_not_current_file(temp_workspace):
    immutable_text = "快照中的场景正文"
    snapshot = EvidenceStore(temp_workspace.sources_dir).create_snapshot(
        "book",
        [
            SourceDocumentInput(
                document_id="ch001",
                chapter_index=1,
                raw_bytes=immutable_text.encode("utf-8"),
                text=immutable_text,
                relative_path="chapters/ch001.md",
            )
        ],
        version="version-a",
    )
    ChineseFTS(temp_workspace).index_scene("scene-versioned", "work_a", 1, "场景检索词")
    scene_path = temp_workspace.sources_dir / "work_a" / "scenes" / "scene-versioned.md"
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text("当前目录中的篡改正文", encoding="utf-8")

    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["主角"], keywords=["场景检索词"]),
        source_id="book",
        source_version=snapshot.version,
    )

    scene = evidence["scenes"][0]
    assert scene["content"] == immutable_text
    assert scene["content_source"] == "evidence_store"
    assert scene["document_available"] is True
    assert any(
        item["code"] == "SOURCE_INDEX_NOT_VERSIONED"
        for item in evidence["diagnostics"]
    )

    asked = QueryEngine(temp_workspace).ask(
        "work_a",
        "场景检索词",
        use_mock=True,
        source_id="book",
        source_version=snapshot.version,
    )
    assert asked.evidence["scenes"][0]["content"] == immutable_text


def test_source_scoped_entity_document_is_not_treated_as_versioned(temp_workspace):
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "unversioned-character", name="未版本化角色", category="character"
    )
    doc_path = (
        temp_workspace.projects_dir
        / "work_a"
        / "entities"
        / "characters"
        / "unversioned-character.md"
    )
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("当前项目投影正文", encoding="utf-8")

    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["未版本化角色"], keywords=[]),
        source_id="book",
        source_version="version-a",
    )

    entity = evidence["entities"][0]
    assert entity["bio"] == ""
    assert entity["bio_source"] == "unavailable"
    assert any(
        item["code"] == "PROJECT_DOCUMENT_NOT_SOURCE_VERSIONED"
        for item in evidence["diagnostics"]
    )
