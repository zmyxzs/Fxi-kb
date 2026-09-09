---
entity_id: char_yanmai_dilong
category: character
is_unique: true
name: 炎脉地龙
aliases:
- 炎脉龙
- 红颜
attributes:
  identity: 被呓语驯服并签订灵魂契约的强大神秘，后成为精神病院护工
  realm: 川境
  role: 配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_yanmai_dilong:
        vessel_id: vessel_char_yanmai_dilong
        name: 炎脉地龙
        status: alive
        location: ''
    souls:
      soul_char_yanmai_dilong:
        soul_id: soul_char_yanmai_dilong
        true_name: 炎脉地龙
        is_controller: true
    personas:
      persona_char_yanmai_dilong_default:
        persona_id: persona_char_yanmai_dilong_default
        display_name: 炎脉地龙
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_yanmai_dilong
    active_soul_id: soul_char_yanmai_dilong
    active_persona_id: persona_char_yanmai_dilong_default
  abilities:
  - ability_id: skill_human_form
    name: 人形化身
    category: hospital
    sequence_num: null
    valid_from_chapter: 201
    valid_to_chapter: null
    cost_description: ''
    description: 可化为女性人形，便于在精神病院中承担洗碗、照料等护工工作。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 11
  - ability_id: skill_yanmai_flame
    name: 炎脉火焰
    category: innate
    sequence_num: null
    valid_from_chapter: 201
    valid_to_chapter: null
    cost_description: ''
    description: 操控高温火焰与岩浆，可释放大规模火球和火焰龙卷；攻击范围巨大，可能连同敌我一并摧毁。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 11
  - ability_id: skill_dragon_flight
    name: 龙翼飞行
    category: innate
    sequence_num: null
    valid_from_chapter: 201
    valid_to_chapter: null
    cost_description: ''
    description: 以龙形挥动翅膀在空中高速移动和作战。
    source_origin: ''
    metadata: &id003
      tier: 1
      batches:
      - 11
phases:
- phase_id: phase_yanmai_bound_believer
  phase_name: 受灵魂契约束缚的地龙
  valid_from_order: 201
  valid_to_order: 204
  traits:
  - 暴烈
  - 记仇
  - 服从契约
  - 残酷
  anti_behaviors:
  - 不会在呓语仍在场时公然违抗其直接命令
  - 不会主动与马逸添和谐共处
  - 不会因误伤同阵营者而停止报复
- phase_id: phase_yanmai_hospital_caregiver
  phase_name: 被收编的人形护工
  valid_from_order: 216
  valid_to_order: 217
  traits:
  - 服从新主
  - 适应性强
  - 外表亲和
  - 保留龙族气势
  anti_behaviors:
  - 不会继续无条件服从呓语
  - 不会在病院内随意发动毁灭性火焰攻击
  - 不会因李毅飞或阿朱的善意而立即失去自身判断
abilities:
- ability_id: skill_human_form
  name: 人形化身
  category: hospital
  sequence_num: null
  valid_from_chapter: 201
  valid_to_chapter: null
  cost_description: ''
  description: 可化为女性人形，便于在精神病院中承担洗碗、照料等护工工作。
  source_origin: ''
  metadata: *id001
- ability_id: skill_yanmai_flame
  name: 炎脉火焰
  category: innate
  sequence_num: null
  valid_from_chapter: 201
  valid_to_chapter: null
  cost_description: ''
  description: 操控高温火焰与岩浆，可释放大规模火球和火焰龙卷；攻击范围巨大，可能连同敌我一并摧毁。
  source_origin: ''
  metadata: *id002
- ability_id: skill_dragon_flight
  name: 龙翼飞行
  category: innate
  sequence_num: null
  valid_from_chapter: 201
  valid_to_chapter: null
  cost_description: ''
  description: 以龙形挥动翅膀在空中高速移动和作战。
  source_origin: ''
  metadata: *id003
---

炎脉地龙原本是呓语的信徒和契约神秘，性情暴烈、记仇且缺乏对同类的顾忌。呓语消失后，它试图以大规模火焰攻击清除敌人，最终被承载倪克斯力量的林七夜击败并收编为护工。