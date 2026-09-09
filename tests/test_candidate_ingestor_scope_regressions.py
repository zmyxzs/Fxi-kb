from fxi.sources.candidate_ingestor import CandidateIngestor


def test_topic_keywords_are_not_global_defaults():
    assert CandidateIngestor._infer_ability_category("魔法阵", "") == "innate"


def test_category_rules_are_loaded_only_from_current_work(temp_workspace):
    work_dir = temp_workspace.projects_dir / "work_a"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "work.yaml").write_text(
        "work_id: work_a\n"
        "worldview_genre: xianxia\n"
        "ability_category_rules:\n"
        "  xianxia:\n"
        "    talisman:\n"
        "      - 符箓\n",
        encoding="utf-8",
    )

    ingestor = CandidateIngestor(temp_workspace)
    rules, error = ingestor._load_ability_category_rules("work_a")

    assert error is None
    assert CandidateIngestor._infer_ability_category("符箓", "", rules) == "talisman"
    assert CandidateIngestor._infer_ability_category("魔法阵", "", rules) == "innate"
