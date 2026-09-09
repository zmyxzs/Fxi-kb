"""
fxi.sources.auto_extractor - 通用无硬编码的小说要素、角色声线、人际张力与文风连续性智能抽取器
"""

import json
from pathlib import Path
from typing import Any, Mapping, Optional
from json_repair import repair_json
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_work_id
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.model_gateway.gateway import ModelGateway
from fxi.storage.sqlite_client import DatabaseClient


class ExtractionError(RuntimeError):
    """抽取失败的统一、可诊断异常；调用方仍可按 RuntimeError 兼容处理。"""

    code = "EXTRACTION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        label: str,
        retryable: bool = False,
        cause_type: Optional[str] = None,
    ):
        self.label = label
        self.retryable = retryable
        self.cause_type = cause_type
        super().__init__(message)

    def to_diagnostic(self, **details: Any) -> dict[str, Any]:
        result = {
            "code": self.code,
            "message": str(self),
            "label": self.label,
            "retryable": self.retryable,
        }
        if self.cause_type:
            result["cause_type"] = self.cause_type
        result.update({key: value for key, value in details.items() if value is not None})
        return result


class ExtractionSchemaError(ExtractionError):
    """模型输出不满足候选抽取契约。"""

    code = "INCOMPLETE_INPUT"

    def __init__(self, message: str, *, label: str):
        super().__init__(message, label=label, retryable=False)


class ExtractionExecutionError(ExtractionError):
    """模型调用或抽取执行阶段失败，保留阶段信息但不泄露模型响应。"""

    code = "MODEL_EXTRACTION_FAILED"

    def __init__(
        self,
        message: str,
        *,
        label: str,
        retryable: bool = True,
        cause_type: Optional[str] = None,
    ):
        super().__init__(
            message,
            label=label,
            retryable=retryable,
            cause_type=cause_type,
        )


class ExtractionInputError(ExtractionError):
    """来源文件读取失败，不能伪造为空文本。"""

    code = "SOURCE_READ_FAILED"

    def __init__(self, message: str, *, label: str, cause_type: Optional[str] = None):
        super().__init__(
            message,
            label=label,
            retryable=False,
            cause_type=cause_type,
        )


def _safe_exception_detail(exc: Exception) -> str:
    """只保留受控的内建异常短消息，避免把模型响应带入诊断。"""
    if type(exc).__module__ != "builtins":
        return type(exc).__name__
    detail = " ".join(str(exc).split())
    return detail[:160] if detail else type(exc).__name__


def _call_model(
    gateway: Any,
    label: str,
    task_type: str,
    prompt: str,
    **kwargs: Any,
) -> Any:
    """调用既有网关一次；失败带阶段信息向上抛出，不在抽取器内重试。"""
    try:
        return gateway.complete(task_type, prompt, **kwargs)
    except ExtractionError:
        raise
    except Exception as exc:
        detail = _safe_exception_detail(exc)
        raise ExtractionExecutionError(
            f"模型抽取 {label} 调用失败: {detail}",
            label=label,
            cause_type=type(exc).__name__,
        ) from exc


def _read_source_text(path: Path, label: str) -> str:
    """读取来源文本；编码或文件错误必须带文件阶段向上报告。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExtractionInputError(
            f"抽取阶段 {label} 读取 {path.name} 失败: {_safe_exception_detail(exc)}",
            label=label,
            cause_type=type(exc).__name__,
        ) from exc


# 通用实体与时序性格相态提取 Prompt 模板 (纯元结构，无任何特定作品硬编码)
PROMPT_EXTRACT_ENTITIES = """你是一个通用小说设定解析与世界观建模专家。
请仔细阅读以下小说章节文本切片（来自作品《{title}》），分析并提取核心实体（人物、物品、势力组织）及其时序性格相态演变（Phases）。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. 角色提取（characters）：
   - 提取叙事核心角色、关键引路人/导师、核心配角或重要对手；
   - 给出当前作品内稳定且唯一的 entity_id（使用项目约定的安全标识格式）；
   - 提炼角色在该文本跨度内的性格阶段相态（phases）：若角色心境、身份、超凡状态随剧情推进发生了阶段性演变，划分其起止章序号（valid_from 到 valid_to），列出性格特征（traits）与该阶段绝不会做出的OOC禁行行为（anti_behaviors）；
   - 提取角色展现的核心能力或特殊机制（abilities）。
2. 道具与超凡设定（items）：
   - 提取具有独特功能、象征意义或重要剧情推动作用的物品（如特殊工具、封印物、随身信物、身份凭证）；
   - 提取物品的当前持有者（current_owner）、来源背景（source_origin）与移交方式（transfer_type: gifted|looted|purchased|created）；
   - 提取物品的核心铭文誓词/图腾标语（symbolic_text）：如徽章刻印的誓言、信物背后的铭文或标志性箴言；若无则留空；
   - 提取该道具的标志性交互仪式动作（interaction_rituals）：如指腹摩挲刻痕、注入精神力、端枪姿势等 1~2 个具象物理交互动作。

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
        "role": "<叙事核心角色/引路人/配角/对手>"
      }},
      "abilities": [
        {{
          "skill_id": "skill_<标识>",
          "name": "<能力或特殊机制名称>",
          "tier": 1,
          "description": "<效果与代价>"
        }}
      ],
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
      "current_owner": "<当前持有者角色姓名，未知则为空字符串>",
      "source_origin": "<物品来源或出处，如神明馈赠、击杀战利品>",
      "transfer_type": "gifted",
      "attributes": {{
        "function": "<核心功效>",
        "symbolic_text": "<铭文誓词/图腾标语，若无留空>",
        "interaction_rituals": ["<交互动作1>", "<交互动作2>"]
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
   - 评估该事件是否属于重大悲剧、关键抉择、命运转折点（如重要角色濒死、关键物品被毁被夺、叙事路线分歧）；
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

# 角色声线、台词口吻、微动作与禁忌提取 Prompt 模板
PROMPT_EXTRACT_VOICE_PROFILES = """你是一个小说角色台词、声线与微表情微动作解析专家。
请仔细分析以下小说章节文本切片（来自作品《{title}》），针对文中出现的核心角色提取其独特的【声线定位、台词口吻特征、典型口癖/典型对白短句、标志性肢体微动作与OOC绝对禁忌】。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. 重点分析文本切片中主要登场角色的对白与肢体动作（叙事核心角色、关键导师/引路人、重要同伴、亲属或对手）；
2. 提炼其语言特征：
   - tone: 性格与声线定位（如通透冷静自嘲的少年、粗粝沧桑护短的长辈、热血话痨的同伴等）；
   - speech_style: 台词句式与口吻风格（言简意赅的大白话、擅长用接地气的生活语言解构沉重、拒绝假大空演讲）；
   - catchphrases: 2~4 句极具辨识度的典型台词或句式习惯；
   - gestures: 2~4 个标志性肢体微动作与生理应激小动作（如插兜、靠栏杆、摸下巴、特定手势等）；
   - taboos: 2~3 个绝对不能出现的破人设行为（OOC）（如该角色绝不会做出的反常行为或绝不会说出的油腻台词）；
   - dialogue_samples: 提取 2~3 组原著中该角色最具神采的真实对白交锋短样板（含交锋情境 context、他人说的话 user、该角色真实回敬的台词 reply）。

请严格输出以下 JSON 格式：
{{
  "voice_profiles": [
    {{
      "name": "<角色姓名>",
      "tone": "<声线与性格定位>",
      "speech_style": "<说话句式与口吻>",
      "catchphrases": ["<典型台词1>", "<典型台词2>"],
      "gestures": ["<微动作1>", "<微动作2>"],
      "taboos": ["<禁忌1>", "<禁忌2>"],
      "dialogue_samples": [
        {{
          "context": "<交锋情境>",
          "user": "<他人说的话>",
          "reply": "<该角色的真实回敬台词>"
        }}
      ]
    }}
  ]
}}
"""

# 人物关系、心理张力与认知差提取 Prompt 模板
PROMPT_EXTRACT_RELATIONSHIPS = """你是一个小说人物关系网与深层戏剧张力分析专家。
请仔细阅读以下小说章节文本切片（来自作品《{title}》），分析核心出场角色之间的人际关系、深层心理张力、共同秘密与互动态度。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. 提取具有戏剧张力、情感羁绊或共同经历的核心角色对（pair: [角色A, 角色B]）；
2. dynamic: 关系定位（例如同伴、亲属、师生、上下级、竞争者或共同经历者）；
3. tension: 两人之间的深层心理张力、未挑明的矛盾或互相亏欠；
4. shared_secret: 两人共同知晓或涉事的秘密；
5. interpersonal_attitude: 角色A对角色B的态度，以及角色B对角色A的态度。

请严格输出以下 JSON 格式：
{{
  "relationships": [
    {{
      "pair": ["<角色A>", "<角色B>"],
      "dynamic": "<关系定位>",
      "tension": "<心理张力>",
      "shared_secret": "<共同秘密>",
      "interpersonal_attitude": {{
        "<角色A>": "<角色A对待角色B的态度>",
        "<角色B>": "<角色B对待角色A的态度>"
      }}
    }}
  ]
}}
"""

# 文笔风格、具象比喻与负向禁令提取 Prompt 模板
PROMPT_EXTRACT_STYLE = """你是一个小说文笔风格、行文节奏、修辞特征与深层情境语境解构专家。
请仔细分析以下小说章节文本切片（来自作品《{title}》），提炼原著作者的文笔风格、遣词造句习惯、比喻偏好、标点节奏、负向禁写清单（Anti-patterns）、视点认知边界（是否打破第四面墙）、三维词汇治理矩阵、情境重力守卫、详略剪辑律与字数配额模型，并直接提取原汁原味的大师正文黄金范例（positive_exemplars）。

【小说文本内容】：
{chapters_summary}

【提取规则】：
1. narrative_cadence: 段落长度偏好（如：20~60字超短段落，切分利落，漫画分镜感），节奏特点（极简白描，重动词轻修饰，对白与短促动作推进）；
2. metaphors:
   - preferred_style: 从输入文本提炼具象、抽象或其他比喻偏好；
   - examples: 提炼 2~4 个输入文本中真实展现的代表性比喻句；
   - banned_abstract_metaphors: 仅记录输入文本或作品配置明确禁止的表达；
3. punctuation_and_dialogue: 破折号停顿、自然省略号、快速对白、台词单次高密度吐露与表里双轨；
4. epistemic_worldview:
   - fourth_wall_policy: 从作品配置或输入文本判断 "strict_in_universe" 或 "meta_aware"；
   - worldview_type: 从作品配置或输入文本提炼，不使用题材默认值；
   - protagonist_identity: 从作品配置或输入文本提炼，不使用身份默认值；
5. lexicon_governance:
   - worldview_genre: 从作品级配置或输入文本中提取，不得套用其他作品的题材默认值
   - universal_ai_cliches_banned: 仅记录项目全局规则或作品配置明确列出的机器套话
   - genre_anachronisms_banned: 仅记录作品配置或输入文本明确体现的跨界错位词
   - permitted_monologue_slang: 从作品配置或输入文本提炼允许的独白用语边界
   - strict_dialogue_taboos: 从作品配置或输入文本提炼对白禁用表达
6. tonal_gravity_rules:
   - 从作品配置或输入文本提炼情感重场景中的幽默功能与边界；
7. detail_vs_ellipsis_cadence:
   - detail_focus: 从作品配置或输入文本提炼详写领域；
   - ellipsis_focus: 从作品配置或输入文本提炼略写领域；
   - cliffhanger_cutoff: 从作品配置或输入文本提炼终点截断习惯；
8. scene_budget_model:
   - average_chapter_words: 根据输入文本估算单章平均字数；
   - setup_words: 根据输入文本估算；
   - pressure_words: 根据输入文本估算；
   - hook_words: 根据输入文本估算；
9. negative_anti_patterns: 列出 3~5 条写该作品时严禁出现的网络油腻烂梗、翻译腔、AI味废话、傲慢龙王式装逼台词；
10. positive_exemplars: 从提供的原文中，按实际场景类型直接摘录 2~4 段代表性正文（每段 150~300 字，必须完全摘自输入文本，禁止自由编造）。

请严格输出以下 JSON 格式：
{{
  "narrative_cadence": {{
    "paragraph_length": "<段落字数与分镜习惯>",
    "pacing": "<叙事节奏特征>"
  }},
  "metaphors": {{
    "preferred_style": "<具象比喻偏好>",
    "examples": ["<真实比喻范例1>", "<真实比喻范例2>"],
    "banned_abstract_metaphors": ["<抽象比喻词1>", "<抽象比喻词2>"]
  }},
  "punctuation_and_dialogue": {{
    "habits": ["<对话与标点特征1>", "<对话与标点特征2>"]
  }},
  "epistemic_worldview": {{
    "fourth_wall_policy": "<strict_in_universe 或 meta_aware>",
    "worldview_type": "<现代本土都市 或 穿越架空等>",
    "protagonist_identity": "<modern_native 或 transmigrator 或 ancient_native>",
    "prohibited_meta_words": ["<从作品配置或输入文本提取>"]
  }},
  "lexicon_governance": {{
    "worldview_genre": "<来自作品级配置或输入文本>",
    "universal_ai_cliches_banned": ["<从项目全局规则或作品配置提取>"],
    "genre_anachronisms_banned": ["<从作品配置或输入文本提取>"],
    "permitted_monologue_slang": ["<从作品配置或输入文本提取>"],
    "strict_dialogue_taboos": ["<从作品配置或输入文本提取>"]
  }},
  "tonal_gravity_rules": {{
    "humor_function": "<从作品配置或输入文本提取>",
    "emotional_protection": "<从作品配置或输入文本提取>"
  }},
  "detail_vs_ellipsis_cadence": {{
    "detail_focus": ["<从作品配置或输入文本提取>"],
    "ellipsis_focus": ["<从作品配置或输入文本提取>"],
    "cliffhanger_cutoff": "<从作品配置或输入文本提取>"
  }},
  "scene_budget_model": {{
    "average_chapter_words": null,
    "setup_words": [],
    "pressure_words": [],
    "hook_words": []
  }},
  "negative_anti_patterns": [
    "<负向行文禁令1>",
    "<负向行文禁令2>"
  ],
  "positive_exemplars": [
    {{
      "id": "<范例ID>",
      "scene_type": "<按输入文本归类的场景类型>",
      "tags": ["<标签1>", "<标签2>"],
      "description": "<该段写作特色说明>",
      "text": "<原著一字不差的真实段落摘录>"
    }}
  ]
}}
"""

# 章节尾声连续性与悬念钩子提取 Prompt 模板
PROMPT_EXTRACT_CHAPTER_CONTINUITY = """你是一个小说章节连续性与未决伏笔分析专家。
请阅读作品《{title}》第 {chapter_index} 章《{chapter_title}》的结尾文本，提炼出该章结束时的精确物理现场快照与未决动作钩子（Hooks）。

【结尾文本】：
{ending_snippet}

请严格输出以下 JSON 格式：
{{
  "ending_location": "<章末角色所处的具体物理地点>",
  "active_characters": ["<在场角色1>", "<在场角色2>"],
  "ending_situation": "<章末瞬间的微观情境态势（80字以内）>",
  "unresolved_hooks": [
    "<下一章必须立刻接续的未决动作或承诺1>",
    "<未决悬念或待解答线索2>"
  ]
}}
"""


class UniversalAutoExtractor:
    """全题材通用、无硬编码的小说要素、声线、张力与文风智能抽取器"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        gateway: Optional[ModelGateway] = None,
    ):
        self.config = config or load_config()
        self.gw = gateway or ModelGateway(self.config)
        self.candidate_store = CandidateStore(self.config)

    @staticmethod
    def _parse_model_result(raw: Any, label: str, required_key: str) -> dict[str, Any]:
        """解析模型结果；结构缺失必须失败，不能静默变成空知识。"""
        try:
            value = raw if isinstance(raw, Mapping) else repair_json(raw, return_objects=True)
        except (TypeError, ValueError) as exc:
            raise ExtractionSchemaError(
                f"模型抽取 {label} 结果无法解析: {type(exc).__name__}",
                label=label,
            ) from exc
        if not isinstance(value, Mapping):
            raise ExtractionSchemaError(
                f"模型抽取 {label} 结果必须是对象",
                label=label,
            )
        if required_key not in value:
            raise ExtractionSchemaError(
                f"模型抽取 {label} 结果缺少必需字段 {required_key}",
                label=label,
            )
        return dict(value)

    @staticmethod
    def _validated_list(
        data: Mapping[str, Any],
        label: str,
        key: str,
        *,
        optional: bool = False,
    ) -> list[dict[str, Any]]:
        if key not in data and optional:
            return []
        value = data.get(key)
        if not isinstance(value, list):
            raise ExtractionSchemaError(
                f"模型抽取 {label} 的 {key} 必须是列表",
                label=label,
            )
        result: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise ExtractionSchemaError(
                    f"模型抽取 {label} 的 {key}[{index}] 必须是对象",
                    label=label,
                )
            result.append(dict(item))
        return result

    @staticmethod
    def _validate_work_id(work_id: str) -> str:
        try:
            return validate_work_id(work_id)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    def _work_scope_status(self, work_id: str) -> dict[str, str]:
        """仅检查指定作品的规则入口，绝不从其他作品继承配置。"""
        work_id = self._validate_work_id(work_id)
        path = self.config.projects_dir / work_id / "work.yaml"
        if not path.is_file():
            return {
                "status": "UNAVAILABLE",
                "message": f"作品 [{work_id}] 没有 work.yaml；候选不得注入默认作品规则。",
            }
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return {
                "status": "INCOMPLETE",
                "message": f"作品 [{work_id}] 的 work.yaml 不可读取: {type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "status": "INCOMPLETE",
                "message": f"作品 [{work_id}] 的 work.yaml 必须是对象。",
            }
        return {"status": "AVAILABLE", "message": "已加载指定作品的 work.yaml。"}

    @staticmethod
    def _candidate_slug(work_id: str) -> str:
        digest = sha256_hex(work_id)[:16]
        return f"fxi-extraction-{digest}"

    @staticmethod
    def _build_text_corpora(raw_files: list[Path], sample_chapters: int = 20) -> tuple[str, str, str]:
        """
        构建多尺度文本语料：
        1. summary_blob: 章节大纲与实体、因果链识别（标题 + 开头5行 + 中段关键事件 + 结尾5行）
        2. dialogue_blob: 纯真实对白与互动语料（抽取包含引述对话及说话人微动作的行），供声线与人际张力分析
        3. style_corpus: 完整保留代表性章节切片，供文风与正向黄金范例抽取
        """
        summary_sections = []
        dialogue_sections = []
        style_sections = []

        # 只按输入样本位置选择代表章节，不绑定任何作品的章节号或剧情。
        key_indices = set(range(1, min(sample_chapters, 6) + 1))

        for idx, ch_file in enumerate(raw_files[:sample_chapters], start=1):
            digits = "".join(filter(str.isdigit, ch_file.stem))
            ch_num = int(digits) if digits else idx
            text = _read_source_text(ch_file, f"chapter[{ch_num}]")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            ch_title = lines[0] if lines else ch_file.stem

            # 1. 结构化大纲切片
            if len(lines) <= 18:
                ch_summary_lines = lines
            else:
                mid_start = len(lines) // 2 - 3
                mid_end = mid_start + 6
                ch_summary_lines = lines[:5] + ["..."] + lines[mid_start:mid_end] + ["..."] + lines[-5:]
            summary_sections.append(f"【{ch_title}】\n" + "\n".join(ch_summary_lines))

            # 2. 对白切片：抓取包含引号的台词及其紧邻动作行
            dialogue_lines = []
            for i, line in enumerate(lines):
                if any(q in line for q in ("“", "”", "「", "」", '"')):
                    if i > 0 and lines[i - 1] not in dialogue_lines and not any(q in lines[i - 1] for q in ("“", "”")):
                        dialogue_lines.append(lines[i - 1])
                    if line not in dialogue_lines:
                        dialogue_lines.append(line)
                    if i + 1 < len(lines) and not any(q in lines[i + 1] for q in ("“", "”")):
                        dialogue_lines.append(lines[i + 1])
            if dialogue_lines:
                dialogue_sections.append(f"【{ch_title} - 对白与互动切片】\n" + "\n".join(dialogue_lines[:30]))

            # 3. 典型章节完整切片 (2500字左右)，供文风与范例抽取
            if ch_num in key_indices or idx in (1, 5, min(sample_chapters, 20)):
                clean_text = "\n".join(lines[1:])
                style_sections.append(f"【{ch_title} 原著真实正文】\n{clean_text[:2500]}")

        summary_blob = "\n\n".join(summary_sections)
        dialogue_blob = "\n\n".join(dialogue_sections) if dialogue_sections else summary_blob
        style_corpus = "\n\n".join(style_sections) if style_sections else summary_blob
        return summary_blob, dialogue_blob, style_corpus

    def extract_and_ingest(
        self,
        work_id: str,
        sample_chapters: int = 20,
        *,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        input_hash: Optional[str] = None,
        evidence_refs: Optional[list[Mapping[str, Any]]] = None,
        evaluation_ref: Optional[str] = None,
        submitted_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        全自动流水线：
        1. 自动从 sources/<work_id>/ 读取已导入章节；
        2. 调用大模型提取实体、性格相态与道具；
        3. 调用大模型提取因果事件链与同人分歧点 (POD)；
        4. 调用大模型提取角色真实声线 (Voice Profiles)；
        5. 调用大模型提取人际关系与深层张力 (Relationships & Tensions)；
        6. 调用大模型提炼作者文笔风格档案 (Style Profile) 与正向大师范例 (Positive Exemplars)；
        7. 自动提炼章节连续性台账 (Continuity Ledger)；
        8. 将结构化结果提交到作品隔离的 candidate store，等待审核后再进入正式知识。
        """
        work_id = self._validate_work_id(work_id)
        if not all((source_id, source_version, input_hash, evidence_refs, evaluation_ref, submitted_by)):
            raise ExtractionInputError(
                "候选抽取必须提供来源版本、输入哈希、证据、评估引用和受控提交身份",
                label="candidate_binding",
            )
        if sample_chapters < 1:
            raise ValueError("sample_chapters 必须是正整数")
        chapters_dir = self.config.sources_dir / work_id / "chapters"
        scenes_dir = self.config.sources_dir / work_id / "scenes"

        # 获取作品标题
        client = DatabaseClient(self.config.sqlite_path)
        with client.get_connection() as conn:
            cur = conn.execute("SELECT title FROM works WHERE work_id = ?", (work_id,))
            row = cur.fetchone()
            title = row["title"] if row else work_id

        raw_files = []
        if chapters_dir.is_dir():
            raw_files = sorted(
                [f for f in chapters_dir.iterdir() if f.is_file() and f.suffix.lower() in (".md", ".txt")],
                key=lambda p: int("".join(filter(str.isdigit, p.stem)) or "9999")
            )
            summary_blob, dialogue_blob, style_corpus = self._build_text_corpora(raw_files, sample_chapters)
        elif scenes_dir.is_dir():
            s_files = sorted(list(scenes_dir.glob("*.md")))[:sample_chapters * 2]
            scenes_text = [
                _read_source_text(sf, f"scene[{sf.stem}]")[:500] for sf in s_files
            ]
            summary_blob = "\n\n".join(scenes_text)
            dialogue_blob = summary_blob
            style_corpus = summary_blob
        else:
            raise RuntimeError(f"作品 [{work_id}] 尚未导入章节或场景文本，请先执行 kb import 导入底本！")

        if not summary_blob:
            raise RuntimeError(f"作品 [{work_id}] 尚未导入章节或场景文本，请先执行 kb import 导入底本！")

        from concurrent.futures import ThreadPoolExecutor

        # 构造并发抽取 Prompts；把作品级规则状态显式传给模型，禁止隐式继承。
        scope_status = self._work_scope_status(work_id)
        scope_note = (
            "\n【作品级配置状态】\n"
            + json.dumps(scope_status, ensure_ascii=False)
            + "\n仅使用本作品输入文本和配置，不得引用或臆造其他作品规则。\n"
        )
        entity_prompt = scope_note + PROMPT_EXTRACT_ENTITIES.format(title=title, chapters_summary=summary_blob)
        causal_prompt = scope_note + PROMPT_EXTRACT_CAUSAL_EVENTS.format(title=title, work_id=work_id, chapters_summary=summary_blob)
        voice_prompt = scope_note + PROMPT_EXTRACT_VOICE_PROFILES.format(title=title, chapters_summary=dialogue_blob)
        rel_prompt = scope_note + PROMPT_EXTRACT_RELATIONSHIPS.format(title=title, chapters_summary=dialogue_blob)
        style_prompt = scope_note + PROMPT_EXTRACT_STYLE.format(title=title, chapters_summary=style_corpus)

        # 并发派发大模型抽取任务 (支持 Luna 推理强度自适应)
        with ThreadPoolExecutor(max_workers=5) as executor:
            fut_entity = executor.submit(
                _call_model, self.gw, "entities", "fast_extraction", entity_prompt
            )
            fut_causal = executor.submit(
                _call_model, self.gw, "causal_events", "fast_extraction", causal_prompt
            )
            fut_voice = executor.submit(
                _call_model, self.gw, "voice_profiles", "fast_extraction", voice_prompt
            )
            fut_rel = executor.submit(
                _call_model, self.gw, "relationships", "fast_extraction", rel_prompt
            )
            fut_style = executor.submit(
                _call_model, self.gw, "style_profile", "style_mining", style_prompt
            )

            raw_entity_res = fut_entity.result()
            raw_causal_res = fut_causal.result()
            raw_voice_res = fut_voice.result()
            raw_rel_res = fut_rel.result()
            raw_style_res = fut_style.result()

        # 1. 解析模型候选；此阶段只构造候选，不触碰正式实体/相态表。
        entity_data = self._parse_model_result(raw_entity_res, "entities", "characters")
        entity_characters = self._validated_list(entity_data, "entities", "characters")
        entity_items = self._validated_list(entity_data, "entities", "items", optional=True)

        saved_characters = []
        char_name_to_id = {}
        for char in entity_characters:
            cid = char.get("entity_id", f"char_{len(saved_characters)+1}")
            cname = char.get("name", cid)
            aliases = char.get("aliases", [])
            if not isinstance(aliases, list):
                raise ExtractionSchemaError(
                    "模型抽取 entities 的 characters.aliases 必须是列表",
                    label="entities",
                )
            attributes = char.get("attributes", {})
            if not isinstance(attributes, Mapping):
                raise ExtractionSchemaError(
                    "模型抽取 entities 的 characters.attributes 必须是对象",
                    label="entities",
                )
            phases = self._validated_list(
                char,
                "entities",
                "phases",
                optional=True,
            )
            char_name_to_id[cname] = cid
            phases_added = []
            for ph in phases:
                pid = ph.get("phase_id", f"phase_{len(phases_added)+1}")
                pname = ph.get("phase_name", "默认阶段")
                phases_added.append({
                    "phase_id": pid,
                    "phase_name": pname,
                    "valid_from": ph.get("valid_from", 0),
                    "valid_to": ph.get("valid_to"),
                    "traits": ph.get("traits", []),
                    "anti_behaviors": ph.get("anti_behaviors", []),
                })
            abilities = self._validated_list(
                char,
                "entities",
                "abilities",
                optional=True,
            )
            normalized_char = dict(char)
            normalized_char.update({
                "entity_id": cid,
                "name": cname,
                "aliases": list(aliases),
                "attributes": dict(attributes),
                "phases": phases,
                "abilities": abilities,
            })
            saved_characters.append({
                "id": cid,
                "name": cname,
                "data": normalized_char,
                "phases": phases_added,
            })

        saved_items = []
        for it in entity_items:
            iid = it.get("entity_id", f"item_{len(saved_items)+1}")
            iname = it.get("name", iid)
            attrs = it.get("attributes", {})
            if not isinstance(attrs, Mapping):
                raise ExtractionSchemaError(
                    "模型抽取 entities 的 items.attributes 必须是对象",
                    label="entities",
                )
            attrs = dict(attrs)
            if "symbolic_text" in it and "symbolic_text" not in attrs:
                attrs["symbolic_text"] = it["symbolic_text"]
            if "interaction_rituals" in it and "interaction_rituals" not in attrs:
                attrs["interaction_rituals"] = it["interaction_rituals"]
            candidate_item = dict(it)
            candidate_item["entity_id"] = iid
            candidate_item["attributes"] = attrs
            saved_items.append(candidate_item)

        # 2. 解析因果候选；不写入正式 DAG。
        causal_data = self._parse_model_result(raw_causal_res, "causal_events", "causal_events")
        causal_items = self._validated_list(causal_data, "causal_events", "causal_events")

        saved_events = []
        candidate_events = []
        for ev in causal_items:
            eid = ev.get("event_id", f"ev_{work_id}_{len(saved_events)+1}")
            try:
                order = int(ev.get("narrative_order", len(saved_events)+1))
            except (TypeError, ValueError) as exc:
                raise ExtractionSchemaError(
                    "模型抽取 causal_events 的 narrative_order 必须是整数",
                    label="causal_events",
                ) from exc
            summary = ev.get("summary", "")
            is_pod = bool(ev.get("is_pod_candidate", False))
            pod_analysis = ev.get("pod_analysis", "")

            normalized_event = dict(ev)
            normalized_event.update({
                "event_id": eid,
                "narrative_order": order,
                "summary": summary,
                "physical_time": ev.get("physical_time", ""),
                "is_pod_candidate": is_pod,
                "pod_analysis": pod_analysis,
            })
            candidate_events.append(normalized_event)
            saved_events.append({
                "event_id": eid,
                "order": order,
                "summary": summary,
                "physical_time": ev.get("physical_time", ""),
                "is_pod": is_pod,
                "pod_analysis": pod_analysis
            })

        # 3. 解析声线候选；不回填实体档案。
        voice_data = self._parse_model_result(raw_voice_res, "voice_profiles", "voice_profiles")
        voice_items = self._validated_list(voice_data, "voice_profiles", "voice_profiles")
        saved_voices = []
        candidate_voices = []
        for vp in voice_items:
            v_name = vp.get("name", "")
            if not v_name:
                continue
            # 寻找匹配的 entity_id，但不读取或写入正式实体档案。
            cid = char_name_to_id.get(v_name)
            if not cid:
                for sc in saved_characters:
                    if sc["name"] in v_name or v_name in sc["name"]:
                        cid = sc["id"]
                        break
            if not cid:
                hash_str = sha256_hex(v_name)[:8]
                cid = f"char_{hash_str}"

            clean_vp = {
                "tone": vp.get("tone", ""),
                "speech_style": vp.get("speech_style", ""),
                "catchphrases": vp.get("catchphrases", []),
                "gestures": vp.get("gestures", []),
                "taboos": vp.get("taboos", []),
                "dialogue_samples": vp.get("dialogue_samples", []),
            }
            candidate_voices.append({"name": v_name, "entity_id": cid, **clean_vp})
            saved_voices.append({"name": v_name, "entity_id": cid, "voice_profile": clean_vp})

        # 4. 解析关系候选；不写入 relationships.yaml 或 SQLite。
        rel_data = self._parse_model_result(raw_rel_res, "relationships", "relationships")
        saved_relationships = self._validated_list(rel_data, "relationships", "relationships")

        # 5. 提炼文笔风格与具象比喻档案 (Style Profile) 与正向大师范例 (Positive Exemplars)
        style_data = self._parse_model_result(raw_style_res, "style_profile", "metaphors")
        if not isinstance(style_data.get("metaphors"), Mapping):
            raise ExtractionSchemaError(
                "模型抽取 style_profile 的 metaphors 必须是对象",
                label="style_profile",
            )
        style_data["metaphors"] = dict(style_data["metaphors"])
        style_data["work_id"] = work_id
        style_data["status"] = "EVALUATION_CANDIDATE"

        # 6. 提炼章节连续性台账 (Continuity Ledger)
        saved_continuity = []
        candidate_continuity = []
        if raw_files:
            # 针对前 sample_chapters 个章节（尤其是后5章与最末章），抽取其结尾切片
            target_chapters = raw_files[:sample_chapters]
            # 重点抽取最后若干关键前章作为接续基准
            for f in target_chapters[-5:]:
                digits = "".join(filter(str.isdigit, f.stem))
                ch_idx = int(digits) if digits else sample_chapters
                text = _read_source_text(f, f"chapter[{ch_idx}]")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                ch_title = lines[0] if lines else f.stem
                tail_lines = lines[-12:]
                tail_text = "\n".join(tail_lines)

                cont_prompt = PROMPT_EXTRACT_CHAPTER_CONTINUITY.format(
                    title=title,
                    chapter_index=ch_idx,
                    chapter_title=ch_title,
                    ending_snippet=tail_text
                )
                raw_cont_res = _call_model(
                    self.gw,
                    "chapter_continuity",
                    "fast_extraction",
                    cont_prompt,
                )
                cont_data = self._parse_model_result(raw_cont_res, "chapter_continuity", "ending_location")
                loc = cont_data.get("ending_location", "")
                act_chars = cont_data.get("active_characters", [])
                situation = cont_data.get("ending_situation", "")
                hooks = cont_data.get("unresolved_hooks", [])
                if not isinstance(act_chars, list) or not isinstance(hooks, list):
                    raise ExtractionSchemaError(
                        "模型抽取 chapter_continuity 的 active_characters/unresolved_hooks 必须是列表",
                        label="chapter_continuity",
                    )
                candidate_continuity.append({
                    "chapter_index": ch_idx,
                    "title": ch_title,
                    "ending_location": loc,
                    "active_characters": list(act_chars),
                    "ending_situation": situation,
                    "unresolved_hooks": list(hooks),
                })
                saved_continuity.append({
                    "chapter_index": ch_idx,
                    "title": ch_title,
                    "ending_location": loc,
                    "active_characters": act_chars,
                    "ending_situation": situation,
                    "hooks": hooks
                })

        candidate_characters = [
            {
                **dict(character["data"]),
                "entity_id": character["id"],
                "phases": list(character["data"].get("phases", [])),
            }
            for character in saved_characters
        ]
        candidate_package = {
            "work_id": work_id,
            "title": title,
            "entities": {
                "characters": candidate_characters,
                "items": [dict(item) for item in saved_items],
            },
            "causal_events": {"causal_events": candidate_events},
            "voice_profiles": {"voice_profiles": candidate_voices},
            "relationships": {"relationships": saved_relationships},
            "continuity": {"chapters": candidate_continuity},
            "style_profile": style_data,
            "configuration": scope_status,
            "status": "EVALUATION_CANDIDATE",
        }
        candidate = self.candidate_store.submit(
            {
                "slug": self._candidate_slug(work_id),
                "version": "extraction-v1",
                "package": candidate_package,
            },
            work_id=work_id,
            source_id=source_id,
            source_version=source_version,
            input_hash=input_hash,
            evidence_refs=evidence_refs,
            evaluation_ref=evaluation_ref,
            submitted_by=submitted_by,
            require_scope=True,
        )
        return {
            "work_id": work_id,
            "title": title,
            "status": "EVALUATION_CANDIDATE",
            "configuration_status": scope_status["status"],
            "configuration_message": scope_status["message"],
            "candidate_id": candidate.candidate_id,
            "candidate_version": candidate.version,
            "candidate_hash": candidate.package_hash,
            "formal_knowledge_written": False,
            "requires_review": True,
            "characters": saved_characters,
            "items": saved_items,
            "causal_events": saved_events,
            "voices": saved_voices,
            "relationships": saved_relationships,
            "style_profile": style_data,
            "continuity": saved_continuity,
        }
