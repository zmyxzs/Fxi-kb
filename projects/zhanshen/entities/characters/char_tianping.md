---
entity_id: char_tianping
category: character
is_unique: true
name: 天平
aliases: []
attributes:
  identity: 【假面】小队成员，战术指挥者
  realm: 被压制至盏境
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_tianping:
        vessel_id: vessel_char_tianping
        name: 天平
        status: alive
        location: ''
    souls:
      soul_char_tianping:
        soul_id: soul_char_tianping
        true_name: 天平
        is_controller: true
    personas:
      persona_char_tianping_default:
        persona_id: persona_char_tianping_default
        display_name: 天平
        speech_channel: physical_dialogue
        voice_profile: &id001
          name: 天平
          tone: 轻佻从容、带有观察者气质的青年声线；习惯保持旁观式幽默和自信，真正遭遇危险时才显出后怕与狼狈。
          speech_style: 喜欢用感慨、调侃和轻描淡写的方式评价战局；面对强敌容易轻敌，台词常有看热闹不嫌事大的意味。
          catchphrases:
          - 队长，这次你也该出手了吧？
          - 来砍我吧，我很好奇。
          - 放心吧，这点小事，还用不着队长出面。
          - 我说，要不要这么拼啊？
          gestures:
          - 微微眯起眼睛观察局势
          - 满眼期待地盯着危险对手
          - 揉眼角，面对王面瞬移般出手时仍未回神
          - 晃晃悠悠飘在空中，保持懒散旁观姿态
          taboos:
          - 不能一开始就表现得畏缩谨慎、缺乏好奇心
          - 不能把所有战况都严肃分析而失去看热闹式调侃
          - 不能在王面面前完全不顾队长威信、公开揭穿其时间回溯
          dialogue_samples:
          - context: 看到曹渊即将拔刀，天平主动要求对方攻击自己。
            reply: 来砍我吧，我很好奇。
            user: ——
          - context: 王面准备替他出手时，他仍认为曹渊的攻击不足为惧。
            reply: 放心吧，这点小事，还用不着队长出面。
            user: 你确定，有把握活下来吗？
          - context: 疯魔曹渊持续咆哮、战斗不止，天平试图劝其休息。
            reply: 我说，要不要这么拼啊？你吼了这么久不累吗？咱也去歇会吧。
            user: ——
    active_vessel_id: vessel_char_tianping
    active_soul_id: soul_char_tianping
    active_persona_id: persona_char_tianping_default
  voice_profile: *id001
  abilities:
  - ability_id: skill_force_field_conversion
    name: 力场转换
    category: innate
    sequence_num: null
    valid_from_chapter: 61
    valid_to_chapter: null
    cost_description: ''
    description: 改变战场中的力场，使金属残片、碎渣、武器和墙砖漂浮并形成攻击或防御结构。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 4
  - ability_id: skill_gravity_amplification
    name: 重力加倍
    category: innate
    sequence_num: null
    valid_from_chapter: 61
    valid_to_chapter: null
    cost_description: ''
    description: 增强局部重力，对敌方行动和战场位置形成压制；具体范围与代价未明确。
    source_origin: ''
    metadata: &id003
      tier: 1
      batches:
      - 4
phases:
- phase_id: phase_tianping_tactical_commander
  phase_name: 冷静的战术指挥者
  valid_from_order: 77
  valid_to_order: 80
  traits:
  - 理智
  - 分析力强
  - 重视战术
  - 团队协作
  anti_behaviors:
  - 不会因漩涡的抱怨而放弃既定战略
  - 不会忽略人数劣势带来的车轮战风险
  - 不会在战场上仅凭个人冲动行动
abilities:
- ability_id: skill_force_field_conversion
  name: 力场转换
  category: innate
  sequence_num: null
  valid_from_chapter: 61
  valid_to_chapter: null
  cost_description: ''
  description: 改变战场中的力场，使金属残片、碎渣、武器和墙砖漂浮并形成攻击或防御结构。
  source_origin: ''
  metadata: *id002
- ability_id: skill_gravity_amplification
  name: 重力加倍
  category: innate
  sequence_num: null
  valid_from_chapter: 61
  valid_to_chapter: null
  cost_description: ''
  description: 增强局部重力，对敌方行动和战场位置形成压制；具体范围与代价未明确。
  source_origin: ''
  metadata: *id003
voice_profile: *id001
---

理智、冷静、擅长分析战场形势的战术核心。能够迅速判断人数劣势并提出优先清除最大威胁的策略。