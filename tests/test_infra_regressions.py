"""Focused regression tests for the model gateway and storage infrastructure."""

from pathlib import Path

import pytest
import yaml

from fxi.core.config import FxiConfig
from fxi.core.exceptions import GatewayError
from fxi.model_gateway.cache import LLMCache
from fxi.model_gateway.providers import MockProvider, OpenAICompatibleProvider
from fxi.model_gateway.router import TaskRouter
from fxi.storage.backup import BackupManager
from fxi.storage.rebuild import Rebuilder
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


@pytest.fixture
def infra_workspace(tmp_path: Path):
    root = tmp_path
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
    with client.transaction() as cur:
        ensure_work(cur, "work_rebuild")
    yield config


def _write_models(config: FxiConfig, providers: dict, routes: dict | None = None) -> None:
    config_dir = config.workspace_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "default_provider": "openai_compatible",
                "providers": providers,
                "task_routes": routes or {"chat": {"provider": "default", "model": "default"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_provider_override_uses_configured_adapter_and_rejects_unsupported(
    infra_workspace: FxiConfig,
):
    _write_models(
        infra_workspace,
        {
            "openai_compatible": {
                "type": "openai_compatible",
                "base_url": "https://example.test/v1",
                "api_key_env": "FXI_TEST_KEY",
                "default_model": "test-chat",
            },
            "google_genai": {
                "type": "google_genai",
                "api_key_env": "GEMINI_API_KEY",
                "default_model": "gemini-test",
            },
        },
    )

    router = TaskRouter(infra_workspace)
    provider, model, _ = router.get_route("chat", provider_override="openai_compatible")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://example.test/v1"
    assert model == "test-chat"

    with pytest.raises(GatewayError, match="unsupported"):
        router.get_route("chat", provider_override="google_genai")

    with pytest.raises(GatewayError, match="未配置"):
        router.get_route("chat", provider_override="missing_provider")


def test_unimplemented_provider_capabilities_fail_explicitly():
    provider = MockProvider()
    for capability in ("embed", "vector_search", "rrf", "enqueue_offline", "failover"):
        with pytest.raises(GatewayError, match="unsupported"):
            getattr(provider, capability)("payload")


def test_unimplemented_task_capability_does_not_use_chat_adapter(infra_workspace: FxiConfig):
    _write_models(
        infra_workspace,
        {
            "openai_compatible": {
                "base_url": "https://example.test/v1",
                "api_key_env": "FXI_TEST_KEY",
                "default_model": "test-chat",
            }
        },
        routes={"embedding": {"provider": "default", "model": "default"}},
    )

    with pytest.raises(GatewayError, match="unsupported"):
        TaskRouter(infra_workspace).get_route("embedding")


def test_cache_key_includes_exact_prompt_and_extra_request_parameters():
    base = dict(
        model="test-model",
        task="chat",
        provider="provider-a",
        temperature=0.2,
        schema="schema-a",
        max_tokens=100,
        prompt_version="prompt-v1",
    )
    exact = LLMCache.compute_key("prompt", **base)
    whitespace = LLMCache.compute_key(" prompt", **base)
    timeout = LLMCache.compute_key("prompt", timeout=30.0, **base)
    reasoning = LLMCache.compute_key(
        "prompt", reasoning_effort="low", **base
    )

    assert exact != whitespace
    assert exact != timeout
    assert exact != reasoning


def test_rebuild_deletes_foreign_key_dependents_before_entities(infra_workspace: FxiConfig):
    entity_file = (
        infra_workspace.projects_dir
        / "work_rebuild"
        / "entities"
        / "characters"
        / "char_rebuild.md"
    )
    entity_file.parent.mkdir(parents=True, exist_ok=True)
    entity_file.write_text(
        "---\nentity_id: char_rebuild\ncategory: character\nname: 重建角色\n---\nauthoritative entity file\n",
        encoding="utf-8",
    )
    db = DatabaseClient(infra_workspace.sqlite_path)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO entities
            (entity_id, work_id, category, name, file_path, updated_at)
            VALUES ('char_rebuild', 'work_rebuild', 'character', '重建角色', 'projects/work_rebuild/entities/characters/char_rebuild.md', datetime('now'))
            """
        )
        cur.execute(
            """
            INSERT INTO entity_phases
            (phase_id, work_id, entity_id, phase_name, updated_at)
            VALUES ('phase_1', 'work_rebuild', 'char_rebuild', '初始', datetime('now'))
            """
        )

    rebuilder = Rebuilder(infra_workspace)
    with db.get_connection() as conn:
        tables = rebuilder._list_rebuild_tables(conn.cursor())
        order = rebuilder._deletion_order(conn.cursor(), tables)
        assert order.index("entity_phases") < order.index("entities")

    report = rebuilder.rebuild_all()

    assert report.success
    assert "entity_phases" in report.cleared_tables
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entity_phases").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE work_id = 'work_rebuild' AND entity_id = 'char_rebuild'"
        ).fetchone()[0] == 1


def test_backup_default_paths_do_not_collide_and_existing_target_is_rejected(
    infra_workspace: FxiConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("fxi.storage.backup.time.strftime", lambda *_: "20260906_120000")
    manager = BackupManager(infra_workspace)

    first = manager.create_snapshot()
    second = manager.create_snapshot()
    assert first != second
    assert (first / "manifest.sqlite").is_file()
    assert (second / "manifest.sqlite").is_file()

    existing = infra_workspace.workspace_root / "existing-snapshot"
    existing.mkdir()
    with pytest.raises(FileExistsError, match="已存在"):
        manager.create_snapshot(existing)


def test_backup_missing_database_failure_is_visible(infra_workspace: FxiConfig):
    missing_config = infra_workspace.model_copy(
        update={"sqlite_path": infra_workspace.data_dir / "missing.sqlite"}
    )
    with pytest.raises(FileNotFoundError, match="数据库"):
        BackupManager(missing_config).create_snapshot()
