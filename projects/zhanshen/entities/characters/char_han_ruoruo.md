---
entity_id: char_han_ruoruo
category: character
is_unique: true
name: 韩若若
aliases:
- 难陀蛇妖
attributes:
  identity: 二中女生，难陀蛇妖在人类社会中的伪装身份与本体
  realm: 神话生物，具体境界未明；攻击力和防御力高于普通感染怪物
  role: 反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_han_ruoruo:
        vessel_id: vessel_char_han_ruoruo
        name: 韩若若
        status: alive
        location: ''
    souls:
      soul_char_han_ruoruo:
        soul_id: soul_char_han_ruoruo
        true_name: 韩若若
        is_controller: true
    personas:
      persona_char_han_ruoruo_default:
        persona_id: persona_char_han_ruoruo_default
        display_name: 韩若若
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_han_ruoruo
    active_soul_id: soul_char_han_ruoruo
    active_persona_id: persona_char_han_ruoruo_default
  abilities:
  - ability_id: skill_nantuo_sheyao_disguise
    name: 完美伪装与性格复刻
    category: innate
    sequence_num: null
    valid_from_chapter: 41
    valid_to_chapter: null
    cost_description: ''
    description: 能够复刻人类的外貌、性格与行为习惯，使目标难以辨认真伪；伪装具有高度逻辑性和欺骗性。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 3
  - ability_id: skill_nantuo_sheyao_infection
    name: 蛇种感染与繁衍
    category: innate
    sequence_num: null
    valid_from_chapter: 41
    valid_to_chapter: null
    cost_description: ''
    description: 通过培养和传播蛇种感染人类，形成大量具有怪物形态的子嗣；繁衍是其种族本能。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 3
  - ability_id: skill_nantuo_sheyao_serpent_form
    name: 难陀蛇妖本体
    category: combat
    sequence_num: null
    valid_from_chapter: 41
    valid_to_chapter: null
    cost_description: ''
    description: 显现为拥有黑色鳞片、利爪和粗壮蛇尾的怪物形态，近战攻击力强，具备一定防御力与高速爬行能力。
    source_origin: ''
    metadata: &id003
      tier: 1
      batches:
      - 3
phases:
- phase_id: phase_han_ruoruo_disguise
  phase_name: 校园伪装与扩散期
  valid_from_order: 43
  valid_to_order: 58
  traits:
  - 狡猾
  - 高智力
  - 善于伪装
  - 隐蔽扩散
  anti_behaviors:
  - 不会轻易以本体形态公开现身
  - 不会采用毫无逻辑的随机感染方式
  - 不会因短期局部暴露就放弃利用人类身份
- phase_id: phase_nantuo_sheyao_exposure
  phase_name: 本体暴露与正面战斗期
  valid_from_order: 59
  valid_to_order: 60
  traits:
  - 冷酷
  - 攻击性强
  - 果断逃遁
  - 本能驱动
  anti_behaviors:
  - 不会在本体暴露后继续维持脆弱的人类伪装
  - 不会坐以待毙而不尝试逃入建筑和复杂地形
  - 不会因子嗣受损就放弃自身生存
abilities:
- ability_id: skill_nantuo_sheyao_disguise
  name: 完美伪装与性格复刻
  category: innate
  sequence_num: null
  valid_from_chapter: 41
  valid_to_chapter: null
  cost_description: ''
  description: 能够复刻人类的外貌、性格与行为习惯，使目标难以辨认真伪；伪装具有高度逻辑性和欺骗性。
  source_origin: ''
  metadata: *id001
- ability_id: skill_nantuo_sheyao_infection
  name: 蛇种感染与繁衍
  category: innate
  sequence_num: null
  valid_from_chapter: 41
  valid_to_chapter: null
  cost_description: ''
  description: 通过培养和传播蛇种感染人类，形成大量具有怪物形态的子嗣；繁衍是其种族本能。
  source_origin: ''
  metadata: *id002
- ability_id: skill_nantuo_sheyao_serpent_form
  name: 难陀蛇妖本体
  category: combat
  sequence_num: null
  valid_from_chapter: 41
  valid_to_chapter: null
  cost_description: ''
  description: 显现为拥有黑色鳞片、利爪和粗壮蛇尾的怪物形态，近战攻击力强，具备一定防御力与高速爬行能力。
  source_origin: ''
  metadata: *id003
---

难陀蛇妖拥有极高的伪装智慧，长期以韩若若的身份潜伏并感染他人。它将繁衍视为种族本能，不甘于在人类社会中低调生存，表现出冷漠、狡猾和强烈的掠夺性。