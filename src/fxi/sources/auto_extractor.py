"""
fxi.sources.auto_extractor - 通用无硬编码的小说要素与因果大纲智能抽取器
"""

import json
from pathlib import Path
from typing import Any, Optional
from json_repair import repair_json

from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import EntityManager
from fxi.domain.phases import PhaseManager
from fxi.model_gateway.gateway import ModelGateway
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.timeline.dag import CausalDAG


# 通用实体与时序性格相态提取 Prompt 模板 (纯元结构，无任何特定作品硬编码)
PROMPT_EXTRACT_ENTITIES = """你是一个通用小说设定解析与世界观建模专家。
请仔细阅读以下小说章节文本切片（来自作品《{title}》），分析并提取核心实体（人物、法宝/道具、势力组织）及其时序性格相态演变（Phases）。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. 角色提取（characters）：
   - 提取主角、关键引路人/导师、核心配角或重要反派；
   - 给出全局唯一 entity_id（格式如 char_xxx，小写英文或拼音）；
   - 提炼角色在该文本跨度内的性格阶段相态（phases）：若角色心境、身份、超凡状态随剧情推进发生了阶段性演变，划分其起止章序号（valid_from 到 valid_to），列出性格特征（traits）与该阶段绝不会做出的OOC禁行行为（anti_behaviors）。
2. 道具与超凡设定（items）：
   - 提取具有独特超凡功能、象征意义或重要剧情推动作用的物品或法宝（如特殊武器、封印物、随身信物）。

请严格输出以下 JSON 格式，绝不要包含任何 Markdown 格式以外的废话：
{{
  "characters": [
    {{
      "entity_id": "char_<标识>",
      "name": "<角色姓名>",
      "category": "character",
      "aliases": ["<别称1>", "<别称2>"],
      "attributes": {{
        "identity": "<身份背景>",
        "realm": "<实力/境界>",
        "role": "<主角/引路人/配角/反派>"
      }},
      "description": "<生平定位与核心性格>",
      "phases": [
        {{
          "phase_id": "phase_<阶段标识>",
          "phase_name": "<阶段名称>",
          "valid_from": 1,
          "valid_to": 10,
          "traits": ["<特征标签1>", "<特征标签2>"],
          "anti_behaviors": ["<此阶段绝对不会做出的破人设行为>"]
        }}
      ]
    }}
  ],
  "items": [
    {{
      "entity_id": "item_<标识>",
      "name": "<物品名称>",
      "category": "item",
      "attributes": {{
        "function": "<核心功效>"
      }},
      "description": "<外观与用途描述>"
    }}
  ]
}}
"""

# 通用因果 DAG 与同人分歧点 (POD) 提取 Prompt 模板 (零硬编码)
PROMPT_EXTRACT_CAUSAL_EVENTS = """你是一个小说因果拓扑与同人创作分歧点（POD, Point of Divergence）分析专家。
请根据以下小说章节文本切片（来自作品《{title}》），梳理出主线剧情的时空因果事件链（Causal DAG），并精准识别适合作为后续同人文创作的“命运改变/蝴蝶效应分歧点”（POD）。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. 因果事件（causal_events）：提取 5~8 个推动主线发展的关键转折事件，narrative_order 按章节序号单调递增。
2. 同人分歧点候选（is_pod_candidate）：
   - 评估该事件是否属于重大悲剧、关键抉择、命运转折点（如重要配角濒死战死、关键法宝被毁被夺、主角人生路线分歧）；
   - 若极适合同人作者在此处做出“剧情改写/挽救遗憾/反转命运”，标记 is_pod_candidate 为 true，并给出 pod_analysis（说明同人改写此事件将引发的连锁蝴蝶效应）。

请严格输出以下 JSON 格式：
{{
  "causal_events": [
    {{
      "event_id": "ev_{work_id}_<章号序号>",
      "narrative_order": 1,
      "physical_time": "<故事内时间或粗略时间段>",
      "summary": "<事件客观事实摘要（30字以内）>",
      "is_pod_candidate": false,
      "pod_analysis": "<如果是同人分歧点，阐明同人改变此事件带来的波及价值，否则为空字符串>"
    }}
  ]
}}
"""


class UniversalAutoExtractor:
    """全题材通用、无硬编码的小说要素与因果智能抽取器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.gw = ModelGateway(self.config)
        self.ent_mgr = EntityManager(self.config)
        self.phase_mgr = PhaseManager(self.config)
        self.dag = CausalDAG(self.config)

    def extract_and_ingest(
        self,
        work_id: str,
        sample_chapters: int = 20
    ) -> dict[str, Any]:
        """
        全自动流水线：
        1. 自动从 sources/<work_id>/ 读取已导入章节；
        2. 调用大模型提取实体、性格相态与道具；
        3. 调用大模型提取因果事件链与同人分歧点 (POD)；
        4. 自动落盘到 projects/<work_id>/ 与 SQLite 中。
        """
        # 1. 查找底本章节或场景
        chapters_dir = self.config.sources_dir / work_id / "chapters"
        scenes_dir = self.config.sources_dir / work_id / "scenes"

        # 获取作品标题
        client = DatabaseClient(self.config.sqlite_path)
        with client.get_connection() as conn:
            cur = conn.execute("SELECT title FROM works WHERE work_id = ?", (work_id,))
            row = cur.fetchone()
            title = row["title"] if row else work_id

        chapters_text = []
        if chapters_dir.is_dir():
            files = sorted(
                [f for f in chapters_dir.iterdir() if f.is_file() and f.suffix.lower() in (".md", ".txt")],
                key=lambda p: int("".join(filter(str.isdigit, p.stem)) or "9999")
            )
            for ch_file in files[:sample_chapters]:
                text = ch_file.read_text(encoding="utf-8")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                ch_title = lines[0] if lines else ch_file.stem
                core_snippet = "\n".join(lines[:8] + lines[-6:])
                chapters_text.append(f"【{ch_title}】\n{core_snippet}")
        elif scenes_dir.is_dir():
            s_files = sorted(list(scenes_dir.glob("*.md")))[:sample_chapters * 2]
            for sf in s_files:
                chapters_text.append(sf.read_text(encoding="utf-8")[:500])

        if not chapters_text:
            raise RuntimeError(f"作品 [{work_id}] 尚未导入章节或场景文本，请先执行 kb import 导入底本！")

        summary_blob = "\n\n".join(chapters_text)

        # 2. 步骤一：提取实体与相态
        entity_prompt = PROMPT_EXTRACT_ENTITIES.format(title=title, chapters_summary=summary_blob)
        raw_entity_res = self.gw.complete(task_type="fast_extraction", prompt=entity_prompt)
        entity_data = repair_json(raw_entity_res, return_objects=True)
        if not isinstance(entity_data, dict):
            entity_data = {"characters": [], "items": []}

        saved_characters = []
        for char in entity_data.get("characters", []):
            cid = char.get("entity_id", f"char_{len(saved_characters)+1}")
            cname = char.get("name", cid)
            self.ent_mgr.upsert_entity(
                work_id=work_id,
                entity_id=cid,
                name=cname,
                category="character",
                is_unique=True,
                aliases=char.get("aliases", []),
                attributes=char.get("attributes", {}),
                description=char.get("description", "")
            )
            phases_added = []
            for ph in char.get("phases", []):
                pid = ph.get("phase_id", f"phase_{len(phases_added)+1}")
                pname = ph.get("phase_name", "默认阶段")
                self.phase_mgr.add_phase(
                    work_id=work_id,
                    entity_id=cid,
                    phase_id=pid,
                    phase_name=pname,
                    valid_from_order=ph.get("valid_from", 0),
                    valid_to_order=ph.get("valid_to"),
                    traits=ph.get("traits", []),
                    anti_behaviors=ph.get("anti_behaviors", [])
                )
                phases_added.append(pname)
            saved_characters.append({"id": cid, "name": cname, "phases": phases_added})

        saved_items = []
        for it in entity_data.get("items", []):
            iid = it.get("entity_id", f"item_{len(saved_items)+1}")
            iname = it.get("name", iid)
            self.ent_mgr.upsert_entity(
                work_id=work_id,
                entity_id=iid,
                name=iname,
                category="item",
                is_unique=True,
                aliases=[],
                attributes=it.get("attributes", {}),
                description=it.get("description", "")
            )
            saved_items.append({"id": iid, "name": iname})

        # 3. 步骤二：提取因果 DAG 与同人分歧点
        causal_prompt = PROMPT_EXTRACT_CAUSAL_EVENTS.format(
            title=title,
            work_id=work_id,
            chapters_summary=summary_blob
        )
        raw_causal_res = self.gw.complete(task_type="fast_extraction", prompt=causal_prompt)
        causal_data = repair_json(raw_causal_res, return_objects=True)
        if not isinstance(causal_data, dict):
            causal_data = {"causal_events": []}

        saved_events = []
        prev_ev_id = None
        for ev in causal_data.get("causal_events", []):
            eid = ev.get("event_id", f"ev_{work_id}_{len(saved_events)+1}")
            order = int(ev.get("narrative_order", len(saved_events)+1))
            summary = ev.get("summary", "")
            time_str = ev.get("physical_time", "未知")
            is_pod = bool(ev.get("is_pod_candidate", False))
            pod_analysis = ev.get("pod_analysis", "")

            self.dag.register_event(
                event_id=eid,
                work_id=work_id,
                scene_uuid=f"sc_{eid}",
                narrative_order=order,
                physical_time=time_str,
                summary=summary,
                is_canon=True,
                status="untouched"
            )
            if prev_ev_id:
                self.dag.add_causal_link(work_id, prev_ev_id, eid, link_type="direct_cause")
            prev_ev_id = eid

            saved_events.append({
                "event_id": eid,
                "order": order,
                "summary": summary,
                "is_pod": is_pod,
                "pod_analysis": pod_analysis
            })

        return {
            "work_id": work_id,
            "title": title,
            "characters": saved_characters,
            "items": saved_items,
            "causal_events": saved_events,
        }
