"""来源对象与证据校验回归测试。"""

import hashlib
from pathlib import Path

import pytest
import yaml

from fxi.core.config import FxiConfig
from fxi.core.exceptions import StorageError, ValidationError
from fxi.sources.evidence_store import EvidenceMismatchError, EvidenceRef, EvidenceStore
from fxi.sources.importer import SourceImporter
from fxi.storage import versioned_store as versioned_store_module
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.versioned_store import (
    DuplicateDocumentError,
    HashMismatchError,
    ImmutableObjectError,
    SourceDocumentInput,
    SourceEncodingError,
    VersionedStore,
)


@pytest.fixture
def isolated_root(tmp_path: Path):
    root = tmp_path / "evidence-regression-runtime"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def isolated_config(isolated_root: Path) -> FxiConfig:
    data_dir = isolated_root / "data"
    config = FxiConfig(
        workspace_root=isolated_root,
        data_dir=data_dir,
        projects_dir=isolated_root / "projects",
        skills_dir=isolated_root / "skills",
        sources_dir=isolated_root / "sources",
        materials_dir=isolated_root / "materials",
        sqlite_path=data_dir / "manifest.sqlite",
        cache_db_path=data_dir / "cache.sqlite",
        jieba_custom_dict_path=data_dir / "project_lexicon.txt",
    )
    config.ensure_directories()
    DatabaseClient(config.sqlite_path).init_db()
    return config


def _document(text: str = "第一章\n正文") -> SourceDocumentInput:
    return SourceDocumentInput(
        document_id="ch001",
        chapter_index=1,
        raw_bytes=text.encode("utf-8"),
        text=text,
        relative_path="chapters/ch001.md",
    )


def test_snapshot_read_is_independent_of_current_source_path(isolated_root: Path):
    current_path = isolated_root / "current.md"
    current_path.write_text("第一章\n原始正文", encoding="utf-8")
    store = VersionedStore(isolated_root / "sources")
    document = SourceDocumentInput.from_file(
        current_path,
        document_id="ch001",
        chapter_index=1,
        relative_path="chapters/ch001.md",
    )

    snapshot = store.create_snapshot("book", [document], version="v1")
    current_path.write_text("第一章\n后来篡改", encoding="utf-8")
    current_path.unlink()

    assert store.read_document("book", snapshot.version, "ch001").content == "第一章\n原始正文"


def test_manifest_hash_is_verified_and_metadata_is_persisted(isolated_root: Path):
    store = VersionedStore(isolated_root / "sources")
    snapshot = store.create_snapshot("book", [_document()], version="v1", metadata={"title": "原著"})
    assert snapshot.metadata["title"] == "原著"
    assert store.read_snapshot("book", "v1").metadata["title"] == "原著"

    manifest_path = isolated_root / "sources" / "objects" / "book" / "v1" / "snapshot.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["manifest_hash"] = "0" * 64
    manifest_path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(HashMismatchError):
        store.read_snapshot("book", "v1")


def test_document_content_hash_is_verified(isolated_root: Path):
    store = VersionedStore(isolated_root / "sources")
    store.create_snapshot("book", [_document()], version="v1")
    normalized_path = isolated_root / "sources" / "objects" / "book" / "v1" / "ch001" / "normalized.txt"
    normalized_path.write_text("篡改正文", encoding="utf-8")

    with pytest.raises(HashMismatchError):
        store.read_document("book", "v1", "ch001")


def test_raw_bytes_and_declared_text_must_match(isolated_root: Path):
    store = VersionedStore(isolated_root / "sources")
    document = SourceDocumentInput(
        document_id="ch001",
        chapter_index=1,
        raw_bytes=b"actual",
        text="declared",
        relative_path="chapters/ch001.md",
    )

    with pytest.raises(SourceEncodingError):
        store.create_snapshot("book", [document], version="v1")
    assert not (isolated_root / "sources" / "objects" / "book" / "v1").exists()


def test_partial_snapshot_write_is_removed(isolated_root: Path, monkeypatch):
    store = VersionedStore(isolated_root / "sources")
    original_atomic_write = versioned_store_module._atomic_write

    def fail_manifest(path: Path, data: bytes) -> None:
        if path.name == "snapshot.yaml":
            raise StorageError("测试用清单写入失败")
        original_atomic_write(path, data)

    monkeypatch.setattr(versioned_store_module, "_atomic_write", fail_manifest)
    with pytest.raises(StorageError, match="清单写入失败"):
        store.create_snapshot("book", [_document()], version="v1")
    assert not (isolated_root / "sources" / "objects" / "book" / "v1").exists()


def test_same_version_cannot_be_overwritten(isolated_root: Path):
    store = VersionedStore(isolated_root / "sources")
    store.create_snapshot("book", [_document("第一章\n正文 A")], version="v1")

    with pytest.raises(ImmutableObjectError):
        store.create_snapshot("book", [_document("第一章\n正文 B")], version="v1")
    assert store.read_document("book", "v1", "ch001").content == "第一章\n正文 A"


def test_evidence_ref_validates_identity_coordinates_and_excerpt_hash(isolated_root: Path):
    store = EvidenceStore(isolated_root / "sources")
    snapshot = store.create_snapshot("book", [_document("第一章\nabcdef")], version="v1")
    document = store.read_document("book", snapshot.version, "ch001", start_char=4, end_char=7)
    ref = EvidenceRef(
        source_id="book",
        source_version="v1",
        document_id="ch001",
        start_char=4,
        end_char=7,
        excerpt_hash=document.excerpt_hash,
        quote="abc",
    )

    validated = store.validate_ref(ref, expected_text="abc")
    assert validated.text == "abc"
    assert validated.document_hash == hashlib.sha256("第一章\nabcdef".encode()).hexdigest()

    invalid_refs = [
        EvidenceRef("book", "wrong-version", "ch001", 4, 7, document.excerpt_hash),
        EvidenceRef("book", "v1", "wrong-document", 4, 7, document.excerpt_hash),
        EvidenceRef("book", "v1", "ch001", 4, 99, document.excerpt_hash),
        EvidenceRef("book", "v1", "ch001", 4, 7, "0" * 64),
    ]
    for invalid_ref in invalid_refs:
        with pytest.raises(EvidenceMismatchError):
            store.validate_ref(invalid_ref)

    with pytest.raises(EvidenceMismatchError):
        store.validate_ref(ref, expected_text="wrong")


def test_import_rejects_invalid_encoding_without_false_success(isolated_config: FxiConfig):
    input_path = isolated_config.workspace_root / "invalid.txt"
    input_path.write_bytes(b"\xff")
    importer = SourceImporter(isolated_config)

    with pytest.raises(SourceEncodingError):
        importer.import_file(input_path, "book", "非法编码")
    assert not (isolated_config.sources_dir / "book" / "source.yaml").exists()
    assert not (isolated_config.sources_dir / "objects" / "book").exists()


def test_import_rejects_duplicate_chapters_without_false_success(isolated_config: FxiConfig):
    chapters_dir = isolated_config.workspace_root / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch001.md").write_text("第一章\nA", encoding="utf-8")
    (chapters_dir / "chapter1.txt").write_text("第一章\nB", encoding="utf-8")
    importer = SourceImporter(isolated_config)

    with pytest.raises(DuplicateDocumentError):
        importer.import_chapters_dir(chapters_dir, "book", "重复章节")
    assert not (isolated_config.sources_dir / "book" / "source.yaml").exists()
    assert not (isolated_config.sources_dir / "objects" / "book").exists()


def test_import_rejects_invalid_max_chapters_without_false_success(isolated_config: FxiConfig):
    chapters_dir = isolated_config.workspace_root / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch001.md").write_text("第一章\nA", encoding="utf-8")
    importer = SourceImporter(isolated_config)

    with pytest.raises(ValidationError):
        importer.import_chapters_dir(chapters_dir, "book", "非法参数", max_chapters="one")
    assert not (isolated_config.sources_dir / "book" / "source.yaml").exists()
    assert not (isolated_config.sources_dir / "objects" / "book").exists()


def test_imported_snapshot_survives_current_chapter_change(isolated_config: FxiConfig):
    chapters_dir = isolated_config.workspace_root / "chapters"
    chapters_dir.mkdir()
    chapter_path = chapters_dir / "ch001.md"
    chapter_path.write_text("第一章\n原始正文", encoding="utf-8")
    importer = SourceImporter(isolated_config)

    manifest = importer.import_chapters_dir(chapters_dir, "book", "可回读来源")
    chapter_path.write_text("第一章\n后来篡改", encoding="utf-8")
    historical = EvidenceStore(isolated_config.sources_dir).read_document(
        "book", manifest.version or "", "ch001"
    )

    assert historical.content == "第一章\n原始正文"
