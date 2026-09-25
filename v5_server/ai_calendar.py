# -*- coding: utf-8 -*-
"""
ai_calendar.py — 本地万年历与节日/节气/生日解算引擎

核心职责：
1. 本地时间驱动：直接读取 Windows/系统时间，无需大模型联网搜索；
2. 离线公历与农历节日判定：涵盖元旦、春节、元宵、清明、端午、中秋、国庆、除夕等各大节日；
3. 二十四节气天文解算：基于紫金山天文台太阳黄经离线常数，精确推算立春、夏至、立秋、冬至等节气；
4. 官方修正者生日名册：深度集成 63 位修正者官方设定生日，精准定位寿星；
5. 管理员（玩家）生日判定：打通 game_user 表的 birth_month 与 birth_day。
"""

import os
import json
import logging
import datetime

logger = logging.getLogger("ai_calendar")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HERO_BIRTHDAYS_PATH = os.path.join(BASE_DIR, "data", "ai_hero_birthdays.json")

# ==============================================================================
# 1. 农历算法（1900 - 2050 离线常数表，无第三方依赖）
# ==============================================================================
LUNAR_INFO = [
    0x04bd8, 0x04ae0, 0x0a570, 0x054d5, 0x0d260, 0x0d950, 0x16554, 0x056a0, 0x09ad0, 0x055d2,
    0x04ae0, 0x0a5b6, 0x0a4d0, 0x0d250, 0x1d255, 0x0b540, 0x0d6a0, 0x0ada2, 0x095b0, 0x14977,
    0x04970, 0x0a4b0, 0x0b4b5, 0x06a50, 0x06d40, 0x1ab54, 0x02b60, 0x09570, 0x052f2, 0x04970,
    0x06566, 0x0d4a0, 0x0ea50, 0x06e95, 0x05ad0, 0x02b60, 0x186e3, 0x092e0, 0x1c8d7, 0x04950,
    0x0d4a0, 0x1f865, 0x0b550, 0x056a0, 0x1a5b4, 0x025d0, 0x092d0, 0x0d2b2, 0x0a950, 0x0b557,
    0x06ca0, 0x0b550, 0x15355, 0x04da0, 0x0a5d0, 0x14573, 0x052d0, 0x0a9a8, 0x0e950, 0x06aa0,
    0x0aea6, 0x0ab50, 0x04b60, 0x0aae4, 0x0a570, 0x05260, 0x0f263, 0x0d950, 0x05b57, 0x056a0,
    0x096d0, 0x04dd5, 0x04ad0, 0x0a4d0, 0x0d4d4, 0x0d250, 0x0d558, 0x0b540, 0x0b5a0, 0x195a6,
    0x095b0, 0x049b0, 0x0a974, 0x0a4b0, 0x0b27a, 0x06a50, 0x06d40, 0x0af46, 0x0ab60, 0x09570,
    0x04af5, 0x04970, 0x064b0, 0x074a3, 0x0ea50, 0x06b58, 0x055c0, 0x0ab60, 0x096d5, 0x092e0,
    0x0c960, 0x0d954, 0x0d4a0, 0x0da50, 0x07552, 0x056a0, 0x0abb7, 0x025d0, 0x092d0, 0x0cab5,
    0x0a950, 0x0b4a0, 0x0baa4, 0x0ad50, 0x055d9, 0x04ba0, 0x0a5b0, 0x15176, 0x052b0, 0x0a930,
    0x07954, 0x06aa0, 0x0ad50, 0x05b52, 0x04b60, 0x0a6e6, 0x0a4e0, 0x0d260, 0x0ea65, 0x0d530,
    0x05aa0, 0x076a3, 0x096d0, 0x04afb, 0x04ad0, 0x0a4d0, 0x1d0b6, 0x0d250, 0x0d520, 0x0dd45,
    0x0b5a0, 0x056d0, 0x055b2, 0x049b0, 0x0a577, 0x0a4b0, 0x0aa50, 0x1b255, 0x06d20, 0x0ada0,
    0x14b63  # 2050
]

def solar_to_lunar(solar_date):
    """公历日期转农历日期：返回 (lunar_year, lunar_month, lunar_day, is_leap)"""
    if isinstance(solar_date, datetime.datetime):
        solar_date = solar_date.date()
    base_date = datetime.date(1900, 1, 31)
    offset = (solar_date - base_date).days
    if offset < 0:
        return 1900, 1, 1, False

    year = 1900
    while year <= 2050:
        code = LUNAR_INFO[year - 1900]
        days_in_year = 0
        for m in range(12):
            days_in_year += 30 if (code & (0x10000 >> (m + 1))) else 29
        leap = code & 0xf
        if leap > 0:
            days_in_year += 30 if (code & 0x10000) else 29

        if offset < days_in_year:
            break
        offset -= days_in_year
        year += 1

    if year > 2050:
        return 2050, 12, 1, False

    code = LUNAR_INFO[year - 1900]
    leap = code & 0xf
    is_leap = False

    for m in range(1, 13):
        if leap > 0 and m == (leap + 1) and not is_leap:
            is_leap = True
            m_days = 30 if (code & 0x10000) else 29
            if offset < m_days:
                return year, leap, offset + 1, True
            offset -= m_days

        m_days = 30 if (code & (0x10000 >> m)) else 29
        if offset < m_days:
            return year, m, offset + 1, False
        offset -= m_days

    return year, 12, offset + 1, False


# ==============================================================================
# 2. 二十四节气天文解算（21世纪 2000-2099 常数表）
# ==============================================================================
SOLAR_TERMS_21C = [
    ("小寒", 1, 5.4055), ("大寒", 1, 20.12),
    ("立春", 2, 3.87),   ("雨水", 2, 18.73),
    ("惊蛰", 3, 5.63),   ("春分", 3, 20.646),
    ("清明", 4, 4.81),   ("谷雨", 4, 20.1),
    ("立夏", 5, 5.52),   ("小满", 5, 21.04),
    ("芒种", 6, 5.678),  ("夏至", 6, 21.37),
    ("小暑", 7, 7.108),  ("大暑", 7, 22.83),
    ("立秋", 8, 7.5),    ("处暑", 8, 23.13),
    ("白露", 9, 7.646),  ("秋分", 9, 23.042),
    ("寒露", 10, 8.318), ("霜降", 10, 23.438),
    ("立冬", 11, 7.438), ("小雪", 11, 22.36),
    ("大雪", 12, 7.18),  ("冬至", 12, 21.94)
]

def get_solar_term(solar_date):
    """判断指定日期是否为二十四节气之一，返回节气名称或 None"""
    if isinstance(solar_date, datetime.datetime):
        solar_date = solar_date.date()
    y = solar_date.year % 100
    for name, month, c in SOLAR_TERMS_21C:
        if solar_date.month == month:
            day = int(y * 0.2422 + c) - int((y - 1) / 4)
            if solar_date.day == day:
                return name
    return None


# ==============================================================================
# 3. 节日规则表（公历节日与农历大节）
# ==============================================================================
GREGORIAN_HOLIDAYS = {
    (1, 1):   ("元旦", "新年的第一天，辞旧迎新"),
    (3, 8):   ("妇女节", "国际劳动妇女节，致敬身边的每一位女性伙伴"),
    (5, 1):   ("劳动节", "国际劳动节，辛苦工作后的惬意休整日"),
    (5, 4):   ("青年节", "五四青年节，意气风发"),
    (6, 1):   ("儿童节", "六一儿童节，重温纯真童心"),
    (9, 10):  ("教师节", "感念师恩，指引明灯"),
    (10, 1):  ("国庆节", "国庆华诞，万象祥和")
}

LUNAR_HOLIDAYS = {
    (1, 1):   ("春节", "农历正月初一，新春大吉，岁岁平安"),
    (1, 15):  ("元宵节", "农历正月十五上元佳节，共吃元宵、赏花灯"),
    (5, 5):   ("端午节", "农历五月初五端午安康，粽叶飘香"),
    (7, 7):   ("七夕节", "农历七月初七星河璀璨，乞巧相伴"),
    (8, 15):  ("中秋节", "农历八月十五月圆人圆，天涯共此时"),
    (9, 9):   ("重阳节", "农历九月初九登高祈福，长久安康")
}


# ==============================================================================
# 4. 修正者官方生日名册加载
# ==============================================================================
_HERO_BIRTHDAYS_CACHE = None

def _get_hero_birthdays():
    global _HERO_BIRTHDAYS_CACHE
    if _HERO_BIRTHDAYS_CACHE is None:
        cache = {}
        if os.path.exists(HERO_BIRTHDAYS_PATH):
            try:
                with open(HERO_BIRTHDAYS_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception as e:
                logger.error(f"加载 ai_hero_birthdays.json 失败: {e}")
        _HERO_BIRTHDAYS_CACHE = cache
    return _HERO_BIRTHDAYS_CACHE


# ==============================================================================
# 5. 核心万能判定接口
# ==============================================================================
def get_calendar_events(cur_date=None, uid=None, db=None):
    """
    解算指定日期的所有事件（节假日、节气、修正者生日、玩家生日）
    """
    if cur_date is None:
        cur_date = datetime.date.today()
    elif isinstance(cur_date, datetime.datetime):
        cur_date = cur_date.date()

    ly, lm, ld, is_leap = solar_to_lunar(cur_date)
    lunar_desc = f"农历{ly}年{'闰' if is_leap else ''}{lm}月{ld}日"

    events = []

    # 1. 检查公历节日
    g_key = (cur_date.month, cur_date.day)
    if g_key in GREGORIAN_HOLIDAYS:
        h_name, h_desc = GREGORIAN_HOLIDAYS[g_key]
        bundle = "holiday_major" if h_name in ("国庆节", "元旦") else "holiday_general"
        events.append({
            "type": "holiday",
            "name": h_name,
            "desc": h_desc,
            "bundle": bundle
        })

    # 2. 检查农历传统大节
    if not is_leap:
        l_key = (lm, ld)
        if l_key in LUNAR_HOLIDAYS:
            h_name, h_desc = LUNAR_HOLIDAYS[l_key]
            bundle = "holiday_major" if h_name in ("春节", "中秋节", "端午节") else "holiday_general"
            events.append({
                "type": "holiday",
                "name": h_name,
                "desc": h_desc,
                "bundle": bundle
            })

    # 3. 检查除夕判定（农历腊月最后一天）
    tomorrow = cur_date + datetime.timedelta(days=1)
    t_ly, t_lm, t_ld, _ = solar_to_lunar(tomorrow)
    if t_lm == 1 and t_ld == 1:
        events.append({
            "type": "holiday",
            "name": "除夕",
            "desc": "岁暮守岁，共迎新春",
            "bundle": "holiday_major"
        })

    # 4. 检查二十四节气
    st_name = get_solar_term(cur_date)
    if st_name:
        events.append({
            "type": "solar_term",
            "name": st_name,
            "desc": f"今日{st_name}节气，顺应天时",
            "bundle": "solar_term"
        })

    # 5. 检查修正者生日
    birthdays_dict = _get_hero_birthdays()
    for rec_id_str, info in birthdays_dict.items():
        if int(info.get("month", 0)) == cur_date.month and int(info.get("day", 0)) == cur_date.day:
            events.append({
                "type": "hero_birthday",
                "name": f"{info.get('hero_name')}生日",
                "hero_name": info.get("hero_name"),
                "record_id": int(rec_id_str),
                "hero_ids": info.get("hero_ids", []),
                "organization": info.get("organization", ""),
                "birthday_str": info.get("birthday_str", f"{cur_date.month}月{cur_date.day}日"),
                "desc": f"今天是修正者【{info.get('hero_name')}】的生日",
                "bundle": "hero_birthday"
            })

    # 6. 检查管理员（玩家）生日
    is_user_bday = False
    if uid and db is not None:
        try:
            g_rows = db.query("SELECT birth_month, birth_day FROM game_user WHERE uid=?", (uid,))
            if g_rows:
                bm = int(g_rows[0].get("birth_month") or 0)
                bd = int(g_rows[0].get("birth_day") or 0)
                if bm > 0 and bd > 0 and bm == cur_date.month and bd == cur_date.day:
                    is_user_bday = True
                    events.append({
                        "type": "user_birthday",
                        "name": "管理员生日",
                        "desc": "今天是管理员您的专属生日！深空之眼全体伙伴衷心祝愿您生日快乐！",
                        "bundle": "user_birthday"
                    })
        except Exception as e:
            logger.warning(f"查询玩家 uid={uid} 生日异常: {e}")

    has_holiday = any(e["type"] == "holiday" for e in events)
    has_solar = any(e["type"] == "solar_term" for e in events)
    has_hero_bday = any(e["type"] == "hero_birthday" for e in events)

    return {
        "date": cur_date.isoformat(),
        "month": cur_date.month,
        "day": cur_date.day,
        "lunar_str": lunar_desc,
        "is_holiday": has_holiday,
        "is_solar_term": has_solar,
        "is_hero_birthday": has_hero_bday,
        "is_user_birthday": is_user_bday,
        "is_player_birthday": is_user_bday,
        "events": events
    }
