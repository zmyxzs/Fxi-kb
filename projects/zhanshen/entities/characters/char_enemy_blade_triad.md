---
entity_id: char_enemy_blade_triad
category: character
is_unique: true
name: 三刀男
aliases:
- 三刀男
attributes:
  identity: 袭击百里胖胖与曹渊的敌方成员
  realm: 未知
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_enemy_blade_triad:
        vessel_id: vessel_char_enemy_blade_triad
        name: 三刀男
        status: alive
        location: ''
    souls:
      soul_char_enemy_blade_triad:
        soul_id: soul_char_enemy_blade_triad
        true_name: 三刀男
        is_controller: true
    personas:
      persona_char_enemy_blade_triad_default:
        persona_id: persona_char_enemy_blade_triad_default
        display_name: 三刀男
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_enemy_blade_triad
    active_soul_id: soul_char_enemy_blade_triad
    active_persona_id: persona_char_enemy_blade_triad_default
  abilities:
  - ability_id: skill_three_blades
    name: 三刀操控
    category: combat
    sequence_num: null
    valid_from_chapter: 101
    valid_to_chapter: null
    cost_description: ''
    description: 拔出一柄刀后，可令另外两柄刀如被无形之手操控般同时出鞘，形成多刀攻击。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 6
phases:
- phase_id: phase_enemy_blade_assault
  phase_name: 沉默的近战执行者
  valid_from_order: 116
  valid_to_order: 119
  traits:
  - 冷漠
  - 直接
  - 寡言
  - 杀伐果断
  anti_behaviors:
  - 不会在战斗中进行无意义长篇解释
  - 不会轻易放弃已锁定的目标
  - 不会主动展现温和或犹豫姿态
abilities:
- ability_id: skill_three_blades
  name: 三刀操控
  category: combat
  sequence_num: null
  valid_from_chapter: 101
  valid_to_chapter: null
  cost_description: ''
  description: 拔出一柄刀后，可令另外两柄刀如被无形之手操控般同时出鞘，形成多刀攻击。
  source_origin: ''
  metadata: *id001
---

沉默、面无表情且行动直接的近战袭击者，以三柄刀同时作战，缺乏多余言语。