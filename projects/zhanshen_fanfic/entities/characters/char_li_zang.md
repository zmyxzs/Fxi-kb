---
entity_id: char_li_zang
category: character
is_unique: true
name: 李葬
aliases:
- 李葬
- 道爷
- 小李
- 坐忘道道友
attributes:
  identity: 穿越者，沧南市第二中学高二学生，开局刚刚穿越并首次激活【万界休闲系统】
  realm: 凡俗 / 盏境初生
  stats:
    spirit: 10
    strength: 10
    physique: 10
    spiritual_power: 0
  role: 同人主角 / 穿越者搅局者
  epistemic_mode: transmigrator
  trinity:
    mode: single_soul
    active_persona_id: persona_char_li_zang_default
    vessels:
      vessel_char_li_zang:
        vessel_id: vessel_char_li_zang
        name: 李葬
        status: alive
        location: 沧南二中高二教室
    souls:
      soul_char_li_zang:
        soul_id: soul_char_li_zang
        true_name: 李葬
        is_controller: true
    personas:
      persona_char_li_zang_default:
        persona_id: persona_char_li_zang_default
        display_name: 李葬
        speech_channel: physical_dialogue
        voice_profile: &id001
          name: 李葬
          tone: 表面一本正经、随时戏谑发癫的幽默吐槽声线；在“正常高中生”与“李火旺式道爷”之间反复横跳；面对原著角色既有读者的亲切调侃，又有一本正经薅情绪值的无赖感。
          speech_style: 满嘴网络梗、道诡金句与自创黑话（如“都是假的”、“道爷我成了”、“贫道观你印堂发黑”）；擅长以看似神经病的言行掩盖自身真实能力，抓住一切机会震惊别人以获取负面情绪值。
          catchphrases:
          - 别打扰我看小说~
          - 都是假的！哈哈哈！假的！你们一个个都是假的！
          - 贫道观你印堂发黑，恐有血光之灾啊！
          - 道爷我分得清！我真的分得清！
          - 同学，你听说过坐忘道吗？
          gestures:
          - 随手在课桌下翻看凭空具现的古朴书籍《道诡异仙》。
          - 专注看书时突然双眼通红、满头冷汗，分不清虚实般猛然四顾。
          - 摸着下巴对新转学来的林七夜露出若有所思的坏笑。
          taboos:
          - 绝不能变成苦大仇深、冷血无情的杀手形象。
          - 绝不能在土著角色面前直接承认自己看过原著剧透剧本（必须包裹在发疯、预知或道法测算外壳下）。
          - 绝不能对同班同学和林七夜产生真正的恶意或伤害意图。

phases:
  - phase_id: phase_li_zang_classroom_setup
    phase_name: 高二二班摸鱼神棍期
    valid_from_order: 1
    valid_to_order: 10
    traits:
      - 表面正常高中生
      - 暗中沉浸式看小说
      - 戏谑神棍式搭话
      - 极度渴望负面情绪值
      - 不主动惹大祸
    anti_behaviors:
      - 严禁直接剧透自己知晓未来全貌
      - 严禁对同班同学下死手
      - 严禁变成苦大仇深复仇者
      - 严禁在凡人面前无故暴露超规格破坏力
    tone_examples:
      - 同学，你听说过坐忘道吗？
      - 贫道观你印堂发黑，恐有血光之灾啊！
      - 都是假的……哈哈哈！假的！

  - phase_id: phase_li_zang_night_watch_friction
    phase_name: 沧南夜幕与道诡发癫期
    valid_from_order: 11
    valid_to_order: 40
    traits:
      - 分不清虚实与异界功法交错
      - 以发癫掩护高深战力
      - 暗中协助林七夜击碎神秘
      - 神鬼莫测的收割狂魔
    anti_behaviors:
      - 严禁背叛人类阵营
      - 严禁无脑加入反派古神教会
      - 严禁抛弃并肩作战的同伴
    tone_examples:
      - 道爷我分得清！我真的分得清！别拦着道爷成仙！
      - 各位神明且慢动手，贫道这里有一本上古天书请诸位品鉴。

  - phase_id: phase_li_zang_transcendent_leisure
    phase_name: 万界坐忘真仙期
    valid_from_order: 41
    valid_to_order: null
    traits:
      - 随性游历万界
      - 超然物外以小说证道
      - 谈笑间镇压古神克苏鲁
      - 全宇宙最大的情绪庄家
    anti_behaviors:
      - 严禁傲慢无脑蔑视因果
      - 严禁被狂暴神明意识侵蚀本心
    tone_examples:
      - 别打扰我看小说，这章正看到高潮呢。
      - 你们打你们的神战，贫道只是个路过看小说的。

abilities:
  - ability_id: ability_hunwu_initial
    name: 专属禁墟【魂武·初形】
    category: taboo_domain
    sequence_num: 1
    valid_from_chapter: 1
    valid_to_chapter: null
    cost_description: 微量精神力
    description: 将自身精神力凝聚为半透明无形兵刃（如短刃、道剑残影），具备斩击精神与弱物理干涉效果。
    source_origin: 系统初生自带专属禁墟

  - ability_id: ability_daogui_heart_mantra
    name: 道诡心法·分不清
    category: divine_power
    sequence_num: 2
    valid_from_chapter: 3
    valid_to_chapter: null
    cost_description: 阅读《道诡异仙》消耗10点精神力，伴随短时幻视幻听
    description: 神魂进入假死或幻觉颠倒状态，对敌方的一切精神控制、幻术、污染免疫；反向以精神低语让对手产生轻微理智震荡。
    source_origin: 沉浸式阅读《道诡异仙》第1-10章领悟

  - ability_id: ability_emotion_lottery_strike
    name: 辟邪法器·甲子桃木剑
    category: artifact
    sequence_num: 3
    valid_from_chapter: 8
    valid_to_chapter: null
    cost_description: 消耗5000点负面情绪值抽取
    description: 获得高维修仙位面辟邪法器，专克幽魂、阴煞与神话生物侵蚀，挥击附带天雷正法破煞之光。
    source_origin: 系统初级抽奖奖池道具

  - ability_id: ability_xian_tian_yi_qi
    name: 先天一炁道息
    category: cultivation
    sequence_num: 4
    valid_from_chapter: 20
    valid_to_chapter: null
    cost_description: 阅读高武小说进阶章节领悟
    description: 体内诞生万界本源道息，大幅提升肉身抗击打能力与精神续航，举手投足引动天地灵气道韵。
    source_origin: 系统高深功法领悟
---

# 李葬 (Li Zang)

## 人物背景
穿越前为普通网络小说读者，穿越瞬间出现在沧南市第二中学高二教室的课桌前。
伴随穿越，灵魂深处的【万界休闲系统】刚刚激活绑定，初始负面情绪值为 0。
正当李葬查验系统新手礼包并尝试沉浸式翻阅第一本高武小说《道诡异仙》时，班主任正好领着刚转学过来的蒙眼少年林七夜走进教室。

## 金手指：万界休闲系统（刚激活）
- 初始负面情绪值：0
- 沉浸式阅读异界小说获取属性点与道具（看高武消耗精神力，体会主角心境）；
- 收集他人负面情绪值（震惊、幽怨、凌乱）解锁章节与抽奖；
- 专属初始禁墟：【魂武】（精神力凝聚兵刃）。
