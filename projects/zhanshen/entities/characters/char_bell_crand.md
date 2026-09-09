---
entity_id: char_bell_crand
category: character
is_unique: true
name: 贝尔·克兰德
aliases:
- 贝尔·克兰德
attributes:
  identity: 来自西方迷雾的强大神秘，欲望与精神的主宰，当前处于濒死状态并被封在水晶中
  realm: 全盛时期力量强大，文本称有可能提升至无量；当前濒死
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_bell_crand:
        vessel_id: vessel_char_bell_crand
        name: 贝尔·克兰德
        status: alive
        location: ''
    souls:
      soul_char_bell_crand:
        soul_id: soul_char_bell_crand
        true_name: 贝尔·克兰德
        is_controller: true
    personas:
      persona_char_bell_crand_default:
        persona_id: persona_char_bell_crand_default
        display_name: 贝尔·克兰德
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_bell_crand
    active_soul_id: soul_char_bell_crand
    active_persona_id: persona_char_bell_crand_default
  abilities:
  - ability_id: skill_yuwang_jingshen_zhuzai
    name: 欲望与精神主宰
    category: magic
    sequence_num: null
    valid_from_chapter: 221
    valid_to_chapter: null
    cost_description: ''
    description: 全盛时期可在短时间内令整座城市的人自相残杀；当前能力受濒死与封印状态限制。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 12
phases:
- phase_id: phase_bell_crand_dying_sealed
  phase_name: 濒死封印期
  valid_from_order: 231
  valid_to_order: 237
  traits:
  - 濒死
  - 被封印
  - 高危险潜力
  - 受他人利用
  anti_behaviors:
  - 不会以当前濒死状态直接展现全盛力量
  - 不会脱离水晶封印自行完成完整行动
  - 不会被描述为普通低危神秘
abilities:
- ability_id: skill_yuwang_jingshen_zhuzai
  name: 欲望与精神主宰
  category: magic
  sequence_num: null
  valid_from_chapter: 221
  valid_to_chapter: null
  cost_description: ''
  description: 全盛时期可在短时间内令整座城市的人自相残杀；当前能力受濒死与封印状态限制。
  source_origin: ''
  metadata: *id001
---

来自西方迷雾的高危神秘，被酒馆老板狂热崇拜并试图通过献祭仪式恢复、提升其力量。