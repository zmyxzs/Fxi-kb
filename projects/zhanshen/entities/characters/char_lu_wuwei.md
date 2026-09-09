---
entity_id: char_lu_wuwei
category: character
is_unique: true
name: 路无为
aliases:
- 外卖小哥
attributes:
  identity: 大夏守夜人体系成员，负责阻拦洛基
  realm: 人类顶尖战力，具备追溯时间相关能力
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_lu_wuwei:
        vessel_id: vessel_char_lu_wuwei
        name: 路无为
        status: alive
        location: ''
    souls:
      soul_char_lu_wuwei:
        soul_id: soul_char_lu_wuwei
        true_name: 路无为
        is_controller: true
    personas:
      persona_char_lu_wuwei_default:
        persona_id: persona_char_lu_wuwei_default
        display_name: 路无为
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_lu_wuwei
    active_soul_id: soul_char_lu_wuwei
    active_persona_id: persona_char_lu_wuwei_default
  abilities:
  - ability_id: skill_time_tracking
    name: 追溯时间
    category: innate
    sequence_num: null
    valid_from_chapter: 241
    valid_to_chapter: null
    cost_description: ''
    description: 具备追溯时间的能力，可试图触及洛基的行动轨迹，但容易被洛基规避。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 13
phases:
- phase_id: phase_lu_wuwei_delay_loki
  phase_name: 使命必达的牵制者
  valid_from_order: 251
  valid_to_order: 258
  traits:
  - 坚决
  - 自我牺牲
  - 务实
  - 忠于使命
  anti_behaviors:
  - 不会因敌我差距悬殊而主动放弃拖延任务
  - 不会把夺回湿婆怨置于城市与同伴安全之上
abilities:
- ability_id: skill_time_tracking
  name: 追溯时间
  category: innate
  sequence_num: null
  valid_from_chapter: 241
  valid_to_chapter: null
  cost_description: ''
  description: 具备追溯时间的能力，可试图触及洛基的行动轨迹，但容易被洛基规避。
  source_origin: ''
  metadata: *id001
---

外表平凡甚至带有生活化色彩，实则意志坚定，明知无法取胜仍以拖延和牵制为目标保护大局。