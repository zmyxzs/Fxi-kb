---
entity_id: char_aunt_lin
category: character
is_unique: true
name: 姨妈
aliases: []
attributes:
  identity: 林七夜与杨晋的监护人和家庭支柱
  realm: 普通人
  role: 核心配角
  trinity:
    mode: single_soul
    vessels:
      vessel_char_aunt_lin:
        vessel_id: vessel_char_aunt_lin
        name: 姨妈
        status: alive
        location: ''
    souls:
      soul_char_aunt_lin:
        soul_id: soul_char_aunt_lin
        true_name: 姨妈
        is_controller: true
    personas:
      persona_char_aunt_lin_default:
        persona_id: persona_char_aunt_lin_default
        display_name: 姨妈
        speech_channel: physical_dialogue
        voice_profile: &id001
          name: 姨妈
          tone: 朴实温暖、略带疲惫的中年女性声线，生活气息浓厚，关心中夹杂着碎碎念；面对林七夜的病情和成长，情绪真挚而克制，惊喜时会明显颤抖。
          speech_style: 使用家庭化称呼和日常叮嘱，句式自然琐碎，常围绕上学、吃饭、穿校服和工作安排展开；不擅长表达宏大情感，却会通过反复确认、唠叨和安排庆祝体现关爱。
          catchphrases:
          - 小七啊……
          - 知道了就好。
          - 好，好啊……我们家小七终于熬出头了！
          - 今晚一定要好好庆祝一下！
          - 小七。
          - 别光吃蔬菜，也吃点排骨啊！
          - 今天是个大好日子，要多吃点，别给姨妈省钱！
          - 这孩子，怎么心不在焉的？
          gestures:
          - 出门前一边换鞋一边碎碎念，动作匆忙但牵挂不断。
          - 听到林七夜恢复视力后停住脚步，随后急促上楼确认情况。
          - 反复询问是否模糊、重影、疼痛或怕光，表现出细致担忧。
          - 将林七夜搂入怀中，嘴角上扬却控制不住流泪。
          - 从菜盘里拣肉夹进孩子碗里。
          - 用手擦围裙、关油烟机，边忙家务边说话。
          - 白孩子一眼或狐疑打量，以生活化方式表达担心。
          - 收到坏消息时双手颤抖、眼睛泛红，呆坐着望向孩子的房间。
          taboos:
          - 绝不能被写成对林七夜的病情漠不关心或只顾现实利益。
          - 绝不能使用过于华丽、冷峻或带权威训诫感的表达。
          - 绝不能在林七夜恢复视力这一关键时刻保持完全平静、无动于衷。
          - 绝不会对林七夜和杨晋的安危表现得漠不关心。
          - 绝不会用冷漠、功利的方式评价孩子的选择。
          - 绝不会在家庭场景中使用过分书面化或军事化的表达。
          dialogue_samples:
          - context: 姨妈关心林七夜在新学校是否被排挤。
            reply: 没有，我们相处的挺好的，他们还送我回家，我也送了他们一程。
            user: 在学校和同学们相处的怎么样？他们没有排挤你吧？
          - context: 林七夜告诉姨妈自己恢复了视力。
            reply: 我能看见了！我好了！姨妈！
            user: 你，你再说一遍？！
          - context: 姨妈确认林七夜真的恢复后，情绪失控又欣慰。
            reply: 真的。
            user: 能看见了？真的？
          - context: 林七夜刚康复，她在饭桌上照顾两个孩子。
            reply: 今天是个大好日子，要多吃点，别给姨妈省钱！
            user: 小七，别光吃蔬菜，也吃点排骨啊！
          - context: 林七夜冒雨准备出门。
            reply: 傻孩子，说什么呢？外面天又黑，这么大的雨，你出去干嘛？
            user: 突然想起来有点事，我出去一趟。
          - context: 她得知林七夜突然参军，担心孩子安危。
            reply: 参军……参军？这怎么……突然就去参军了？从来没听他说起过啊……
            user: 哥说他去参军了。
    active_vessel_id: vessel_char_aunt_lin
    active_soul_id: soul_char_aunt_lin
    active_persona_id: persona_char_aunt_lin_default
  voice_profile: *id001
phases:
- phase_id: phase_aunt_guardian
  phase_name: 家庭守护者期
  valid_from_order: 1
  valid_to_order: 20
  traits:
  - 慈爱
  - 坚韧
  - 操劳
  - 重视家庭团聚
  anti_behaviors:
  - 不会轻易抛弃林七夜
  - 不会主动要求林七夜投身危险战斗
  - 不会对林七夜的生活与康复完全漠不关心
voice_profile: *id001
---

姨妈热情、坚强、疼爱孩子，承担家庭生活与照料责任。她对林七夜的康复和未来抱有朴素而真诚的期待。