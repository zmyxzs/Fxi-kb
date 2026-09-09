---
entity_id: char_mumu
category: character
is_unique: true
name: 木木
aliases:
- 小木乃伊
- 木乃伊
attributes:
  identity: 林七夜身边的小木乃伊，具备召唤武器的特殊能力
  realm: 境界未明确
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_mumu:
        vessel_id: vessel_char_mumu
        name: 木木
        status: alive
        location: ''
    souls:
      soul_char_mumu:
        soul_id: soul_char_mumu
        true_name: 木木
        is_controller: true
    personas:
      persona_char_mumu_default:
        persona_id: persona_char_mumu_default
        display_name: 木木
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_mumu
    active_soul_id: soul_char_mumu
    active_persona_id: persona_char_mumu_default
  abilities:
  - ability_id: skill_mumu_weapon_manifestation
    name: 武器具现
    category: innate
    sequence_num: null
    valid_from_chapter: 181
    valid_to_chapter: null
    cost_description: ''
    description: 从绷带包裹的身体中具现出巨大火箭筒并发射火箭弹，使用后身形会缩小；能力消耗未明确。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 10
  - ability_id: skill_mumu_zhujing
    name: 巨大化护卫
    category: innate
    sequence_num: null
    valid_from_chapter: 221
    valid_to_chapter: null
    cost_description: ''
    description: 巨大化后可保护林七夜并承受部分攻击，结束后恢复原本大小。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 12
  - ability_id: skill_mumu_paoji
    name: 炮管轰击
    category: innate
    sequence_num: null
    valid_from_chapter: 221
    valid_to_chapter: null
    cost_description: ''
    description: 可使身体膨胀并延伸炮管，对目标进行火力轰击；短时间内可能无法获得新的武器。
    source_origin: ''
    metadata: &id003
      tier: 1
      batches:
      - 12
phases:
- phase_id: phase_mumu_combat_companion
  phase_name: 听令作战的木乃伊伙伴
  valid_from_order: 189
  valid_to_order: 190
  traits:
  - 服从
  - 沉默
  - 认真
  - 战斗辅助
  anti_behaviors:
  - 不会无故攻击林七夜及其同伴
  - 不会在接受命令后故意拖延
  - 不会以炫耀取代战斗执行
- phase_id: phase_mumu_weapon_limited
  phase_name: 武器受限支援期
  valid_from_order: 221
  valid_to_order: 236
  traits:
  - 依赖武器
  - 情绪外显
  - 忠诚
  - 火力支援
  anti_behaviors:
  - 不会在没有武器补充时凭空获得新武器
  - 不会无视林七夜的指令
  - 不会主动伤害需要保护的队友
abilities:
- ability_id: skill_mumu_weapon_manifestation
  name: 武器具现
  category: innate
  sequence_num: null
  valid_from_chapter: 181
  valid_to_chapter: null
  cost_description: ''
  description: 从绷带包裹的身体中具现出巨大火箭筒并发射火箭弹，使用后身形会缩小；能力消耗未明确。
  source_origin: ''
  metadata: *id001
- ability_id: skill_mumu_zhujing
  name: 巨大化护卫
  category: innate
  sequence_num: null
  valid_from_chapter: 221
  valid_to_chapter: null
  cost_description: ''
  description: 巨大化后可保护林七夜并承受部分攻击，结束后恢复原本大小。
  source_origin: ''
  metadata: *id002
- ability_id: skill_mumu_paoji
  name: 炮管轰击
  category: innate
  sequence_num: null
  valid_from_chapter: 221
  valid_to_chapter: null
  cost_description: ''
  description: 可使身体膨胀并延伸炮管，对目标进行火力轰击；短时间内可能无法获得新的武器。
  source_origin: ''
  metadata: *id003
---

沉默、听话、执行力强的小木乃伊，接受林七夜命令后以火箭筒牵制庄崎。