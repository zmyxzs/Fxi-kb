---
entity_id: char_mayi_tian
category: character
is_unique: true
name: 马逸添
aliases: []
attributes:
  identity: 古神教会成员，炎脉地龙地下空洞中的战斗人员
  realm: 境界未明确
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_mayi_tian:
        vessel_id: vessel_char_mayi_tian
        name: 马逸添
        status: alive
        location: ''
    souls:
      soul_char_mayi_tian:
        soul_id: soul_char_mayi_tian
        true_name: 马逸添
        is_controller: true
    personas:
      persona_char_mayi_tian_default:
        persona_id: persona_char_mayi_tian_default
        display_name: 马逸添
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_mayi_tian
    active_soul_id: soul_char_mayi_tian
    active_persona_id: persona_char_mayi_tian_default
phases:
- phase_id: phase_mayi_wounded_subordinate
  phase_name: 绝境中的受伤下属
  valid_from_order: 198
  valid_to_order: 199
  traits:
  - 服从
  - 畏惧上级
  - 负伤坚持
  anti_behaviors:
  - 不会在呓语面前公然违抗命令
  - 不会隐瞒庄崎等人的死亡情况
  - 不会在未获许可时擅自改变任务目标
---

在炎脉地龙制造的岩浆环境中负伤作战并向呓语汇报，服从性强但被呓语视为废物。