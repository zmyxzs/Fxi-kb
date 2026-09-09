---
entity_id: char_li_yaoguang
category: character
is_unique: true
name: 李耀光
aliases: []
attributes:
  identity: 集训营内部暗桩，被敌对势力以父母为人质胁迫
  realm: 文本未明确
  role: 配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_li_yaoguang:
        vessel_id: vessel_char_li_yaoguang
        name: 李耀光
        status: alive
        location: ''
    souls:
      soul_char_li_yaoguang:
        soul_id: soul_char_li_yaoguang
        true_name: 李耀光
        is_controller: true
    personas:
      persona_char_li_yaoguang_default:
        persona_id: persona_char_li_yaoguang_default
        display_name: 李耀光
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_li_yaoguang
    active_soul_id: soul_char_li_yaoguang
    active_persona_id: persona_char_li_yaoguang_default
phases:
- phase_id: phase_li_yaoguang_coerced_informant
  phase_name: 被胁迫的暗桩
  valid_from_order: 134
  valid_to_order: 134
  traits:
  - 恐惧
  - 愧疚
  - 被迫
  - 心理崩溃
  anti_behaviors:
  - 不会把出卖集训营当作个人荣耀
  - 不会在父母安全受到威胁时表现出毫不在意
  - 不会被塑造成主动策划袭击的核心首谋
---

并非出于主动背叛，而是在父母被控制后被迫传递加密信息和协助敌方；面对审讯时崩溃痛哭，表现出恐惧、愧疚与无力。