---
entity_id: char_rose
category: character
is_unique: true
name: 蔷薇
aliases: []
attributes:
  identity: 【假面】小队成员
  realm: 被压制至盏境
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_rose:
        vessel_id: vessel_char_rose
        name: 蔷薇
        status: alive
        location: ''
    souls:
      soul_char_rose:
        soul_id: soul_char_rose
        true_name: 蔷薇
        is_controller: true
    personas:
      persona_char_rose_default:
        persona_id: persona_char_rose_default
        display_name: 蔷薇
        speech_channel: physical_dialogue
        voice_profile: {}
    active_vessel_id: vessel_char_rose
    active_soul_id: soul_char_rose
    active_persona_id: persona_char_rose_default
  abilities:
  - ability_id: skill_rose_giant_hammer
    name: 巨锤化
    category: innate
    sequence_num: null
    valid_from_chapter: 61
    valid_to_chapter: null
    cost_description: ''
    description: 能够将手中锤子的尺寸扩大至近似居民楼大小，并以巨大冲击力攻击目标；体型扩大后仍可被其轻松举起。
    source_origin: ''
    metadata: &id001
      tier: 1
      batches:
      - 4
phases:
- phase_id: phase_rose_suppressive_combatant
  phase_name: 正面压制型战士
  valid_from_order: 73
  valid_to_order: 80
  traits:
  - 强悍
  - 直接
  - 执行力强
  - 压迫感
  anti_behaviors:
  - 不会在战斗中因人数劣势而轻易退缩
  - 不会以温和谈判替代已经开始的实战
  - 不会忽视队友的整体战术
abilities:
- ability_id: skill_rose_giant_hammer
  name: 巨锤化
  category: innate
  sequence_num: null
  valid_from_chapter: 61
  valid_to_chapter: null
  cost_description: ''
  description: 能够将手中锤子的尺寸扩大至近似居民楼大小，并以巨大冲击力攻击目标；体型扩大后仍可被其轻松举起。
  source_origin: ''
  metadata: *id001
---

战斗风格强悍直接，拥有极具压迫感的巨锤能力。面对新兵围攻时表现出强烈的战斗执行力。