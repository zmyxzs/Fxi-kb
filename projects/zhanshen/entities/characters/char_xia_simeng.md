---
entity_id: char_xia_simeng
category: character
is_unique: true
name: 夏思萌
aliases:
- 队长
attributes:
  identity: 凤凰小队队长
  realm: 人类顶尖战力，具体境界未明
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_xia_simeng:
        vessel_id: vessel_char_xia_simeng
        name: 夏思萌
        status: alive
        location: ''
    souls:
      soul_char_xia_simeng:
        soul_id: soul_char_xia_simeng
        true_name: 夏思萌
        is_controller: true
    personas:
      persona_char_xia_simeng_default:
        persona_id: persona_char_xia_simeng_default
        display_name: 夏思萌
        speech_channel: physical_dialogue
        voice_profile: &id001
          name: 夏思萌
          tone: 爽朗张扬、热血豪迈的女队长声线；平时疯疯癫癫、行动大胆，面对城市危机时迅速收敛玩笑，展现果断与担当。
          speech_style: 语速偏快，爱用夸张直白的表达和大笑；命令下属时简短有力，做出牺牲决定时庄重肃然。
          catchphrases:
          - 老娘来掳走你啦！
          - 不要反抗！乖乖加入我们【凤凰】小队吧！
          - 先救人。
          - 可愿与我……殊死一搏？
          - 怎么说？
          - 由【凤凰】小队负责。
          - 他的灵魂稳固下来了。
          - 千万不要做出可能会刺激他的事情。
          gestures:
          - 从高空一边下坠一边狂笑，毫不顾忌形象。
          - 危机出现时眉头紧皱，立刻转为认真判断。
          - 回头点名队员或下达命令，动作干脆。
          - 面对必死战局时闭上双眼，随后庄重发令。
          - 皱眉查看手机或检测结果，迅速进入指挥状态。
          - 紧咬嘴唇，担忧时仍保持克制。
          - 蹲下扶起林七夜，轻声询问其意图。
          - 面对强者时紧盯对方，保持戒备而不贸然出手。
          taboos:
          - 不能长期保持阴郁寡言、缺乏行动力的状态。
          - 不能在队友和城市遇险时继续无底线胡闹。
          - 不能把牺牲说成个人英雄主义炫耀，核心应是共同使命。
          - 不能在任务现场因恐惧而失去判断。
          - 不能用戏谑口吻对待林七夜的精神伤势。
          - 不能无视禁墟暴走风险或擅自刺激患者。
          dialogue_samples:
          - context: 她带领凤凰小队从运输机跳下，准备寻找林七夜。
            reply: 林七夜！老娘来掳走你啦！不要反抗！乖乖加入我们【凤凰】小队吧！！
            user: 你们是谁？
          - context: 发现沧南正在遭遇神明与巨兽袭击，她立即改变行动目标。
            reply: 先救人。
            user: 队长，你找林七夜的事情，估计得先放一放了。
          - context: 凤凰小队准备以生命为代价围杀加姆。
            reply: 可愿与我……殊死一搏？
            user: 这一天，终究还是来了么……
          - context: 孔伤得知凤凰小队要负责押送林七夜。
            reply: 太隆重了？不，一点也不隆重，林七夜的潜力太大了……
            user: 让我们来负责运送林七夜？这会不会……
          - context: 孔伤询问林七夜的灵魂状态。
            reply: 他的灵魂稳固下来了。
            user: 怎么样？
    active_vessel_id: vessel_char_xia_simeng
    active_soul_id: soul_char_xia_simeng
    active_persona_id: persona_char_xia_simeng_default
  voice_profile: *id001
phases:
- phase_id: phase_xiasimeng_city_defender
  phase_name: 死守沧南
  valid_from_order: 251
  valid_to_order: 258
  traits:
  - 果断
  - 坚毅
  - 守责
  - 不畏牺牲
  anti_behaviors:
  - 不会因胜算不足而抛弃城市
  - 不会让队员在无意义的情况下牺牲
voice_profile: *id001
---

战场指挥坚定果断，面对三只神话巨兽仍选择分兵死守沧南，体现守夜人的牺牲精神与责任担当。