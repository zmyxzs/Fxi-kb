---
entity_id: char_zhanzheng_munaiyi
category: character
is_unique: true
name: 战争木乃伊
aliases:
- 小木乃伊
attributes:
  identity: 来自异变废土世界的召唤生物，与林七夜建立灵魂契约
  realm: 文本未明确
  role: 配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_zhanzheng_munaiyi:
        vessel_id: vessel_char_zhanzheng_munaiyi
        name: 战争木乃伊
        status: alive
        location: ''
    souls:
      soul_char_zhanzheng_munaiyi:
        soul_id: soul_char_zhanzheng_munaiyi
        true_name: 战争木乃伊
        is_controller: true
    personas:
      persona_char_zhanzheng_munaiyi_default:
        persona_id: persona_char_zhanzheng_munaiyi_default
        display_name: 战争木乃伊
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_zhanzheng_munaiyi
    active_soul_id: soul_char_zhanzheng_munaiyi
    active_persona_id: persona_char_zhanzheng_munaiyi_default
  abilities:
  - ability_id: skill_wuqitunshi
    name: 武器吞食与异变成长
    category: combat
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 能够吞食枪械、弹药、火药、刀具及炸药等武器装备，并随吞食大量军火而迅速长高、增强体型。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 9
phases:
- phase_id: phase_munaiyi_hungry
  phase_name: 吞食军火的幼体
  valid_from_order: 176
  valid_to_order: 177
  traits:
  - 贪食
  - 单纯
  - 兴奋
  - 成长迅速
  anti_behaviors:
  - 拒绝吞食被允许的武器
  - 无故伤害召唤者
  - 在获得食物后表现出冷漠或消极怠工
abilities:
- ability_id: skill_wuqitunshi
  name: 武器吞食与异变成长
  category: combat
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 能够吞食枪械、弹药、火药、刀具及炸药等武器装备，并随吞食大量军火而迅速长高、增强体型。
  source_origin: ''
  metadata: *id001
---

外表瘦小、初看不擅长战斗，实际拥有吞食军火并成长的特殊能力。性格表现为单纯、贪食且对武器有强烈兴趣。