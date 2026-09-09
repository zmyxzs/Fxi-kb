---
entity_id: char_zhuangqi
category: character
is_unique: true
name: 庄崎
aliases:
- 鸭舌帽男人
attributes:
  identity: 古神教会成员，追踪并猎杀守夜人新兵
  realm: 川境
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_zhuangqi:
        vessel_id: vessel_char_zhuangqi
        name: 庄崎
        status: alive
        location: ''
    souls:
      soul_char_zhuangqi:
        soul_id: soul_char_zhuangqi
        true_name: 庄崎
        is_controller: true
    personas:
      persona_char_zhuangqi_default:
        persona_id: persona_char_zhuangqi_default
        display_name: 庄崎
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_zhuangqi
    active_soul_id: soul_char_zhuangqi
    active_persona_id: persona_char_zhuangqi_default
  abilities:
  - ability_id: skill_liedao_fenjie
    name: 猎刀分解控制
    category: combat
    sequence_num: null
    valid_from_chapter: 181
    valid_to_chapter: null
    cost_description: ''
    description: 将特殊猎刀分解为大量细密刀片并远程操控，形成银雾刀片风暴；目前明确只能作用于该柄猎刀或同类型特殊金属。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 10
phases:
- phase_id: phase_zhuangqi_hunter
  phase_name: 自负的猎杀者
  valid_from_order: 186
  valid_to_order: 197
  traits:
  - 追踪老练
  - 暴躁
  - 自负
  - 攻击性强
  anti_behaviors:
  - 不会轻易放弃林七夜这个猎物
  - 不会因火箭弹骚扰而停止追击
  - 不会主动承认战术失误
abilities:
- ability_id: skill_liedao_fenjie
  name: 猎刀分解控制
  category: combat
  sequence_num: null
  valid_from_chapter: 181
  valid_to_chapter: null
  cost_description: ''
  description: 将特殊猎刀分解为大量细密刀片并远程操控，形成银雾刀片风暴；目前明确只能作用于该柄猎刀或同类型特殊金属。
  source_origin: ''
  metadata: *id001
---

经验老道、擅长追踪，性格暴躁而自负，依靠特殊猎刀进行高压追杀，最终被林七夜借力反杀。