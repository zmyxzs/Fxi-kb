---
entity_id: char_mofang
category: character
is_unique: true
name: 魔方
aliases:
- 错乱空间神秘
attributes:
  identity: 藏身于集训营三栋、操纵空间错乱的川境神秘，后被收容
  realm: 川境
  role: 重要反派
  trinity:
    mode: single_soul
    vessels:
      vessel_char_mofang:
        vessel_id: vessel_char_mofang
        name: 魔方
        status: alive
        location: ''
    souls:
      soul_char_mofang:
        soul_id: soul_char_mofang
        true_name: 魔方
        is_controller: true
    personas:
      persona_char_mofang_default:
        persona_id: persona_char_mofang_default
        display_name: 魔方
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_mofang
    active_soul_id: soul_char_mofang
    active_persona_id: persona_char_mofang_default
  abilities:
  - ability_id: skill_quanzidong_jixie
    name: 全自动生活辅助
    category: innate
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 收容后可操纵空间与水、肥皂等物品，自动完成洗衣、甩干、洗碗等工作，也能作为麻将机使用。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 9
  - ability_id: skill_kongdong_yidong
    name: 空间节点挪移
    category: combat
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 通过空间变化将目标区域或战斗双方分隔、转移；若目标进入其藏身空洞，则难以继续挪走目标。
    source_origin: ''
    metadata: &id002
      tier: 1
      batches:
      - 9
  - ability_id: skill_kongjian_cuoluan
    name: 空间错乱
    category: innate
    sequence_num: null
    valid_from_chapter: 161
    valid_to_chapter: null
    cost_description: ''
    description: 将房间、走廊和楼层重新拼接、挪移，使整栋建筑如同可旋转的魔方；能够根据规律改变空间布局并隐藏自身。
    source_origin: ''
    metadata: &id003
      tier: 1
      batches:
      - 9
phases:
- phase_id: phase_mofang_predator
  phase_name: 空间迷宫操纵者
  valid_from_order: 161
  valid_to_order: 172
  traits:
  - 隐蔽
  - 聪明
  - 擅长控制战场
  - 避免正面暴露
  anti_behaviors:
  - 在空间优势下主动暴露藏身处
  - 无规律地改变空间而不顾自身安全
  - 放弃隔离策略与敌人进行无意义正面搏杀
- phase_id: phase_mofang_housekeeper
  phase_name: 精神病院自动化护工
  valid_from_order: 173
  valid_to_order: 174
  traits:
  - 服从契约
  - 功能化
  - 高效
  - 缺乏主动攻击表现
  anti_behaviors:
  - 在收容后无故攻击院方人员
  - 拒绝执行已被安排的生活辅助工作
  - 继续主动制造三栋级别的空间杀局
abilities:
- ability_id: skill_quanzidong_jixie
  name: 全自动生活辅助
  category: innate
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 收容后可操纵空间与水、肥皂等物品，自动完成洗衣、甩干、洗碗等工作，也能作为麻将机使用。
  source_origin: ''
  metadata: *id001
- ability_id: skill_kongdong_yidong
  name: 空间节点挪移
  category: combat
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 通过空间变化将目标区域或战斗双方分隔、转移；若目标进入其藏身空洞，则难以继续挪走目标。
  source_origin: ''
  metadata: *id002
- ability_id: skill_kongjian_cuoluan
  name: 空间错乱
  category: innate
  sequence_num: null
  valid_from_chapter: 161
  valid_to_chapter: null
  cost_description: ''
  description: 将房间、走廊和楼层重新拼接、挪移，使整栋建筑如同可旋转的魔方；能够根据规律改变空间布局并隐藏自身。
  source_origin: ''
  metadata: *id003
---

智慧较高、擅长隐藏和操纵环境的空间类神秘。战斗中通过错乱空间隔离敌人，后被林七夜利用规律和空间空洞击败并收容，转化为精神病院的自动化劳务能力。