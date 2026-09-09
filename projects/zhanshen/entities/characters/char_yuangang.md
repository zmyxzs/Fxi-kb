---
entity_id: char_yuangang
category: character
is_unique: true
name: 袁罡
aliases:
- 袁教官
attributes:
  identity: 守夜人集训营教官及管理者
  realm: 文本未明确
  role: 关键引路人
  trinity:
    mode: single_soul
    vessels:
      vessel_char_yuangang:
        vessel_id: vessel_char_yuangang
        name: 袁罡
        status: alive
        location: ''
    souls:
      soul_char_yuangang:
        soul_id: soul_char_yuangang
        true_name: 袁罡
        is_controller: true
    personas:
      persona_char_yuangang_default:
        persona_id: persona_char_yuangang_default
        display_name: 袁罡
        speech_channel: physical_dialogue
        voice_profile: &id001
          catchphrases:
          - 所有救援小队，汇报情况。
          - 是你太具备辨识度了。
          - 你的目标是林七夜？
          - 看来我说中了。
          dialogue_samples:
          - context: 袁罡确认所有救援小队的进度。
            reply: 所有救援小队，汇报情况。
            user: 设备已经接通，等待首长指示。
          - context: 认出前来拖住自己的呓语。
            reply: 是你太具备辨识度了。真实的噩梦，思维操控者，古神教会中最古老的三位‘神’之一……当然，最关键的，还是你那令人作呕的贵族风度。
            user: 想不到您这位上京市小队的副队长，竟然还认识我。
          - context: 袁罡推断地震和泥石流是敌人制造的。
            reply: 你的目标是林七夜？
            user: 所以，这次地震和泥石流，并不是自然灾害。
          gestures:
          - 背着双手站在帐篷门口，沉默眺望灾区。
          - 皱眉观察环境，脸色在确认敌人身份后骤然阴沉。
          - 面对强敌仍腰杆笔挺，不因重伤而弯腰退缩。
          - 说话时很少有多余动作，主要以目光和停顿制造压迫感。
          name: 袁罡
          speech_style: 命令式短句为主，惜字如金；善于从细节迅速推断敌人的目的，质问时冷静而有压迫感。
          taboos:
          - 绝不会在下属面前慌乱失态或无故消失。
          - 绝不会为了求生抛弃仍在救援中的队伍。
          - 绝不会用轻佻或夸张语言与敌人争吵。
          tone: 低沉厚重、威严冷峻的首长声线；情绪极少外露，越是危险越显得沉着克制。
    active_vessel_id: vessel_char_yuangang
    active_soul_id: soul_char_yuangang
    active_persona_id: persona_char_yuangang_default
  voice_profile: *id001
phases:
- phase_id: phase_yuangang_instructor
  phase_name: 严格而务实的教官
  valid_from_order: 172
  valid_to_order: 180
  traits:
  - 严谨
  - 重视纪律
  - 判断务实
  - 责任感强
  - 危机调度果断
  anti_behaviors:
  - 在演习结果未核查时草率定罪
  - 灾害发生后袖手旁观
  - 因个人情绪影响调令或救灾安排
voice_profile: *id001
---

负责组织演习评分、处理武器失踪调查并在灾害发生后迅速调动新兵救灾，是训练营纪律与守夜人职责的执行者。