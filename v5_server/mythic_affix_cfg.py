"""
v5_server/mythic_affix_cfg.py
黑区净化 (Mythic) 与最高难度Ω/失序深区 (Mythic Final) 全量词条库、Boss机制与半周动态轮换服务。
"""

import time
import datetime

# ----------------- 1. 全量词条数据库 -----------------

AFFIX_DATABASE = {
    # --- [A] 终焉难度：环境复合有利词缀 (双属性增伤/抗性削减) ---
    441: {"id": 441, "name": "复合有利·物冰", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到物理和冰属性以外的属性伤害衰减", "races": [1, 4]}, # 奥山, 圣树
    442: {"id": 442, "name": "复合有利·火水", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到火和水属性以外的属性伤害衰减", "races": [2, 3]}, # 尼罗, 真樱
    443: {"id": 443, "name": "复合有利·雷风", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到雷和风属性以外的属性伤害衰减", "races": [3, 5]}, # 真樱, 众星
    444: {"id": 444, "name": "复合有利·光暗", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到光和暗属性以外的属性伤害衰减", "races": [1, 4]}, # 奥山, 圣树
    445: {"id": 445, "name": "复合有利·冰光", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到冰和光属性以外的属性伤害衰减", "races": [4, 5]}, # 圣树, 众星
    446: {"id": 446, "name": "复合有利·物暗", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到物理和暗属性以外的属性伤害衰减", "races": [1, 9]}, # 奥山, 天垣
    447: {"id": 447, "name": "复合有利·物风", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到物理和风属性以外的属性伤害衰减", "races": [3, 9]}, # 真樱, 天垣
    448: {"id": 448, "name": "复合有利·水冰", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到水和冰属性以外的属性伤害衰减", "races": [2, 4]}, # 尼罗, 圣树
    449: {"id": 449, "name": "复合有利·火雷", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到火和雷属性以外的属性伤害衰减", "races": [3, 5]}, # 真樱, 众星
    451: {"id": 451, "name": "复合有利·火暗", "max_level": 20, "default_level": 7, "type": 0, "desc": "敌方单位受到火和暗属性以外的属性伤害衰减", "races": [2, 9]}, # 尼罗, 天垣

    # --- [B] 终焉难度：主控专属强化词缀 ---
    9691: {"id": 9691, "name": "神能强化", "max_level": 1, "default_level": 1, "type": 0, "desc": "每过20秒，修正者回复全部神能"},
    9692: {"id": 9692, "name": "输出强化", "max_level": 1, "default_level": 1, "type": 0, "desc": "主控修正者造成的伤害提高25%"},
    9693: {"id": 9693, "name": "防御强化", "max_level": 1, "default_level": 1, "type": 0, "desc": "主控修正者的防御力提高50%"},

    # --- [C] 终焉难度：全队通用机制词缀 ---
    9681: {"id": 9681, "name": "移速强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "我方移动速度提高25%"},
    9682: {"id": 9682, "name": "回复强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "我方每3秒恢复1%的生命值"},
    9683: {"id": 9683, "name": "闪避强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "我方闪避值上限提高20%"},
    9684: {"id": 9684, "name": "队友强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "队友造成的非会心伤害提高25%"},
    9685: {"id": 9685, "name": "防御强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "我方防御力提高25%"},
    9686: {"id": 9686, "name": "零时强化", "max_level": 1, "default_level": 1, "type": 3, "desc": "主控修正者触发的零时空间时间延长1秒"},

    # --- [D] 终焉难度：14组 Boss 专属挑战机制三件套 (生存/伤害/特殊) ---
    9601: {"id": 9601, "name": "生存强化·塞壬", "max_level": 1, "default_level": 1, "type": 0, "desc": "塞壬生命上限+25%，防御+75%"},
    9602: {"id": 9602, "name": "伤害强化·塞壬", "max_level": 1, "default_level": 1, "type": 0, "desc": "塞壬伤害+50%，命中后附加持续灼烧"},
    9603: {"id": 9603, "name": "特殊强化·塞壬", "max_level": 1, "default_level": 1, "type": 0, "desc": "塞壬命中后免疫队友伤害5秒"},

    9604: {"id": 9604, "name": "生存强化·拉冬", "max_level": 1, "default_level": 1, "type": 0, "desc": "拉冬生命上限+25%，二阶段获得护盾"},
    9605: {"id": 9605, "name": "伤害强化·拉冬", "max_level": 1, "default_level": 1, "type": 0, "desc": "拉冬伤害+50%，对护盾目标伤害+100%"},
    9606: {"id": 9606, "name": "特殊强化·拉冬", "max_level": 1, "default_level": 1, "type": 0, "desc": "拉冬修正值随时间持续流逝降低"},

    9607: {"id": 9607, "name": "生存强化·乌尔德", "max_level": 1, "default_level": 1, "type": 0, "desc": "乌尔德生命上限+25%，命中吸血"},
    9608: {"id": 9608, "name": "伤害强化·乌尔德", "max_level": 1, "default_level": 1, "type": 0, "desc": "乌尔德伤害+50%，命中后增伤75%持续5秒"},
    9609: {"id": 9609, "name": "特殊强化·乌尔德", "max_level": 1, "default_level": 1, "type": 0, "desc": "乌尔德命中后获得自身护盾"},

    9610: {"id": 9610, "name": "生存强化·明塔琉刻", "max_level": 1, "default_level": 1, "type": 0, "desc": "明塔与琉刻生命上限+25%，命中加防"},
    9611: {"id": 9611, "name": "伤害强化·明塔琉刻", "max_level": 1, "default_level": 1, "type": 0, "desc": "明塔与琉刻伤害+50%，命中受创持续流血"},
    9612: {"id": 9612, "name": "特殊强化·明塔琉刻", "max_level": 1, "default_level": 1, "type": 0, "desc": "击败一人后另一人回满血并增伤75%"},

    9613: {"id": 9613, "name": "生存强化·比弗隆斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "比弗隆斯生命上限+25%，大眼球出场获盾"},
    9614: {"id": 9614, "name": "伤害强化·比弗隆斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "比弗隆斯伤害+50%，大眼球存活额外增伤75%"},
    9615: {"id": 9615, "name": "特殊强化·比弗隆斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "生命低于20%时对主控伤害提高300%"},

    9616: {"id": 9616, "name": "生存强化·远吕知", "max_level": 1, "default_level": 1, "type": 0, "desc": "远吕知生命上限提高50%"},
    9617: {"id": 9617, "name": "伤害强化·远吕知", "max_level": 1, "default_level": 1, "type": 0, "desc": "远吕知伤害+50%，命中后增伤75%"},
    9618: {"id": 9618, "name": "特殊强化·远吕知", "max_level": 1, "default_level": 1, "type": 0, "desc": "一定距离外修正者攻击力降低100%"},

    9619: {"id": 9619, "name": "生存强化·歌姬鲸鱼", "max_level": 1, "default_level": 1, "type": 0, "desc": "歌姬生命+25%，鲸鱼生命+100%"},
    9620: {"id": 9620, "name": "伤害强化·歌姬鲸鱼", "max_level": 1, "default_level": 1, "type": 0, "desc": "歌姬伤害+50%，命中后增伤75%"},
    9621: {"id": 9621, "name": "特殊强化·歌姬鲸鱼", "max_level": 1, "default_level": 1, "type": 0, "desc": "命中主控后削减60%闪避值"},

    9622: {"id": 9622, "name": "生存强化·哈法斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "哈法斯生命上限+25%，命中降低自身修正值"},
    9623: {"id": 9623, "name": "伤害强化·哈法斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "哈法斯伤害+50%，命中眩晕3秒(CD10s)"},
    9624: {"id": 9624, "name": "特殊强化·哈法斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "哈法斯免疫非会心伤害"},

    9625: {"id": 9625, "name": "生存强化·战骸", "max_level": 1, "default_level": 1, "type": 0, "desc": "战骸生命上限+25%，获得霸体"},
    9626: {"id": 9626, "name": "伤害强化·战骸", "max_level": 1, "default_level": 1, "type": 0, "desc": "战骸伤害+50%，命中后增伤75%"},
    9627: {"id": 9627, "name": "特殊强化·战骸", "max_level": 1, "default_level": 1, "type": 0, "desc": "命中主控后伤害永久叠加+15%"},

    9628: {"id": 9628, "name": "生存强化·净化者", "max_level": 1, "default_level": 1, "type": 0, "desc": "净化者生命上限+25%，防御+75%"},
    9629: {"id": 9629, "name": "伤害强化·净化者", "max_level": 1, "default_level": 1, "type": 0, "desc": "净化者伤害+50%，命中后增伤75%"},
    9630: {"id": 9630, "name": "特殊强化·净化者", "max_level": 1, "default_level": 1, "type": 0, "desc": "净化者命中主控后吸血5%"},

    9631: {"id": 9631, "name": "生存强化·弥诺陶洛斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "弥诺陶洛斯生命上限+25%，命中吸血"},
    9632: {"id": 9632, "name": "伤害强化·弥诺陶洛斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "弥诺陶洛斯伤害+50%，命中后增伤75%"},
    9633: {"id": 9633, "name": "特殊强化·弥诺陶洛斯", "max_level": 1, "default_level": 1, "type": 0, "desc": "弥诺陶洛斯免疫非会心伤害"},

    9634: {"id": 9634, "name": "生存强化·自律机甲", "max_level": 1, "default_level": 1, "type": 0, "desc": "自律机甲生命上限+25%，获得25%护盾"},
    9635: {"id": 9635, "name": "伤害强化·自律机甲", "max_level": 1, "default_level": 1, "type": 0, "desc": "自律机甲伤害+50%，命中眩晕3秒"},
    9636: {"id": 9636, "name": "特殊强化·自律机甲", "max_level": 1, "default_level": 1, "type": 0, "desc": "自律机甲修正值随时间流逝降低"},

    9637: {"id": 9637, "name": "生存强化·苍梅", "max_level": 1, "default_level": 1, "type": 0, "desc": "苍梅生命上限+25%，获得25%护盾"},
    9638: {"id": 9638, "name": "伤害强化·苍梅", "max_level": 1, "default_level": 1, "type": 0, "desc": "苍梅伤害+50%，命中后增伤75%"},
    9639: {"id": 9639, "name": "特殊强化·苍梅", "max_level": 1, "default_level": 1, "type": 0, "desc": "苍梅命中削减60%闪避值"},

    9640: {"id": 9640, "name": "生存强化·蹈火者", "max_level": 1, "default_level": 1, "type": 0, "desc": "蹈火者生命上限+25%，获得霸体"},
    9641: {"id": 9641, "name": "伤害强化·蹈火者", "max_level": 1, "default_level": 1, "type": 0, "desc": "蹈火者伤害+50%，命中后增伤75%"},
    9642: {"id": 9642, "name": "特殊强化·蹈火者", "max_level": 1, "default_level": 1, "type": 0, "desc": "蹈火者召唤大量狂信徒协助战斗"},

    # --- [E] 常规黑区：单属性增伤词缀 (ID 411 ~ 418) ---
    411: {"id": 411, "name": "物理增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的物理属性抗性降低"},
    412: {"id": 412, "name": "火增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的火属性抗性降低"},
    413: {"id": 413, "name": "水增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的水属性抗性降低"},
    414: {"id": 414, "name": "冰增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的冰属性抗性降低"},
    415: {"id": 415, "name": "雷增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的雷属性抗性降低"},
    416: {"id": 416, "name": "风增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的风属性抗性降低"},
    417: {"id": 417, "name": "光增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的光属性抗性降低"},
    418: {"id": 418, "name": "暗增伤", "max_level": 40, "default_level": 10, "type": 0, "desc": "敌方单位的暗属性抗性降低"},

    # --- [F] 常规黑区：我方强化与机制 Buff (170~180) ---
    177: {"id": 177, "name": "魅影", "max_level": 1, "default_level": 1, "type": 3, "desc": "修正者闪避消耗的能量减少25%"},
    178: {"id": 178, "name": "狂暴", "max_level": 3, "default_level": 1, "type": 3, "desc": "修正者会心率提高"},
    179: {"id": 179, "name": "极意", "max_level": 1, "default_level": 1, "type": 3, "desc": "修正者怒气获得率提高50%"},
    180: {"id": 180, "name": "超频", "max_level": 1, "default_level": 1, "type": 3, "desc": "修正者能量获得率提高50%"},

    # --- [G] 常规黑区：劣势与评分修正 (121, 228~231) ---
    121: {"id": 121, "name": "强击", "max_level": 4, "default_level": 3, "type": 0, "desc": "敌方攻击力提升"},
    228: {"id": 228, "name": "逆境", "max_level": 1, "default_level": 1, "type": 3, "desc": "修正等级达到Ω级时伤害+75%；未达到时伤害-30%"},
    229: {"id": 229, "name": "怯战", "max_level": 1, "default_level": 1, "type": 3, "desc": "评分值获得率降低30%"},
    230: {"id": 230, "name": "斗志", "max_level": 1, "default_level": 1, "type": 3, "desc": "评分值获得率提高30%"},
    231: {"id": 231, "name": "鏖战", "max_level": 1, "default_level": 1, "type": 3, "desc": "修正模式内伤害提高150%，非修正模式伤害降低50%"}
}

# 14 组 Boss 轮换表
BOSS_PROFILES = [
    {"name": "塞壬", "affixes": [9601, 9602, 9603]},
    {"name": "拉冬", "affixes": [9604, 9605, 9606]},
    {"name": "乌尔德", "affixes": [9607, 9608, 9609]},
    {"name": "明塔与琉刻", "affixes": [9610, 9611, 9612]},
    {"name": "比弗隆斯", "affixes": [9613, 9614, 9615]},
    {"name": "远吕知", "affixes": [9616, 9617, 9618]},
    {"name": "歌姬与鲸鱼", "affixes": [9619, 9620, 9621]},
    {"name": "哈法斯", "affixes": [9622, 9623, 9624]},
    {"name": "战骸", "affixes": [9625, 9626, 9627]},
    {"name": "净化者", "affixes": [9628, 9629, 9630]},
    {"name": "弥诺陶洛斯", "affixes": [9631, 9632, 9633]},
    {"name": "自律机甲", "affixes": [9634, 9635, 9636]},
    {"name": "苍梅", "affixes": [9637, 9638, 9639]},
    {"name": "蹈火者", "affixes": [9640, 9641, 9642]}
]

# 10 组环境复合有利轮换池
COMPOSITE_ADVANTAGE_POOL = [444, 441, 442, 443, 445, 446, 447, 448, 449, 451]
HOST_BUFF_POOL = [9692, 9691, 9693] # 输出 / 神能 / 防御
TEAM_BUFF_POOL = [9682, 9686, 9683, 9681, 9684, 9685] # 回复 / 零时 / 闪避 / 移速 / 队友 / 防御

# 常规黑区单属性增伤对
REGULAR_SUPERIORITY_POOL = [
    [417, 412, 178], # 光 + 火 + 会心 (奥山/真樱)
    [411, 414, 180], # 物 + 冰 + 能量 (奥山/圣树)
    [413, 415, 179], # 水 + 雷 + 怒气 (尼罗/真樱)
    [416, 418, 177], # 风 + 暗 + 闪避 (众星/天垣)
    [412, 415, 178], # 火 + 雷 + 会心 (真樱/众星)
    [414, 417, 180], # 冰 + 光 + 能量 (圣树/众星)
    [411, 416, 179], # 物 + 风 + 怒气 (真樱/天垣)
    [413, 418, 178]  # 水 + 暗 + 会心 (尼罗/天垣)
]


# ----------------- 2. 半周轮换计算函数 -----------------

def get_semiweekly_cycle_info(ts=None):
    """
    计算半周周期序号与下一次周一/周四早 05:00 时间戳。
    基准时间：2026-08-24 05:00:00 (周一早5点) -> cycle_id = 0
    """
    if ts is None:
        ts = int(time.time())
        
    dt = datetime.datetime.fromtimestamp(ts)
    
    # 找到最近的过去周一或周四早 5:00
    # 星期一=0, 星期二=1, 星期三=2, 星期四=3, 星期五=4, 星期六=5, 星期日=6
    weekday = dt.weekday()
    hour = dt.hour
    
    # 计算当前半周期的起始时间
    if weekday < 3 or (weekday == 3 and hour < 5):
        # 属于 周一 05:00 ~ 周四 04:59:59 周期
        days_since_monday = weekday
        monday_5am = dt.replace(hour=5, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_since_monday)
        if weekday == 0 and hour < 5:
            monday_5am -= datetime.timedelta(days=7)
        period_start = monday_5am
        # 下次刷新是周四 05:00
        next_refresh = monday_5am + datetime.timedelta(days=3)
    else:
        # 属于 周四 05:00 ~ 下周一 04:59:59 周期
        days_since_thursday = weekday - 3
        thursday_5am = dt.replace(hour=5, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_since_thursday)
        if weekday == 3 and hour < 5:
            thursday_5am -= datetime.timedelta(days=3.5) # rollback to previous
        period_start = thursday_5am
        # 下次刷新是下周一 05:00
        next_refresh = thursday_5am + datetime.timedelta(days=4)
        
    # 计算周期序号 (基于 2026-01-01)
    base_ts = 1767214800 # 2026-01-01 05:00
    cycle_id = int(max(0, period_start.timestamp() - base_ts) // (3.5 * 86400))
    next_refresh_ts = int(next_refresh.timestamp())
    
    return cycle_id, next_refresh_ts


def get_mythic_rotation(ts=None):
    """
    返回当期黑区净化全部轮换配置：
    - superiority_affixes
    - inferiority_affixes
    - ultimate_affixes (难度Ω失序深区)
    - recommend_team
    - next_refresh_ts
    - boss_profile
    """
    cycle_id, next_rf = get_semiweekly_cycle_info(ts)
    
    # 1. 终焉难度 (难度Ω) 词缀：复合有利 + 主控 + 通用
    comp_aff_id = COMPOSITE_ADVANTAGE_POOL[cycle_id % len(COMPOSITE_ADVANTAGE_POOL)]
    host_aff_id = HOST_BUFF_POOL[cycle_id % len(HOST_BUFF_POOL)]
    team_aff_id = TEAM_BUFF_POOL[cycle_id % len(TEAM_BUFF_POOL)]
    
    comp_info = AFFIX_DATABASE.get(comp_aff_id, {"max_level": 20, "default_level": 7, "type": 0, "races": [1, 4]})
    host_info = AFFIX_DATABASE.get(host_aff_id, {"max_level": 1, "default_level": 1, "type": 0})
    team_info = AFFIX_DATABASE.get(team_aff_id, {"max_level": 1, "default_level": 1, "type": 3})
    
    ultimate_affixes = [
        {"id": comp_aff_id, "level": comp_info.get("default_level", 7), "type": comp_info.get("type", 0)},
        {"id": host_aff_id, "level": host_info.get("default_level", 1), "type": host_info.get("type", 0)},
        {"id": team_aff_id, "level": team_info.get("default_level", 1), "type": team_info.get("type", 3)}
    ]
    
    # 2. 推荐神系 (基于复合有利属性对应神系)
    recommend_team = comp_info.get("races", [1, 4])
    
    # 3. 常规黑区优势词缀
    sup_pair = REGULAR_SUPERIORITY_POOL[cycle_id % len(REGULAR_SUPERIORITY_POOL)]
    superiority_affixes = [
        {"id": sup_pair[0], "level": 10, "type": 0},
        {"id": sup_pair[1], "level": 5, "type": 0},
        {"id": sup_pair[2], "level": 1, "type": 3}
    ]
    
    # 4. 常规黑区劣势词缀
    inferiority_affixes = [
        {"id": 121, "level": 3, "type": 0},
        {"id": 228, "level": 1, "type": 3},
        {"id": 229, "level": 1, "type": 3}
    ]
    
    # 5. 当期 Boss 组
    boss_profile = BOSS_PROFILES[cycle_id % len(BOSS_PROFILES)]
    
    return {
        "cycle_id": cycle_id,
        "next_refresh_timestamp": next_rf,
        "ultimate_affixes": ultimate_affixes,
        "superiority_affixes": superiority_affixes,
        "inferiority_affixes": inferiority_affixes,
        "recommend_team": recommend_team,
        "boss_profile": boss_profile
    }


# ----------------- 3. 奖励配置表 -----------------

# 失序深区 1~30 档单档通关奖励
MYTHIC_FINAL_REWARDS = {
    d: [
        {"id": 43, "num": 60},      # 移转之辉
        {"id": 41301, "num": 2},   # 辉光物质
        {"id": 25, "num": 150},     # 狂气之黑曜
        {"id": 40701, "num": 4}     # 重构秘典
    ]
    for d in range(1, 31)
}

# 常规黑区 1~13 难度累计星级奖励
MYTHIC_NORMAL_STAR_REWARDS = {
    1: [{"id": 25, "num": 135}, {"id": 40701, "num": 4}],
    2: [{"id": 25, "num": 195}, {"id": 40701, "num": 5}],
    3: [{"id": 25, "num": 255}, {"id": 40701, "num": 6}],
    4: [{"id": 25, "num": 315}, {"id": 40701, "num": 7}],
    5: [{"id": 25, "num": 375}, {"id": 40701, "num": 8}],
    6: [{"id": 25, "num": 435}, {"id": 40701, "num": 9}],
    7: [{"id": 25, "num": 495}, {"id": 40701, "num": 10}],
    8: [{"id": 25, "num": 555}, {"id": 40701, "num": 11}],
    9: [{"id": 25, "num": 615}, {"id": 40701, "num": 12}],
    10: [{"id": 25, "num": 675}, {"id": 40701, "num": 13}],
    11: [{"id": 25, "num": 735}, {"id": 40701, "num": 14}],
    12: [{"id": 25, "num": 795}, {"id": 40701, "num": 15}],
    13: [{"id": 25, "num": 855}, {"id": 40701, "num": 16}]
}
