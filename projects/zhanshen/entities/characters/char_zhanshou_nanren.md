---
entity_id: char_zhanshou_nanren
category: character
is_unique: true
name: 无头男人
aliases:
- 猎音者
attributes:
  identity: 集训营三栋内的川境神秘，以声音为力量来源
  realm: 川境
  role: 重要反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_zhanshou_nanren:
        vessel_id: vessel_char_zhanshou_nanren
        name: 无头男人
        status: alive
        location: ''
    souls:
      soul_char_zhanshou_nanren:
        soul_id: soul_char_zhanshou_nanren
        true_name: 无头男人
        is_controller: true
    personas:
      persona_char_zhanshou_nanren_default:
        persona_id: persona_char_zhanshou_nanren_default
        display_name: 无头男人
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_zhanshou_nanren
    active_soul_id: soul_char_zhanshou_nanren
    active_persona_id: persona_char_zhanshou_nanren_default
  abilities:
  - ability_id: skill_shengyin_zengqiang
    name: 声音增幅
    category: combat
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 能够利用周围战斗、移动及声响增强自身实力，声音越大越难以对付。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 9
  - ability_id: skill_yaozhan
    name: 斩首刀腰斩
    category: combat
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 拖拽巨大的斩首刀进行高速斩击，可瞬间腰斩目标并破坏房门。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 9
phases:
- phase_id: phase_lieting_silent_hunter
  phase_name: 以声增幅的无头猎杀者
  valid_from_order: 163
  valid_to_order: 172
  traits:
  - 凶猛
  - 机制明确
  - 对声音敏感
  - 近战压迫力强
  anti_behaviors:
  - 在完全无声环境中无依据地精准锁定目标
  - 放弃斩首刀进行远程或复杂法术战斗
  - 表现出正常人类的语言交流与情绪反应
abilities:
- ability_id: skill_shengyin_zengqiang
  name: 声音增幅
  category: combat
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 能够利用周围战斗、移动及声响增强自身实力，声音越大越难以对付。
  source_origin: ''
  metadata: *id001
- ability_id: skill_yaozhan
  name: 斩首刀腰斩
  category: combat
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 拖拽巨大的斩首刀进行高速斩击，可瞬间腰斩目标并破坏房门。
  source_origin: ''
  metadata: *id002
---

无头、无血液特征的高危近战神秘，依靠声音判断和强化自身。其机制呈现出对安静环境的弱点，因此林七夜与曹渊通过压制心跳和脚步声规避其感知。