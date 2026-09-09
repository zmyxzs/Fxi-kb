---
entity_id: char_shiqie_guidong
category: character
is_unique: true
name: 十切鬼童
aliases:
- 十切鬼童群
attributes:
  identity: 与酒馆老板共同出现的冤鬼类神秘，能够成群作战
  realm: 文本未明确
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_shiqie_guidong:
        vessel_id: vessel_char_shiqie_guidong
        name: 十切鬼童
        status: alive
        location: ''
    souls:
      soul_char_shiqie_guidong:
        soul_id: soul_char_shiqie_guidong
        true_name: 十切鬼童
        is_controller: true
    personas:
      persona_char_shiqie_guidong_default:
        persona_id: persona_char_shiqie_guidong_default
        display_name: 十切鬼童
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_shiqie_guidong
    active_soul_id: soul_char_shiqie_guidong
    active_persona_id: persona_char_shiqie_guidong_default
  abilities:
  - ability_id: skill_shiqie_gui_tong
    name: 十指短刀斩击
    category: combat
    sequence_num: null
    valid_from_chapter: 221
    valid_to_chapter: null
    cost_description: ''
    description: 挥舞短刀高速攻击，数量可同时达到十只；智力较低，不擅长独立完成复杂案件布局。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 12
phases:
- phase_id: phase_shiqie_gui_tong_controlled
  phase_name: 受控战斗期
  valid_from_order: 226
  valid_to_order: 237
  traits:
  - 凶猛
  - 高速
  - 群体行动
  - 智力有限
  anti_behaviors:
  - 不会独立设计复杂仪式
  - 不会表现出高水平推理能力
  - 不会脱离操控者自行制定长期计划
abilities:
- ability_id: skill_shiqie_gui_tong
  name: 十指短刀斩击
  category: combat
  sequence_num: null
  valid_from_chapter: 221
  valid_to_chapter: null
  cost_description: ''
  description: 挥舞短刀高速攻击，数量可同时达到十只；智力较低，不擅长独立完成复杂案件布局。
  source_origin: ''
  metadata: *id001
---

行动迅速但智力有限的神秘个体，被酒馆老板作为战斗和仪式工具使用。