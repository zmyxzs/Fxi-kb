---
entity_id: char_enemy_archer
category: character
is_unique: true
name: 女弓箭手
aliases:
- 弓箭手
- 袭击者
attributes:
  identity: 袭击集训营及相关目标的敌方成员
  realm: 未知
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_enemy_archer:
        vessel_id: vessel_char_enemy_archer
        name: 女弓箭手
        status: alive
        location: ''
    souls:
      soul_char_enemy_archer:
        soul_id: soul_char_enemy_archer
        true_name: 女弓箭手
        is_controller: true
    personas:
      persona_char_enemy_archer_default:
        persona_id: persona_char_enemy_archer_default
        display_name: 女弓箭手
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_enemy_archer
    active_soul_id: soul_char_enemy_archer
    active_persona_id: persona_char_enemy_archer_default
  abilities:
  - ability_id: skill_enemy_archery
    name: 高速箭术
    category: innate
    sequence_num: null
    valid_from_chapter: 101
    valid_to_chapter: null
    cost_description: ''
    description: 能够连续射出高速羽箭，并以三箭齐发封锁目标走位；攻击速度和穿透力极强。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 6
phases:
- phase_id: phase_enemy_archer_hunter
  phase_name: 自负的猎杀者
  valid_from_order: 117
  valid_to_order: 118
  traits:
  - 冷酷
  - 自负
  - 攻击性强
  - 执行目标明确
  anti_behaviors:
  - 不会因目标是新兵就轻视到放弃远程优势
  - 不会在确认林七夜身份后停止追杀
  - 不会主动与敌人进行友善交流
abilities:
- ability_id: skill_enemy_archery
  name: 高速箭术
  category: innate
  sequence_num: null
  valid_from_chapter: 101
  valid_to_chapter: null
  cost_description: ''
  description: 能够连续射出高速羽箭，并以三箭齐发封锁目标走位；攻击速度和穿透力极强。
  source_origin: ''
  metadata: *id001
---

性格冷酷、轻蔑且具有攻击性，确认林七夜为前所未有的双神代理人后试图将其击杀，却被林七夜以黑夜力量反制。