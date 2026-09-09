---
entity_id: char_han_jiaoguan
category: character
is_unique: true
name: 韩教官
aliases:
- 韩栗
- 韩栗教官
attributes:
  identity: 守夜人教官，精通多种武艺
  realm: 未明确
  role: 关键引路人/导师
  trinity:
    mode: single_soul
    vessels:
      vessel_char_han_jiaoguan:
        vessel_id: vessel_char_han_jiaoguan
        name: 韩教官
        status: alive
        location: ''
    souls:
      soul_char_han_jiaoguan:
        soul_id: soul_char_han_jiaoguan
        true_name: 韩教官
        is_controller: true
    personas:
      persona_char_han_jiaoguan_default:
        persona_id: persona_char_han_jiaoguan_default
        display_name: 韩教官
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_han_jiaoguan
    active_soul_id: soul_char_han_jiaoguan
    active_persona_id: persona_char_han_jiaoguan_default
  abilities:
  - ability_id: skill_wushu_mastery
    name: 综合武艺
    category: combat
    sequence_num: null
    valid_from_chapter: 81
    valid_to_chapter: null
    cost_description: ''
    description: 熟悉多种武器与战斗技巧，能够担任新兵武艺启蒙与战斗风格引导者。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 5
phases:
- phase_id: phase_han_collapse
  phase_name: 教官受挫期
  valid_from_order: 92
  valid_to_order: 95
  traits:
  - 无奈
  - 崩溃
  - 专业
  - 被迫应战
  anti_behaviors:
  - 不会在失控局面下继续逞强硬拼
  - 不会否认自身武艺专业性
  - 不会因短暂受挫彻底放弃教官职责
- phase_id: phase_hanli_sword_mentor
  phase_name: 直率的刀术指导者
  valid_from_order: 105
  valid_to_order: 105
  traits:
  - 专业
  - 直率
  - 客观
  - 善于因材施教
  anti_behaviors:
  - 不会为了鼓励而虚假夸大林七夜的刀术天赋
  - 不会简单断言林七夜毫无成长可能
  - 不会强迫其继续走不适合的道路
abilities:
- ability_id: skill_wushu_mastery
  name: 综合武艺
  category: combat
  sequence_num: null
  valid_from_chapter: 81
  valid_to_chapter: null
  cost_description: ''
  description: 熟悉多种武器与战斗技巧，能够担任新兵武艺启蒙与战斗风格引导者。
  source_origin: ''
  metadata: *id001
---

原本因被迫应对曹渊等妖孽新兵而崩溃，但拥有扎实全面的武艺底蕴，最终接受指导新兵成长的职责。