---
entity_id: char_ghost_face_monster
category: character
is_unique: true
name: 鬼面人
aliases:
- 神话生物鬼面王
- 鬼面王
attributes:
  identity: 袭击老城区的神话生物，鬼面人肃清行动的目标
  realm: 强力神话生物，鬼面王在战斗中受重伤
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_ghost_face_monster:
        vessel_id: vessel_char_ghost_face_monster
        name: 鬼面人
        status: alive
        location: ''
    souls:
      soul_char_ghost_face_monster:
        soul_id: soul_char_ghost_face_monster
        true_name: 鬼面人
        is_controller: true
    personas:
      persona_char_ghost_face_monster_default:
        persona_id: persona_char_ghost_face_monster_default
        display_name: 鬼面人
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_ghost_face_monster
    active_soul_id: soul_char_ghost_face_monster
    active_persona_id: persona_char_ghost_face_monster_default
  abilities:
  - ability_id: skill_ghostface_physical_predation
    name: 神话生物体魄与捕食
    category: combat
    sequence_num: null
    valid_from_chapter: 1
    valid_to_chapter: null
    cost_description: ''
    description: 拥有远超人类的力量、速度和体重，可从高处突袭并以利爪撕裂目标，具备啃食受害者的凶残捕食本能。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 1
  - ability_id: skill_guimian_xiangdi
    name: 鬼面相地
    category: taboo_domain
    sequence_num: '176'
    valid_from_chapter: 21
    valid_to_chapter: null
    cost_description: ''
    description: 禁墟序列176，制造巨大鬼脸并扭曲目标的空间感知、方向感和触觉，使天地仿佛颠倒；可通过雨水触觉等外部标准破解。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 2
phases:
- phase_id: phase_ghostface_predator
  phase_name: 老城区猎杀期
  valid_from_order: 5
  valid_to_order: 10
  traits:
  - 嗜血
  - 凶残
  - 突袭
  - 以人类为食
  anti_behaviors:
  - 不会对受害者产生怜悯
  - 不会在捕食时遵守人类交流规则
  - 不会因普通人的威胁而主动停止猎杀
- phase_id: phase_ghostface_stunned
  phase_name: 神威压制与死亡期
  valid_from_order: 9
  valid_to_order: 10
  traits:
  - 短暂僵硬
  - 遭受神威震慑
  - 生机断绝
  anti_behaviors:
  - 不会在大脑被贯穿后继续保持完整战斗行动
  - 不会在神威压制期间立即反杀林七夜
  - 不会恢复为普通人形态
- phase_id: phase_ghostface_dying
  phase_name: 濒死与灵魂消融
  valid_from_order: 28
  valid_to_order: 38
  traits:
  - 濒死
  - 本能挣扎
  - 最终覆灭
  anti_behaviors:
  - 不会在被斩首后重新恢复完整战力
  - 不会抵抗林七夜金色眼瞳造成的最终消融
  - 不会作为病院护工被强行收编
abilities:
- ability_id: skill_ghostface_physical_predation
  name: 神话生物体魄与捕食
  category: combat
  sequence_num: null
  valid_from_chapter: 1
  valid_to_chapter: null
  cost_description: ''
  description: 拥有远超人类的力量、速度和体重，可从高处突袭并以利爪撕裂目标，具备啃食受害者的凶残捕食本能。
  source_origin: ''
  metadata: *id001
- ability_id: skill_guimian_xiangdi
  name: 鬼面相地
  category: taboo_domain
  sequence_num: '176'
  valid_from_chapter: 21
  valid_to_chapter: null
  cost_description: ''
  description: 禁墟序列176，制造巨大鬼脸并扭曲目标的空间感知、方向感和触觉，使天地仿佛颠倒；可通过雨水触觉等外部标准破解。
  source_origin: ''
  metadata: *id002
---

鬼面人是残暴、嗜血且以人类为猎物的怪物，能够在夜间袭击普通人。其个体被林七夜利用炽天使神威制造的短暂僵直击杀。