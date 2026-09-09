---
entity_id: char_gu_jiaoguan
category: character
is_unique: true
name: 顾教官
aliases:
- 老顾
attributes:
  identity: 华清大学在职教授，守夜人集训营教官
  realm: 实力未明
  role: 关键引路人
  trinity:
    mode: single_soul
    vessels:
      vessel_char_gu_jiaoguan:
        vessel_id: vessel_char_gu_jiaoguan
        name: 顾教官
        status: alive
        location: ''
    souls:
      soul_char_gu_jiaoguan:
        soul_id: soul_char_gu_jiaoguan
        true_name: 顾教官
        is_controller: true
    personas:
      persona_char_gu_jiaoguan_default:
        persona_id: persona_char_gu_jiaoguan_default
        display_name: 顾教官
        speech_channel: physical_dialogue
        voice_profile: &id001
          name: 顾教官
          tone: 认真求知、容易钻牛角尖的中年学者型声线；平时温和，遇到理论问题会迅速陷入执着和兴奋。
          speech_style: 讲课时结构严谨、定义清楚；被提出终极问题后从笃定转为迟疑，再转入自我说服式的求索，语气中带有学究气。
          catchphrases:
          - 这个问题……很好。
          - 求索与辩证，是每一个求学者都应该做到的事情。
          - 我不会回避这个问题。
          - 给我一段时间，我一定会给你一个答案！
          gestures:
          - 被问题击中后呆立、沉默思考数分钟。
          - 深深吸气，抬头重新组织答案。
          - 急匆匆离开教室，边走边低头思考。
          - 返营时戴帽子、口罩并警惕环顾，显出憔悴和躲避。
          taboos:
          - 不能面对学术问题敷衍了事或粗暴打断学生。
          - 不能突然变成冷酷威权、拒绝讨论的教官。
          - 不能把自己的精神状况写成轻佻玩笑或毫无自知。
          dialogue_samples:
          - context: 林七夜提出世界可能由更高维存在创造的理论。
            reply: 应该，不会吧……
            user: 你有没有想过……我们有可能是一个更高维度的存在创造出来的？
          - context: 他无法回答问题，却不愿轻易认输。
            reply: 林七夜，你的这个问题……很好，但是现在我还无法给出答案，你等我回去研究研究，到时候给你答复。
            user: 你能证明这个世界是真实的吗？
    active_vessel_id: vessel_char_gu_jiaoguan
    active_soul_id: soul_char_gu_jiaoguan
    active_persona_id: persona_char_gu_jiaoguan_default
  voice_profile: *id001
phases:
- phase_id: phase_gu_teacher
  phase_name: 博学授课期
  valid_from_order: 146
  valid_to_order: 146
  traits:
  - 博学
  - 理性
  - 善于思辨
  - 教学自信
  anti_behaviors:
  - 不会承认自己知识贫乏
  - 不会对真实世界问题毫无思考就敷衍作答
- phase_id: phase_gu_obsessed
  phase_name: 真实世界困扰期
  valid_from_order: 147
  valid_to_order: 152
  traits:
  - 困惑
  - 执着
  - 精神失衡
  - 羞耻回避
  anti_behaviors:
  - 不会坦然面对学生并公开承认自己因问题失常
  - 不会继续若无其事地进行正常授课
  - 不会主动将异常状态当作普通小事
voice_profile: *id001
---

知识渊博、擅长哲学与世界观思辨；被林七夜关于真实世界的问题困扰并因此失踪，表现出精神状态异常。