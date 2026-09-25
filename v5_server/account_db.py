# -*- coding: utf-8 -*-
"""
account_db.py — 账户数据库封装（SQLite，多用户 uid 隔离）

架构：伪装服务器的"库存储"层。
- 每张业务表带 uid 列（多用户隔离），所有查询 WHERE uid = ?
- 关键扁平字段拆列（currency/material 的 id+num），复杂嵌套结构存 JSON（TEXT 列）
- 提供建表 / 种子导入 / CRUD 接口，供中间件（codec + generator）使用

表域：
  users      账户主表（uid/昵称/等级/经验/服务器）
  sdk        SDK 登录态（token/gstoken/uid_sign）
  currency   货币（id + num）
  material   材料（id + num）
  equip      装备（id + JSON 详情）
  hero       英雄（id + JSON 详情）
  sign       签到（activity_id + 日历 JSON + 状态 JSON）
  validation 资源校验（assethash/voice 版本）
  mail       邮件（JSON 列表）

依赖：Python 内置 sqlite3，零外部依赖。
"""
import json
import math
import os
import sqlite3
import threading
import time

DEFAULT_DB = os.environ.get(
    "ACCOUNT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "account.db")
)
_CANTEEN_FOOD_CFG = None
_BACKHOME_HERO_SKILL_CFG = None

# 官方 63 档案与主自机形态映射表 (archives_id -> primary_hero_id)
ARCHIVE_MAP = {
    1011: 1211, 1012: 1012, 1013: 1013, 1015: 1015, 1016: 1016, 1017: 1017, 1019: 1119, 1020: 1020,
    1021: 1021, 1022: 1022, 1024: 1024, 1026: 1026, 1027: 1127, 1028: 1028, 1032: 1132, 1033: 1133,
    1034: 1034, 1035: 1035, 1037: 1137, 1038: 1138, 1039: 1139, 1041: 1041, 1042: 1042, 1043: 1043,
    1044: 1044, 1045: 1045, 1046: 1046, 1047: 1047, 1048: 1248, 1049: 1049, 1050: 1150, 1052: 1052,
    1053: 1053, 1054: 1054, 1055: 1055, 1056: 1156, 1058: 1158, 1059: 1059, 1060: 1060, 1061: 1061,
    1066: 1166, 1067: 1067, 1068: 1068, 1070: 1170, 1071: 1071, 1072: 1072, 1073: 1073, 1074: 1074,
    1075: 1075, 1076: 1076, 1077: 1077, 1080: 1080, 1081: 1081, 1083: 1083, 1084: 1284, 1085: 1085,
    1089: 1089, 1093: 1093, 1094: 1194, 1095: 1095, 1096: 1096, 1097: 1197, 1099: 1199
}

# 英雄自机卡号到档案 ID 的反向查找表 (hero_id -> archives_id)
HERO_TO_ARCHIVE = {
    1011: 1011, 1111: 1011, 1211: 1011, 1012: 1012, 1013: 1013, 1015: 1015, 1016: 1016, 1017: 1017,
    1019: 1019, 1119: 1019, 1020: 1020, 1021: 1021, 1022: 1022, 1024: 1024, 1026: 1026, 1027: 1027,
    1127: 1027, 1028: 1028, 1032: 1032, 1132: 1032, 1033: 1033, 1133: 1033, 1034: 1034, 1035: 1035,
    1037: 1037, 1137: 1037, 1038: 1038, 1138: 1038, 1039: 1039, 1139: 1039, 1041: 1041, 1042: 1042,
    1043: 1043, 1044: 1044, 1045: 1045, 1046: 1046, 1047: 1047, 1048: 1048, 1148: 1048, 1248: 1048,
    1049: 1049, 1050: 1050, 1150: 1050, 1052: 1052, 1053: 1053, 1054: 1054, 1055: 1055, 1056: 1056,
    1156: 1056, 1058: 1058, 1158: 1058, 1059: 1059, 1060: 1060, 1061: 1061, 1066: 1066, 1166: 1066,
    1067: 1067, 1068: 1068, 1070: 1070, 1170: 1070, 1071: 1071, 1072: 1072, 1073: 1073, 1074: 1074,
    1075: 1075, 1076: 1076, 1077: 1077, 1080: 1080, 1081: 1081, 1083: 1083, 1084: 1084, 1184: 1084,
    1284: 1084, 1085: 1085, 1089: 1089, 1093: 1093, 1094: 1094, 1194: 1094, 1095: 1095, 1096: 1096,
    1097: 1097, 1197: 1097, 1099: 1099, 1199: 1099
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    uid               INTEGER PRIMARY KEY,
    account           TEXT,
    server_id         INTEGER DEFAULT 2,
    nick              TEXT DEFAULT '',
    level             INTEGER DEFAULT 1,
    exp               INTEGER DEFAULT 0,
    extra             TEXT DEFAULT '{}',
    hero_num          INTEGER DEFAULT 0,
    plot_progress     INTEGER DEFAULT 0,
    is_changed_nick   INTEGER DEFAULT 0,
    change_nick_times INTEGER DEFAULT 0,
    sign              TEXT DEFAULT '',
    portrait          INTEGER DEFAULT 0,
    icon_frame        INTEGER DEFAULT 0,
    likes             INTEGER DEFAULT 0,
    ip_location       TEXT DEFAULT '',
    register_ts       INTEGER DEFAULT 0,
    club_id           INTEGER DEFAULT 0,
    club_post         INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sdk (
    uid         INTEGER PRIMARY KEY,
    token       TEXT,
    gstoken     TEXT,
    uid_sign    TEXT,
    register_ts INTEGER DEFAULT 0,
    extra       TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS currency (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    num         INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);
CREATE TABLE IF NOT EXISTS material (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    num         INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);
CREATE TABLE IF NOT EXISTS equip (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    detail      TEXT DEFAULT '{}',
    PRIMARY KEY (uid, id)
);
CREATE TABLE IF NOT EXISTS hero (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    detail      TEXT DEFAULT '{}',
    PRIMARY KEY (uid, id)
);
CREATE TABLE IF NOT EXISTS sign (
    uid         INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    year        INTEGER,
    month       INTEGER,
    day         INTEGER,
    sign_list   TEXT DEFAULT '[]',
    sign_count  INTEGER DEFAULT 0,
    last_sign_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS sign_reward_cfg (     -- 每日签到奖励（signcfg.lua 导入，静态无 uid）
    month INTEGER NOT NULL,
    day INTEGER NOT NULL,                        -- 累计第 N 天 → (本月, N)，无则 (0, N) 兜底
    reward_id INTEGER DEFAULT 0,
    reward_num INTEGER DEFAULT 0,
    PRIMARY KEY (month, day)
);
CREATE TABLE IF NOT EXISTS validation (
    uid         INTEGER NOT NULL,
    kind        TEXT NOT NULL,        -- 'assethash' / 'voice_zh' / 'voice_ja' / 'voice_package'
    version     TEXT DEFAULT '',
    detail      TEXT DEFAULT '{}',
    PRIMARY KEY (uid, kind)
);
CREATE TABLE IF NOT EXISTS mail (
    uid         INTEGER NOT NULL,
    mail_id     INTEGER NOT NULL,
    detail      TEXT DEFAULT '{}',
    PRIMARY KEY (uid, mail_id)
);
CREATE TABLE IF NOT EXISTS item_catalog (
    id         INTEGER PRIMARY KEY,
    name       TEXT DEFAULT '',
    type       INTEGER,
    rare       INTEGER,
    category   TEXT DEFAULT 'other'   -- currency/material/keyong/cosmetic/other
);
CREATE TABLE IF NOT EXISTS redpoint (
    uid         INTEGER NOT NULL,
    red_dot_id  INTEGER NOT NULL,
    state       INTEGER DEFAULT 0,    -- 0=已处理(finished) 1=待展示(red_dot 白名单)
    finish_ts   INTEGER DEFAULT 0,
    PRIMARY KEY (uid, red_dot_id)
);
CREATE TABLE IF NOT EXISTS game_level_setting (   -- 等级曲线（gamelevelsetting.lua 导入，静态无 uid）
    level INTEGER PRIMARY KEY,
    hero_exp_need INTEGER DEFAULT 0,   -- 本级升下一级所需经验（herotools.CheckExp 级内语义）
    hero_exp_sum INTEGER DEFAULT 0,    -- 升到本级累计经验
    fatigue_upgrade_reward INTEGER DEFAULT 0,
    battlepass_level_exp INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hero_skill_element_cfg (  -- 技能属性强化配置（heroskillelementcfg.lua 导入，静态无 uid）
    id INTEGER PRIMARY KEY,            -- hero_id*100+level
    hero_id INTEGER NOT NULL,
    level INTEGER DEFAULT 0,
    cost TEXT DEFAULT '{}',            -- {"1": [[item_id,num],...], ...} 槽位→材料
    attr TEXT DEFAULT '{}'             -- {"1": [[attr_id,value],...], ...}
);
CREATE TABLE IF NOT EXISTS hero_archive (
    uid         INTEGER NOT NULL,
    archive_id  INTEGER NOT NULL,     -- 英雄档案（心链/羁绊系统）：archive_id=英雄 id
    exp         INTEGER DEFAULT 0,    -- 档案经验
    text_list   TEXT DEFAULT '[]',    -- 已读档案文本 JSON
    video_list  TEXT DEFAULT '[]',    -- 已看档案视频 JSON
    gift_list   TEXT DEFAULT '[]',    -- 礼物记录 JSON [{id,num}]
    selected_picture TEXT DEFAULT '{}',   -- 选中档案卡面 {type,id}
    super_heart_link_list TEXT DEFAULT '[]', -- 超心链 {index,is_viewed}
    hero_story_list TEXT DEFAULT '[]',      -- 英雄故事
    PRIMARY KEY (uid, archive_id)
);
CREATE TABLE IF NOT EXISTS hero_piece (
    uid     INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,         -- 英雄碎片（升星材料）
    num     INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id)
);
CREATE TABLE IF NOT EXISTS guide (
    uid       INTEGER NOT NULL,
    guide_id  INTEGER NOT NULL,
    name      TEXT DEFAULT '',
    kind      TEXT DEFAULT 'main',   -- main=强引导(12011) / weak=弱引导(12111)
    PRIMARY KEY (uid, guide_id)
);
CREATE TABLE IF NOT EXISTS letter_special (
    uid         INTEGER NOT NULL,
    letter_id   INTEGER NOT NULL,
    hero_id     INTEGER DEFAULT 0,
    is_viewed   INTEGER DEFAULT 0,
    update_ts   INTEGER DEFAULT 0,
    PRIMARY KEY (uid, letter_id)
);
CREATE TABLE IF NOT EXISTS momotalk (

    uid         INTEGER NOT NULL,
    momotalk_id INTEGER NOT NULL,
    name        TEXT DEFAULT '',
    read_flag   INTEGER DEFAULT 1,
    activity_id INTEGER DEFAULT 3639701,
    PRIMARY KEY (uid, momotalk_id)
);
CREATE TABLE IF NOT EXISTS ops_common (
    uid        INTEGER NOT NULL,
    kind       TEXT NOT NULL,          -- survey/operate/platform/announcement
    item_id    TEXT NOT NULL,
    name       TEXT DEFAULT '',
    value_json TEXT DEFAULT '{}',
    PRIMARY KEY (uid, kind, item_id)
);
CREATE TABLE IF NOT EXISTS chapter_star_reward (
    uid          INTEGER NOT NULL,
    chapter_id   INTEGER NOT NULL,
    reward_order INTEGER NOT NULL,     -- 1=first_reward, 2=second_reward, 3=third_reward
    receive_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, reward_order)
);
CREATE TABLE IF NOT EXISTS chat_sticker (
    uid      INTEGER NOT NULL,
    emoji_id INTEGER NOT NULL,
    name     TEXT DEFAULT '',
    desc     TEXT DEFAULT '',
    PRIMARY KEY (uid, emoji_id)
);
CREATE TABLE IF NOT EXISTS recommend_equip (
    engrave_id  INTEGER NOT NULL,      -- 英雄id（静态配置，无 uid）
    astro_json  TEXT DEFAULT '[]',
    servant_json TEXT DEFAULT '[]',
    omega_json  TEXT DEFAULT '[]',
    PRIMARY KEY (engrave_id)
);
CREATE TABLE IF NOT EXISTS shop_goods (
    goods_id      INTEGER PRIMARY KEY, -- 商品id（静态配置，无 uid）
    item_id       INTEGER DEFAULT 0,
    name          TEXT DEFAULT '',
    cost_type     INTEGER DEFAULT 0,
    cost_id       INTEGER DEFAULT 0,
    cost          INTEGER DEFAULT 0,
    cheap_cost    INTEGER DEFAULT 0,
    discount      INTEGER DEFAULT 0,
    limit_num     INTEGER DEFAULT 0,   -- -1=无限购(uint64最大)
    refresh_cycle INTEGER DEFAULT 0,
    open_time     TEXT DEFAULT '',
    close_time    TEXT DEFAULT '',
    tag           INTEGER DEFAULT 0,
    detail_json   TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS game_user (
    uid                     INTEGER PRIMARY KEY,
    birth_month             INTEGER DEFAULT 0,
    birth_day               INTEGER DEFAULT 0,
    is_first_draw           INTEGER DEFAULT 0,
    is_first_draw_limited   INTEGER DEFAULT 0,
    is_first_draw_lucky     INTEGER DEFAULT 0,
    total_buy_fatigue_times INTEGER DEFAULT 0,
    bgm_id                  INTEGER DEFAULT 0,
    dessert_11_got          INTEGER DEFAULT 0,
    dessert_18_got          INTEGER DEFAULT 0,
    update_ts               INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system_config (
    uid     INTEGER NOT NULL,
    sys_id  INTEGER NOT NULL,
    open    INTEGER DEFAULT 1,    -- 1=开放(10600) 0=未开放
    suspend INTEGER DEFAULT 0,    -- 1=停用(40035)
    name    TEXT DEFAULT '',
    PRIMARY KEY (uid, sys_id)
);
CREATE TABLE IF NOT EXISTS enchant (
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    difficulty INTEGER DEFAULT 0,
    group_id INTEGER DEFAULT 0,
    monster_level INTEGER DEFAULT 0,
    cost INTEGER DEFAULT 0,
    drop_lib_id INTEGER DEFAULT 0,
    remain_free_refresh INTEGER DEFAULT 2,   -- 剩余免费刷新次数(合入条目，不单独建表)
    daily_refresh_times INTEGER DEFAULT 2,   -- 每日免费刷新上限
    unlock_level INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS resource_stage (
    id INTEGER PRIMARY KEY,        -- 静态配置（全账号一致，无 uid）
    name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    difficulty INTEGER DEFAULT 0,
    max_star INTEGER DEFAULT 3,
    stamina_need INTEGER DEFAULT 0,
    reward TEXT DEFAULT '',
    group_id INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS battle_equip (
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    drop_lib_id INTEGER DEFAULT 0,
    insure_times INTEGER DEFAULT 0,
    next_refresh_time INTEGER DEFAULT 0,
    suit_id INTEGER DEFAULT 0,       -- 战斗装备·当前上阵套装（43004 切换）
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS tutorial (
    uid INTEGER NOT NULL,
    tutorial_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    kind TEXT DEFAULT 'base',   -- base=基础教学 / hero=英雄教学
    pass_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, tutorial_id)
);
CREATE TABLE IF NOT EXISTS stage_archive_collect (
    uid INTEGER NOT NULL,
    archive_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    view_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, archive_id)
);
CREATE TABLE IF NOT EXISTS story_unlock (
    uid INTEGER NOT NULL,
    story_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    unlock_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, story_id)
);
CREATE TABLE IF NOT EXISTS backhome (
    uid INTEGER PRIMARY KEY,
    data_json TEXT DEFAULT '{}',
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shop (
    shop_id INTEGER PRIMARY KEY,      -- 商店元信息（静态，无 uid）
    name TEXT DEFAULT '',
    group_name TEXT DEFAULT '',
    system INTEGER DEFAULT 1,
    refresh_num_limit INTEGER DEFAULT 0,
    activity_id INTEGER DEFAULT 0,
    is_permanent INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS store_purchase (
    uid INTEGER NOT NULL,
    shop_id INTEGER NOT NULL,
    goods_id INTEGER NOT NULL,
    buy_times INTEGER DEFAULT 0,
    next_refresh_timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, shop_id, goods_id)
);
CREATE TABLE IF NOT EXISTS announcement (
    uid INTEGER NOT NULL,
    ann_id INTEGER NOT NULL,
    type TEXT DEFAULT 'game',     -- game=游戏内公告 / other=其他公告（细分：运营声明/系统公告/版本PV）
    title TEXT DEFAULT '',
    start_ts INTEGER DEFAULT 0,
    end_ts INTEGER DEFAULT 0,
    content_json TEXT DEFAULT '{}',
    PRIMARY KEY (uid, ann_id)
);
CREATE TABLE IF NOT EXISTS player_card (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,           -- portrait/icon_frame/title/card_bg/home_bg/bubble/game_icon/sticker/sticker_bg
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    obtained INTEGER DEFAULT 0,
    PRIMARY KEY (uid, kind, item_id)
);
CREATE TABLE IF NOT EXISTS scene (
    uid INTEGER NOT NULL,
    scene_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    scene_type INTEGER DEFAULT 0,  -- 1=常驻展示 / 0=活动特殊
    is_current INTEGER DEFAULT 0,
    obtain_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, scene_id)
);
CREATE TABLE IF NOT EXISTS draw_pool (
    pool_id INTEGER PRIMARY KEY,   -- 卡池配置（静态，无 uid）
    name TEXT DEFAULT '',
    pool_type INTEGER DEFAULT 0,   -- 1常驻角色/2武器/3角色UP/5新手/6武器UP/8新手自选/9回归
    ssr_rate REAL DEFAULT 2.0,
    guarantee TEXT DEFAULT '',
    detail_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS draw_hero_pool (
    hero_id INTEGER PRIMARY KEY,   -- 抽卡专有角色发放池（静态无 uid，82位可玩修正者）
    name TEXT NOT NULL,
    rare TEXT NOT NULL,            -- 'S' / 'A' / 'B'
    rare_val INTEGER NOT NULL,     -- 3=S / 2=A / 1=B
    unlock_star INTEGER NOT NULL,  -- 300=S / 200=A / 100=B
    is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS task (
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    task_type INTEGER DEFAULT 0,   -- type：6每日/3章节/603七日/604主线/1006勘察等（taskconst）
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    expired_ts INTEGER DEFAULT 0,
    claimed_ts INTEGER DEFAULT 0,   -- 领取时间(防重复入账，幂等)
    PRIMARY KEY (uid, task_id)
);
CREATE TABLE IF NOT EXISTS tower (
    uid INTEGER NOT NULL,
    area INTEGER NOT NULL,          -- 历战轮回塔(4010101-4010120)
    stage INTEGER DEFAULT 0,        -- 已通关最高层(全局层号)
    name TEXT DEFAULT '',
    PRIMARY KEY (uid, area)
);
CREATE TABLE IF NOT EXISTS activity_pt (
    uid INTEGER NOT NULL,
    activity_pt_id INTEGER NOT NULL, -- 1=每日 / 3=每周 (2=七日)
    active_point INTEGER DEFAULT 0,
    get_id_list TEXT DEFAULT '[]',   -- 已领档位目标积分值
    PRIMARY KEY (uid, activity_pt_id)
);
CREATE TABLE IF NOT EXISTS coop_room (
    uid INTEGER PRIMARY KEY,
    room_info_json TEXT DEFAULT '{}', -- 合作房间占坑（54201/37101 空帧）
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS task_cfg (
    task_id INTEGER PRIMARY KEY,     -- 全量任务配置（静态，无 uid，assignmentcfg 提取 5472 条）
    name TEXT DEFAULT '',
    task_type INTEGER DEFAULT 0,
    condition INTEGER DEFAULT 0,
    desc TEXT DEFAULT '',
    need INTEGER DEFAULT 0,
    activity_id INTEGER DEFAULT 0,
    pre_id INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS matrix_hero_standard (
    standard_id INTEGER PRIMARY KEY,  -- 3031xxx 可解锁标准角色（每周四解锁池，33 个）
    hero_id INTEGER DEFAULT 0,
    hero_name TEXT DEFAULT '',
    template_id INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS matrix_hero_attr (
    standard_id INTEGER PRIMARY KEY,  -- = herostandardsystemcfg 条目 id
    hero_id INTEGER DEFAULT 0,
    star_lv INTEGER DEFAULT 0,
    skill_lv INTEGER DEFAULT 0,
    hero_lv INTEGER DEFAULT 0,
    weapon_break INTEGER DEFAULT 0,
    weapon_level INTEGER DEFAULT 0,
    weapon_stage INTEGER DEFAULT 0,
    break_lv INTEGER DEFAULT 0,
    hero_break INTEGER DEFAULT 0,
    equip_lv INTEGER DEFAULT 0,
    weapon_key INTEGER DEFAULT 0,
    astrolabe_id TEXT DEFAULT '',
    equip_list_json TEXT DEFAULT '[]',
    skill_element_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS matrix_system (
    uid INTEGER NOT NULL,
    ready_hero_ids TEXT DEFAULT '[]',    -- 本期 5 个 standard_id
    difficulty_json TEXT DEFAULT '[]',   -- 3 档难度
    next_refresh_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);
CREATE TABLE IF NOT EXISTS matrix_user (
    uid INTEGER PRIMARY KEY,
    game_state INTEGER DEFAULT 1,
    terminal_json TEXT DEFAULT '{}',
    beacon_json TEXT DEFAULT '[]',
    point_reward_got TEXT DEFAULT '[]',  -- 已领档位 rank 列表
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matrix_point_reward (
    rank INTEGER PRIMARY KEY,
    point_need INTEGER DEFAULT 0,
    need_level INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS warchess_chapter (
    chapter_id INTEGER PRIMARY KEY,   -- 4040xxx 战棋章节（29 章）
    name TEXT DEFAULT '',
    chapter_type INTEGER DEFAULT 0,   -- 0=常驻
    open_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS warchess_box (
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    box_id INTEGER NOT NULL,
    num INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, box_id)
);
CREATE TABLE IF NOT EXISTS warchess_box_record (
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    box_id INTEGER NOT NULL,
    tag INTEGER NOT NULL DEFAULT 0,
    pos_x REAL NOT NULL DEFAULT 0,
    pos_z REAL NOT NULL DEFAULT 0,
    open_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, box_id, tag, pos_x, pos_z)
);
CREATE TABLE IF NOT EXISTS warchess_map (
    uid INTEGER NOT NULL,
    activity_id INTEGER DEFAULT 0,
    current_chapter INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS warchess_session (
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    pos_x REAL DEFAULT 0,
    pos_z REAL DEFAULT 0,
    direction INTEGER DEFAULT 0,
    is_fog INTEGER DEFAULT 0,
    fog_json TEXT DEFAULT '[]',
    map_json TEXT DEFAULT '[]',
    bag_json TEXT DEFAULT '{"item":[],"artifact":[]}',
    hp_json TEXT DEFAULT '[]',
    logs_json TEXT DEFAULT '[]',
    events_json TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id)
);
CREATE TABLE IF NOT EXISTS history_cmd (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- history/recharge/first_charge
    key_id TEXT NOT NULL,
    data TEXT DEFAULT '',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, kind, key_id)
);
CREATE TABLE IF NOT EXISTS achievement (
    uid INTEGER NOT NULL,
    achievement_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    condition INTEGER DEFAULT 0,     -- 配置字段（uid=0 配置行）
    desc TEXT DEFAULT '',
    need INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]',
    progress INTEGER DEFAULT 0,      -- 进度字段（uid=实际 进度行）
    complete_flag INTEGER DEFAULT 0,
    achieve_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, achievement_id)
);
CREATE TABLE IF NOT EXISTS month_card (
    uid INTEGER PRIMARY KEY,
    monthly_card_num INTEGER DEFAULT 0,
    monthly_card_timestamp INTEGER DEFAULT 0,  -- 到期时间戳
    is_sign INTEGER DEFAULT 0,                 -- 今日已签到
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS big_month_card (
    uid INTEGER PRIMARY KEY,
    buy_timestamp INTEGER DEFAULT 0,
    is_sign INTEGER DEFAULT 0,
    total_sign_times INTEGER DEFAULT 0,
    total_sign_receive_list TEXT DEFAULT '[]',
    daily_record TEXT DEFAULT '[]',
    is_expire_tip INTEGER DEFAULT 0,
    template_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS battlepass (
    uid INTEGER PRIMARY KEY,
    battlepass_list_id INTEGER DEFAULT 0,      -- 期数(20032)
    pay_level INTEGER DEFAULT 0,               -- 付费等级(202=付费卡)
    is_start INTEGER DEFAULT 0,
    weekly_gain_exp INTEGER DEFAULT 0,
    start_timestamp INTEGER DEFAULT 0,
    end_timestamp INTEGER DEFAULT 0,
    next_refresh_timestamp INTEGER DEFAULT 0,
    receive_info TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS illustrated_cfg (
    kind TEXT NOT NULL,            -- enemy/servant/equip/plot/inbetweening/affix
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    PRIMARY KEY (kind, item_id)
);
CREATE TABLE IF NOT EXISTS illustrated (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    times INTEGER DEFAULT 0,       -- enemy 击杀次数
    is_view INTEGER DEFAULT 0,     -- 已查看
    is_receive INTEGER DEFAULT 0,  -- 插画已领
    progress INTEGER DEFAULT 0,    -- intelligence 进度
    complete_flag INTEGER DEFAULT 1,  -- 图鉴条目激活状态: 1=已解锁(入sc_52001负载帧), 0=未解锁(保留库中元数据不入包)
    pos_list_json TEXT DEFAULT '[]',  -- equip 已收集部位
    PRIMARY KEY (uid, kind, item_id)
);
CREATE TABLE IF NOT EXISTS loading_set (             -- sc_52021 加载图集 id 列表（玩家自定义加载画面）
    uid INTEGER NOT NULL,
    id_list TEXT NOT NULL DEFAULT '[]',              -- JSON 数组，如 [2109401,2109403,2109402]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);
CREATE TABLE IF NOT EXISTS hero_race_collect (       -- sc_52025 英雄种族收集已领取档位
    uid INTEGER NOT NULL,
    race_type INTEGER NOT NULL,                      -- 1奥山/2尼罗/3真樱/4圣树/5众星/9天垣
    received_cnt_list TEXT NOT NULL DEFAULT '[]',    -- JSON 数组，已领档位的收集数量，如 [6,3]
    PRIMARY KEY (uid, race_type)
);
CREATE TABLE IF NOT EXISTS user_skin_draw_pool ( -- 换装/场景卡池抽卡实时库存
    uid INTEGER NOT NULL,
    pool_id INTEGER NOT NULL,
    drop_id INTEGER NOT NULL,
    remain_num INTEGER NOT NULL DEFAULT 0,
    drawn_num INTEGER NOT NULL DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pool_id, drop_id)
);
CREATE TABLE IF NOT EXISTS user_skin_story (     -- 换装活动剧情完成状态
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    story_id INTEGER NOT NULL,
    finish_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, story_id)
);
CREATE TABLE IF NOT EXISTS activity (                -- sc_11001 活动列表全量
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 对应 activitycfg.lua [id]
    name TEXT DEFAULT '',                            -- 活动名（activitycfg，识别用）
    start_time INTEGER DEFAULT 0,
    stop_time INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0,                         -- 0=未开放/已结束 1=开放中
    theme INTEGER DEFAULT 0,
    template INTEGER DEFAULT 0,                      -- ActivityTemplateConst 模板 id
    sub_activity_id_list TEXT NOT NULL DEFAULT '[]', -- JSON 数组
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS activity_play_cmd (       -- 活动玩法→相关协议 cmd 映射（旧 activity 表 20260814 改名保留）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    data_json TEXT DEFAULT '{}',                     -- {"cmd": [...]}
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS mythic_final_cfg (        -- sc_44021 终焉神话难度→关卡映射（静态配置，无 uid）
    difficulty_id INTEGER PRIMARY KEY,               -- 1~30
    stage_id_list TEXT NOT NULL DEFAULT '[]'         -- JSON 数组（41 关卡 3028001~3028041）
);
CREATE TABLE IF NOT EXISTS mythic_final_progress (   -- sc_44023 终焉神话玩家进度
    uid INTEGER PRIMARY KEY,
    now_difficulty INTEGER DEFAULT 0,
    can_choose_list TEXT DEFAULT '[]',               -- JSON 数组
    receive_reward_list TEXT DEFAULT '[]',
    clear_list TEXT DEFAULT '[]',
    challenge_info_json TEXT DEFAULT '[]',           -- 队伍挑战记录
    is_new_difficulty INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS skuld_progress (          -- sc_24051 斯克尔德活动进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 321201
    client_opt_list TEXT DEFAULT '[]',               -- JSON 数组
    point INTEGER DEFAULT 0,
    reward_list TEXT DEFAULT '[]',                   -- 已领积分奖励 id
    mission_json TEXT DEFAULT '[]',                  -- [{id,times}] 关卡通关次数
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS abyss_node_time (         -- sc_23665 深渊赛季节点→最后活动时间戳
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 101~401 节点 / 1001~1003 赛季条目（abysscfg）
    last_ts INTEGER DEFAULT 0,
    times INTEGER DEFAULT 0,
    flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id)
);
CREATE TABLE IF NOT EXISTS abyss_progress (          -- sc_23565 未知玩法多层进度（100000 系列运行时 ID，raw 全量）
    uid INTEGER PRIMARY KEY,
    raw_json TEXT DEFAULT '{}',                      -- 完整 protobuf 结构
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hodur_progress (          -- sc_23885 霍德尔记录复现活动进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 3941101
    chapter_id INTEGER NOT NULL,                     -- 1~3（4 极限研习未开始）
    option_id_list TEXT DEFAULT '[]',                -- 已选事件选项
    stage_clear_json TEXT DEFAULT '[]',              -- [{stage_id, clear}]
    stat_json TEXT DEFAULT '{}',                     -- 章节统计 {a,b,c}
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS hero_trial (              -- sc_11035 英雄试炼/角色试用活动
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4310701
    activity_info_json TEXT DEFAULT '[]',            -- [{id, challenge_state}]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS idol_pvp_stage (          -- sc_58173 偶像练习生 PVP 舞台
    uid INTEGER PRIMARY KEY,
    stage_id INTEGER DEFAULT 0,                      -- 10013
    refresh_timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS seven_day_skin_new (      -- sc_11097 七日皮肤签到（新）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4300102
    unlock_reward TEXT DEFAULT '[]',
    gain_reward TEXT DEFAULT '[]',
    unlock_times INTEGER DEFAULT 0,
    gift_reward INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS rhythm_game_progress (    -- sc_17588 节奏游戏关卡进度
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 5190300~5190317
    star_id_list TEXT DEFAULT '[]',                  -- [101,102,103]
    use_seconds INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS resident_music_record (    -- sc_61047 浮光绎曲乐曲挑战记录
    uid INTEGER NOT NULL,
    music_id INTEGER NOT NULL,                       -- ActivityMusicCfg id (1~166)
    score INTEGER DEFAULT 0,                         -- 历史最高分
    sign INTEGER DEFAULT 0,                          -- 判定状态 (0: 未完成, 1: 完成, 2: NoMistake 全连, 3: Perfect 全P)
    is_rewarded INTEGER DEFAULT 0,                   -- 奖励是否已领 (0: 未领, 1: 已领)
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, music_id)
);
CREATE TABLE IF NOT EXISTS rhythm_story_record (       -- sc_83124 鸣律探微剧情阅览记录
    uid INTEGER NOT NULL,
    story_id INTEGER NOT NULL,                       -- 已阅览剧情 ID (如 911001011)
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, story_id)
);
CREATE TABLE IF NOT EXISTS summer_pub_record (          -- sc_89011 食与异世界/夏日餐厅经营记录
    uid INTEGER NOT NULL,
    stage_list TEXT DEFAULT '[]',                      -- 通关弹珠关卡列表 [40301, ...]
    cook_stages TEXT DEFAULT '{}',                     -- 料理制作状态 {"10100": 2, ...} (1=做完, 2=看CG)
    battle_stages TEXT DEFAULT '[]',                   -- 通关战斗剧情列表 [4030101, ...]
    illustrated TEXT DEFAULT '{}',                     -- 图鉴状态 {"40301": 2, ...} (1=未读红点, 2=已读)
    time_state INTEGER DEFAULT 1,                      -- 昼夜状态 (1=白天, 2=黑夜)
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);
CREATE TABLE IF NOT EXISTS rogue_card_game_record (      -- sc_89601 诡谈夜话/卡牌构筑玩法记录
    uid INTEGER NOT NULL,
    thread_id INTEGER DEFAULT 0,                      -- 当前进行中的主线帖子ID (0表示无)
    thread_state INTEGER DEFAULT 0,                   -- 当前主线帖子状态
    battle_id INTEGER DEFAULT 0,                      -- 当前主线帖子分配的对局ID
    finish_thread_list TEXT DEFAULT '[]',             -- 已完成的主线帖子ID列表 [107, 106, 105, 104, 103, 102, 101]
    like_thread_list TEXT DEFAULT '[]',               -- 已点赞的帖子ID列表 []
    post_thread_list TEXT DEFAULT '[]',               -- 已发布的帖子ID列表 []
    view_thread_list TEXT DEFAULT '[]',               -- 已阅览的帖子ID列表 [311, 312, ...]
    challenge_deck INTEGER DEFAULT 0,                 -- 当前进行中的挑战卡组 (0表示无)
    challenge_diff INTEGER DEFAULT 0,                 -- 当前挑战难度
    challenge_state INTEGER DEFAULT 0,                -- 当前挑战状态
    challenge_battle_id INTEGER DEFAULT 0,            -- 当前挑战分配的对局ID
    challenge_status_list TEXT DEFAULT '[]',          -- 各卡组最高通关记录 [{"deck": 2, "max_diff": 1}, {"deck": 3, "max_diff": 1}]
    gather_card_list TEXT DEFAULT '[]',               -- 收集的卡牌ID列表 [305, 121, ...]
    gather_enhance_list TEXT DEFAULT '[]',            -- 收集的符文ID列表 [303, 105, ...]
    gather_weal_woe_list TEXT DEFAULT '[]',           -- 收集的吉凶ID列表 [103, 111, ...]
    save_data TEXT DEFAULT '{}',                      -- 局内暂存数据 (impermanence_save_data_net_rec 结构)
    save_rollback TEXT DEFAULT '{}',                  -- 局内回滚存档
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);
CREATE TABLE IF NOT EXISTS monster_cosplay_cfg (     -- sc_17865 怪物扮演静态配置（词缀映射+关卡列表，无 uid 全服一份）
    activity_id INTEGER NOT NULL,                    -- 282991
    chapter_id INTEGER NOT NULL,                     -- 6108/6109
    affix_map_json TEXT DEFAULT '[]',                -- [{affix_id, stage_id}] 词缀→技能教学关
    stage_id_list TEXT DEFAULT '[]',                 -- 全部关卡 id
    PRIMARY KEY (activity_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS monster_cosplay_progress (-- sc_17865 怪物扮演玩家进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    max_stage_id INTEGER DEFAULT 0,                  -- 当前最高解锁/挑战关
    score INTEGER DEFAULT 0,
    score2 INTEGER DEFAULT 0,
    flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, chapter_id)
);
CREATE TABLE IF NOT EXISTS quanzhou_sail_progress (  -- sc_20499 泉州箱庭航行解锁进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323841
    skill_id_list TEXT DEFAULT '[]',                 -- 已解锁航行技能（1001~2003）
    stage_id_list TEXT DEFAULT '[]',                 -- 已解锁航行关卡（400001~400106）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS quanzhou_level_progress ( -- sc_20501 泉州箱庭航行见闻等级+档位奖励
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323841
    level INTEGER DEFAULT 0,                         -- 见闻等级（10 满级）
    reward_id_list TEXT DEFAULT '[]',                -- 已领档位奖励（32364101~32364110）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS growth_task_state (       -- sc_23475 成长阶段任务状态（主任务 4 位 + 子任务 8 位统一）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 40301~40314 主任务 / 4030101~4030308 子任务
    state INTEGER DEFAULT 0,                         -- 2=已完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);
CREATE TABLE IF NOT EXISTS chapter_state (           -- sc_23475 主线章节状态
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,                     -- 10100~10800
    state INTEGER DEFAULT 0,                         -- 2=已完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id)
);
CREATE TABLE IF NOT EXISTS achievement_snapshot (    -- sc_9473 成就快照（raw 全量兜底；真实成就状态走 achievement 表）
    uid INTEGER PRIMARY KEY,
    f1_stat INTEGER DEFAULT 0,
    f2_stat INTEGER DEFAULT 0,
    achievement_json TEXT DEFAULT '[]',              -- [{achievement_id, p2,p3,p4}] 16 个（含 8 个未知 id）
    id20xx_list TEXT DEFAULT '[]',                   -- 24 个未知 id
    f5_list TEXT DEFAULT '[]',
    ts INTEGER DEFAULT 0,                            -- 1786568400
    raw_json TEXT DEFAULT '{}',                      -- 完整 protobuf 结构
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rogueteam_progress (      -- sc_22769 RogueTeam 玩家总览
    uid INTEGER PRIMARY KEY,
    mode_flag INTEGER DEFAULT 0,                     -- 100001 玩法模式标识
    f2_flag INTEGER DEFAULT 0,                       -- 帧 f2 状态位
    f4_count_json TEXT DEFAULT '[]',                 -- [{group,count}] 组计数
    f5_mark_json TEXT DEFAULT '[]',                  -- [{group,count}] 标记
    top_tech_node INTEGER DEFAULT 0,                 -- 最高科技树节点 110160
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rogueteam_item (          -- sc_22769 RogueTeam 道具明细（f3 各组展开）
    uid INTEGER NOT NULL,
    category_id INTEGER NOT NULL,                    -- 组1~5（1=141/142/143系, 2=10000~60050系, 3=131/132/133系, 4=1200001系, 5=初始标记[6,1,2,3]）
    item_id INTEGER NOT NULL,                        -- 道具 id（rogueteamitemcfg）
    name TEXT DEFAULT '',
    in_f6 INTEGER DEFAULT 0,                         -- 是否在 f6/f7 列表（131/132/133 系）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, category_id, item_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_tech (          -- sc_22769 RogueTeam 科技树节点
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 10101~11299 已解锁节点
    name TEXT DEFAULT '',
    cost INTEGER DEFAULT 0,                          -- 配置消耗
    level INTEGER DEFAULT 0,                         -- 配置等级
    unlock INTEGER DEFAULT 1,                        -- 已解锁标记
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_map (           -- sc_24165 RogueTeam 地图节点进度
    uid INTEGER NOT NULL,
    map_id INTEGER NOT NULL,                         -- 1001~3005（rogueteammapcfg 节点）
    status INTEGER DEFAULT 1,                        -- 1=已达成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, map_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_user (           -- sc_88305 虚构推演用户外围状态
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    difficult INTEGER DEFAULT 0,
    max_difficult INTEGER DEFAULT 1,
    last_score_id INTEGER DEFAULT 0,
    point INTEGER DEFAULT 0,
    rewarded_list_json TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_tree (           -- sc_88305 / sc_88301 科技树节点
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    node_id INTEGER NOT NULL,
    unlock_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id, node_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_illustrated (    -- sc_88305 / sc_88313 图鉴与收集品
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    item_id INTEGER NOT NULL,
    item_type INTEGER DEFAULT 1,
    is_viewed INTEGER DEFAULT 0,
    unlock_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id, item_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_history (        -- sc_88305 / sc_88307 通关历史统计
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    diff_clear_json TEXT DEFAULT '[]',                -- [{key: diff_id, value: count}]
    ending_pass_json TEXT DEFAULT '[]',               -- [{key: ending_id, value: count}]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id)
);
CREATE TABLE IF NOT EXISTS rogueteam_session (        -- sc_88001 / 88xxx 局内状态机
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    difficult INTEGER DEFAULT 1,
    floor_num INTEGER DEFAULT 1,
    floor_state INTEGER DEFAULT 0,
    select_node_id INTEGER DEFAULT 0,
    map_info_json TEXT DEFAULT '[]',
    second_map_json TEXT DEFAULT '[]',
    treasure_list_json TEXT DEFAULT '[]',
    other_item_list_json TEXT DEFAULT '[]',
    effect_list_json TEXT DEFAULT '[]',
    hero_list_json TEXT DEFAULT '[]',
    attr_list_json TEXT DEFAULT '[]',
    shop_info_json TEXT DEFAULT '{}',
    other_info_json TEXT DEFAULT '{}',
    affix_list_json TEXT DEFAULT '[]',
    room_passed_count INTEGER DEFAULT 0,
    battle_passed_count INTEGER DEFAULT 0,
    score_rate INTEGER DEFAULT 100,
    in_game INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id)
);
CREATE TABLE IF NOT EXISTS sp_hero_boss_progress (   -- sc_17484 SP 英雄挑战·挑战关卡(Boss战, 242851)
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242851
    boss_stage_id INTEGER DEFAULT 0,                 -- 5170222 重量诱导
    score INTEGER DEFAULT 0,                         -- 最高得分 4,500,000
    achieved_list TEXT DEFAULT '[]',                 -- [401,402,403,406] 达成/领取项
    fight_cnt INTEGER DEFAULT 0,                     -- f3 战斗计数
    flag INTEGER DEFAULT 0,                          -- f5 侵蚀度/计数
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS sp_hero_puzzle_progress ( -- sc_17485 SP 英雄挑战·解密玩法(242871)
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242871
    cleared_stage_list TEXT DEFAULT '[]',            -- [2428701~2428720] 通关关卡
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS equip_seizure_progress (  -- sc_35011 装备侵夺
    uid INTEGER PRIMARY KEY,
    stage_id INTEGER DEFAULT 0,                      -- 3070117
    challenge_rate REAL DEFAULT 1.0,                 -- 挑战倍率 double
    affix_id_list TEXT DEFAULT '[]',                 -- [9206,9220] 随机关卡词缀
    affix_refresh_ts INTEGER DEFAULT 0,              -- 词缀刷新时间
    today_max_score INTEGER DEFAULT 0,
    sum_score INTEGER DEFAULT 0,
    refresh_ts INTEGER DEFAULT 0,                    -- 活动刷新时间
    got_reward_id_list TEXT DEFAULT '[]',
    last_day_tag INTEGER DEFAULT 0,                  -- 记录最后更新的自然日 05:00
    team_heroes TEXT DEFAULT '[]',                   -- 编队出战英雄列表
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS boss_challenge_progress ( -- sc_45201 Boss 挑战入口
    uid INTEGER PRIMARY KEY,
    mode INTEGER DEFAULT 0,                          -- 0=未选择
    next_refresh_time INTEGER DEFAULT 0,
    difficulty_list TEXT DEFAULT '[]',               -- [3,4,101,102] 开放难度
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pinball_progress (        -- sc_23865 弹球玩法进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 3840801 4.6远村异闻
    stage_count INTEGER DEFAULT 0,                   -- 关卡总数 13
    cleared_stage_list TEXT DEFAULT '[]',            -- 已通关普通关（40601~40612）
    unlock_skill_list TEXT DEFAULT '[]',             -- 已解锁技能（12 个）
    equip_skill_list TEXT DEFAULT '[]',              -- 携带主动技能（2 个）
    score INTEGER DEFAULT 0,                         -- 累计/挑战分 130367
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS pt_roulette_progress (    -- sc_10615 活动 PT 轮盘进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4324301 联防协议·α
    clear_stage_list TEXT DEFAULT '[]',              -- 已清轮盘战斗关（5350301~5350304）
    clear_times INTEGER DEFAULT 0,                   -- 清关次数 395
    pool_id INTEGER DEFAULT 0,                       -- 随机词缀池 502002
    up_select INTEGER DEFAULT 0,
    buff_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS activity_buff_rotation (  -- sc_22779 队伍肉鸽 buff 轮换（活动 1000）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 1000
    period INTEGER DEFAULT 0,                        -- 轮换期数 5
    rotation_ts INTEGER DEFAULT 0,                   -- 轮换时间 1786914000
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS chapter_v2_map_node (     -- sc_24065 章节V2地图事件节点（行化）
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 帧小 ID（101~313）
    ev_type INTEGER DEFAULT 0,                       -- 1主线40801xx/2支线40802xx/3补充40803xx/0运行时
    list_type TEXT DEFAULT '',                       -- 来源组 f1f4/f1f7/f3/f4/f5
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id, list_type)
);
CREATE TABLE IF NOT EXISTS sp_hero_challenge_schedule ( -- sc_17480 薇儿丹蒂挑战日程记录
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242841
    schedule_type INTEGER DEFAULT 0,                 -- 1~5
    schedule_id INTEGER NOT NULL,                    -- 2428401~2428406
    ts INTEGER DEFAULT 0,                            -- 1786337562
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, schedule_id, schedule_type)
);
CREATE TABLE IF NOT EXISTS sp_hero_challenge_stage ( -- sc_17480 薇儿丹蒂专属关卡进度（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242841
    stage_id INTEGER NOT NULL,                       -- 5170201~5170206 解锁 / 5170211~5170219 进度组
    stage_kind TEXT DEFAULT '',                      -- unlock(已解锁) / progress(进度组)
    group_type INTEGER DEFAULT 0,                    -- 进度组类型 2/3/4
    value INTEGER DEFAULT 0,                         -- 进度值 3000
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, stage_id)
);
CREATE TABLE IF NOT EXISTS activity_refresh_time (   -- sc_64031 活动刷新时间
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 9 / 4335001
    refresh_time INTEGER NOT NULL,                   -- 下次刷新时间戳
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, refresh_time)
);
CREATE TABLE IF NOT EXISTS kadas_race_progress (     -- sc_18795 卡达斯假日赛状态
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4343601
    node INTEGER DEFAULT 0,                          -- 当前节点 312
    node2 INTEGER DEFAULT 0,                         -- 配套节点 312
    count INTEGER DEFAULT 0,                         -- 计数 106
    status INTEGER DEFAULT 0,
    ts INTEGER DEFAULT 0,                            -- 时间戳（-1 未记录）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS sandplay_progress (       -- sc_14065 泉州沙盘玩法状态
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323641
    map_id INTEGER DEFAULT 0,                        -- 1110101
    pos_x REAL DEFAULT 0.0,                          -- 52.69
    pos_y REAL DEFAULT 0.0,                          -- 18.76
    pos_z REAL DEFAULT 0.0,
    rot REAL DEFAULT 0.0,                            -- 朝向 0.757
    scale1 REAL DEFAULT 1.0,                         -- 2.00
    scale2 REAL DEFAULT 1.0,                         -- 0.653
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS mythic_public (           -- sc_44019 MyTHIC 幻境玩家公共（普通神话）
    uid INTEGER PRIMARY KEY,
    current_difficulty INTEGER DEFAULT 0,            -- 当前选择难度
    is_new_difficulty INTEGER DEFAULT 0,
    next_refresh_ts INTEGER DEFAULT 0,               -- sc_44007 f5
    recommend_team_1 INTEGER DEFAULT 0,              -- sc_44007 f6
    recommend_team_2 INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mythic_difficulty (       -- sc_44019 已开放难度（行化）
    uid INTEGER NOT NULL,
    difficulty_id INTEGER NOT NULL,                  -- 1~13 / 1001=难度Ω
    is_open INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty_id)
);
CREATE TABLE IF NOT EXISTS mythic_partition (        -- sc_44007 难度分区（52 行）
    uid INTEGER NOT NULL,
    difficulty INTEGER NOT NULL,                     -- 1~13
    partition_id INTEGER NOT NULL,                   -- 主分区=难度 / 子分区=难度×100+1..3
    stage_id INTEGER NOT NULL,                       -- 3026001~3026013 / 3027001~3027039
    kind TEXT DEFAULT 'main',                        -- main/sub
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty, partition_id)
);
CREATE TABLE IF NOT EXISTS mythic_affix (            -- sc_44007 当期词缀（9 行）
    uid INTEGER NOT NULL,
    affix_kind TEXT NOT NULL,                        -- advantage/inferiority/ultimate
    affix_id INTEGER NOT NULL,                       -- 417 光增伤等
    level INTEGER DEFAULT 0,
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, affix_kind, affix_id)
);
CREATE TABLE IF NOT EXISTS mythic_progress_partition ( -- sc_44009 玩家通关分区/星数（行化）
    uid INTEGER NOT NULL,
    partition_id INTEGER NOT NULL,
    star INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, partition_id)
);
CREATE TABLE IF NOT EXISTS mythic_progress_reward (  -- sc_44009 已领星级奖励档位（行化）
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);
CREATE TABLE IF NOT EXISTS osiris_stage_progress (   -- sc_2635 奥西里斯玩法关卡进度（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 5260111~5260163（6章×3）
    score INTEGER DEFAULT 0,                         -- 分数
    settle INTEGER DEFAULT 0,                        -- 结算值
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS osiris_chapter_progress ( -- sc_2635 奥西里斯 6 章进度标记
    uid INTEGER NOT NULL,
    chapter_no INTEGER NOT NULL,                     -- 1~6
    value INTEGER DEFAULT 0,                         -- 6/5/4/3/2/1
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_no)
);
CREATE TABLE IF NOT EXISTS activity_point (          -- sc_60201 活动累计积分
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323801 奥西里斯 / 3639701 灰烬
    point INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS activity_point_reward (   -- sc_60201 已领积分奖励档位（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,                      -- 32380101~18 / 363970101~110
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, reward_id)
);
CREATE TABLE IF NOT EXISTS core_verification_stage ( -- sc_23477 核心验证挑战关卡成绩
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3093101 主 / 3092101~2 子
    kind TEXT DEFAULT 'main',                        -- main/sub
    max_score INTEGER DEFAULT 0,                     -- 最高分
    last_score INTEGER DEFAULT 0,                    -- 最近分
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS core_verification_stage_score ( -- sc_23477 关卡阶段小计分（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    score_idx INTEGER NOT NULL,                      -- 1~3
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, score_idx)
);
CREATE TABLE IF NOT EXISTS core_verification_stage_affix ( -- sc_23477 主关已选增益词缀（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, affix_id)
);
CREATE TABLE IF NOT EXISTS core_verification_affix ( -- sc_23477 词缀池状态（8 行）
    uid INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,                       -- 740010 技能伤害强化 lv10 等
    level INTEGER DEFAULT 0,                         -- 0=未选
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, affix_id)
);
CREATE TABLE IF NOT EXISTS core_verification_task (  -- sc_23477 挑战任务/徽章（行化）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 50201~50205
    state INTEGER DEFAULT 0,                         -- 2=完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);
CREATE TABLE IF NOT EXISTS skin_draw_pool (          -- sc_2615/2649 T0 皮肤抽取卡池剩余份数（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4322201/4322202 诗蔻蒂 / 4342401/4342402 托特
    drop_id INTEGER NOT NULL,                        -- 奖励格 id（2301~2320/2401~2408/2501~2519/2601~2619）
    remain INTEGER DEFAULT 0,                        -- 剩余份数（=total 未抽）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, drop_id)
);
CREATE TABLE IF NOT EXISTS skin_draw_guarantee (     -- sc_2649 誓约卡池保底记录（行化，语义待确认）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    drop_id INTEGER NOT NULL,                        -- 保底核心格
    idx INTEGER NOT NULL,                            -- 1~10
    value INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, drop_id, idx)
);
CREATE TABLE IF NOT EXISTS skin_draw_story (         -- sc_2625 皮肤抽取剧情进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4322301
    story_stage INTEGER DEFAULT 0,                   -- 当前阶段 1
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS skin_draw_story_finished (-- sc_2625 已完成剧情（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    story_id INTEGER NOT NULL,                       -- 928011001~928041001
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, story_id)
);
CREATE TABLE IF NOT EXISTS ash_shoot_stage (         -- sc_2645 灰烬射击关卡最高分（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 404101~404304
    point INTEGER DEFAULT 0,                         -- 历史最高分
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS cvm4_stage (              -- sc_24265 核心验证 Mode4 关卡（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3092091~3092093/3093091
    stage_index INTEGER DEFAULT 0,
    cur_value INTEGER DEFAULT 0,                     -- 队伍得分
    seconds INTEGER DEFAULT 0,                       -- 通关耗时
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS cvm4_stage_hero (         -- sc_24265 Mode4 上阵队伍（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    hero_idx INTEGER NOT NULL,                       -- 1~3
    hero_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, hero_idx)
);
CREATE TABLE IF NOT EXISTS cvm4_task (               -- sc_24265 Mode4 徽章任务（行化）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 50110~50114
    state INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);
CREATE TABLE IF NOT EXISTS cvm4_meta (               -- sc_24265 Mode4 总览
    uid INTEGER PRIMARY KEY,
    first_enter INTEGER DEFAULT 0,
    max_point INTEGER DEFAULT 0,                     -- 19570
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS skin_pool_unopen (        -- 未开放/已结束皮肤（换装）卡池配置
    pool_id INTEGER PRIMARY KEY,                     -- 1001~1021（奇）
    pool_name TEXT DEFAULT '',
    activity_id INTEGER DEFAULT 0,                   -- 卡池活动 id
    main_activity_id INTEGER DEFAULT 0,
    main_activity_name TEXT DEFAULT '',
    version TEXT DEFAULT '',                         -- 2.2~5.0
    template INTEGER DEFAULT 0,                      -- 222/424
    pair_pool_ids TEXT DEFAULT '',                   -- 配套场景池（逗号分隔，如 "1006,1007"）
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scene_pool_unopen (       -- 未开放/已结束场景卡池配置
    pool_id INTEGER PRIMARY KEY,                     -- 1002~1022（偶）
    pool_name TEXT DEFAULT '',
    activity_id INTEGER DEFAULT 0,
    main_activity_id INTEGER DEFAULT 0,
    main_activity_name TEXT DEFAULT '',
    version TEXT DEFAULT '',
    template INTEGER DEFAULT 0,                      -- 222/424
    pair_pool_ids TEXT DEFAULT '',                   -- 配套皮肤池
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS newbie_activity (         -- sc_59011 新手活动 Noob
    uid INTEGER PRIMARY KEY,
    completed_time INTEGER DEFAULT 0,                -- 1762739253
    now_sign_times INTEGER DEFAULT 0,                -- 14
    last_sign_ts INTEGER DEFAULT 0,
    fr_first_gear INTEGER DEFAULT 0,                 -- 首充 6 元档
    fr_second_gear INTEGER DEFAULT 0,                -- 首充 18 元档
    fr_now_sign INTEGER DEFAULT 0,
    fr_last_sign_ts INTEGER DEFAULT 0,
    fr_new6 INTEGER DEFAULT 0,
    fr_new18 INTEGER DEFAULT 0,
    mc_flag INTEGER DEFAULT 0,                       -- 月卡已充
    mc_role_flag INTEGER DEFAULT 0,                  -- 月卡角色奖励已领
    mc_sign_times INTEGER DEFAULT 0,                 -- 月卡签到 10
    mc_sign_reward INTEGER DEFAULT 0,
    mc_new_role INTEGER DEFAULT 0,
    mc_new_sign INTEGER DEFAULT 0,
    bp_reward INTEGER DEFAULT 0,                     -- 战令奖励
    bp_new INTEGER DEFAULT 0,
    trigger_time INTEGER DEFAULT 0,                  -- 1760069162
    max_phase INTEGER DEFAULT 0,                     -- 7
    version_id INTEGER DEFAULT 0,                    -- 3
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS newbie_pt_reward (        -- sc_59011 累计任务奖励档位（行化）
    uid INTEGER NOT NULL,
    pt_id INTEGER NOT NULL,                          -- 1~7
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pt_id)
);
CREATE TABLE IF NOT EXISTS newbie_level_reward (     -- sc_59011 升级奖励已领档位（行化）
    uid INTEGER NOT NULL,
    level INTEGER NOT NULL,                          -- 20/30/40/50/60
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, level)
);
CREATE TABLE IF NOT EXISTS guild_boss (              -- sc_33001 公会 Boss 挑战
    club_id INTEGER PRIMARY KEY,
    boss_template_id INTEGER DEFAULT 0,              -- 4010103
    difficulty INTEGER DEFAULT 0,
    total_damage INTEGER DEFAULT 0,
    collective_damage_award INTEGER DEFAULT 0,
    personal_score INTEGER DEFAULT 0,
    day_first_clear_prepose INTEGER DEFAULT 0,
    day_clear_prepose_times INTEGER DEFAULT 0,
    day_first_clear_award_admitted INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS guild_boss_affix (        -- sc_33001 本期 Buff 词缀（行化）
    club_id INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,                       -- 116/117/195/181/183
    level INTEGER DEFAULT 0,
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (club_id, affix_id)
);
CREATE TABLE IF NOT EXISTS guild_boss_prebuff (      -- sc_33001 预置 Buff 关卡成绩（行化）
    club_id INTEGER NOT NULL,
    level_id INTEGER NOT NULL,                       -- 1~5
    stage_id INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (club_id, level_id)
);
CREATE TABLE IF NOT EXISTS backhome_meta (           -- sc_58001 家园总览
    uid INTEGER PRIMARY KEY,
    last_fatigue_update_time INTEGER DEFAULT 0,      -- 1786411476
    exhibition_id INTEGER DEFAULT 0,                 -- 5
    share_is_open INTEGER DEFAULT 0,
    received_be_visited_gift_num INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS backhome_hero (           -- sc_58001 回家英雄（行化）
    uid INTEGER NOT NULL,
    archives_id INTEGER NOT NULL,                    -- 1015 等
    hero_id INTEGER NOT NULL,
    fatigue INTEGER DEFAULT 0,
    feed_times INTEGER DEFAULT 0,
    total_feed_times INTEGER DEFAULT 0,
    is_lock INTEGER DEFAULT 0,
    skin_id INTEGER DEFAULT 0,                       -- 后宅英雄皮肤（58126 切换，BackHomeHeroSkinCfg）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, archives_id)
);
CREATE TABLE IF NOT EXISTS backhome_dorm (           -- sc_58001 宿舍（行化）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,                        -- 5~15
    pos_id INTEGER DEFAULT 0,
    exp INTEGER DEFAULT 0,
    liked_num INTEGER DEFAULT 0,
    be_visited_num INTEGER DEFAULT 0,                -- 被访次数（58054 查询）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_task (   -- 食堂委托任务静态配置（backhomecanteentaskcfg.lua 导入，静态无 uid）
    task_id INTEGER PRIMARY KEY,
    name TEXT DEFAULT '',
    task_level INTEGER DEFAULT 1,                    -- 1/2/3 → base_success 20/25/30
    cost INTEGER DEFAULT 0,                          -- 派遣疲劳消耗（统一 120）
    need_min INTEGER DEFAULT 1,
    need_max INTEGER DEFAULT 3,
    base_success INTEGER DEFAULT 0,
    tag_success INTEGER DEFAULT 5,
    tag_list TEXT DEFAULT '[]',                      -- JSON [[[tag,val],...],...] 需求标签
    tag_max TEXT DEFAULT '[]',
    reward_list TEXT DEFAULT '[]',                   -- JSON [[item_id, num], ...]
    time TEXT DEFAULT '{}'                           -- JSON {"1":[480,100],"2":[720,140],"3":[1200,210]}
);
CREATE TABLE IF NOT EXISTS skin_draw_pool_cfg (      -- 皮肤抽池静态配置（activitylimiteddrawpoollistcfg.lua 导入，静态无 uid）
    pool_id INTEGER PRIMARY KEY,
    draw_name TEXT DEFAULT '',
    token INTEGER DEFAULT 0,                         -- 抽卡凭证 item_id
    cost_once TEXT DEFAULT '[]',                     -- [凭证id, 单抽数]
    cost_ten TEXT DEFAULT '[]',                      -- [凭证id, 十连数]
    flawless_exchange TEXT DEFAULT '{}',             -- 移转之辉直兑 {cost_id,unit,qty,give}
    ascending TEXT DEFAULT '[]'                      -- 移转之花递增档 [{unit,discount,qty,give}, ...]
);
CREATE TABLE IF NOT EXISTS system_closed (            -- [Fix by Gemini 3.7-flash] 关闭系统备份表
    uid INTEGER NOT NULL,
    system_id INTEGER NOT NULL,
    system_name TEXT DEFAULT '',
    is_open INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    remark TEXT DEFAULT '',
    PRIMARY KEY (uid, system_id)
);
CREATE TABLE IF NOT EXISTS backhome_dorm_hero (      -- sc_58001 宿舍驻守英雄（行化）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id, hero_id)
);
CREATE TABLE IF NOT EXISTS backhome_dorm_layout (   -- sc_58003 宿舍家具布局（58010 保存）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,
    layout_json TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id)
);
CREATE TABLE IF NOT EXISTS backhome_dorm_template ( -- sc_58003 宿舍家具预设模板表（58040/58142/58144）
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    type INTEGER DEFAULT 1,
    architecture_id INTEGER DEFAULT 0,
    pos INTEGER DEFAULT 0,
    layout_json TEXT DEFAULT '{}',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id)
);
CREATE TABLE IF NOT EXISTS backhome_dorm_gift (     -- sc_58003 宿舍礼物
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,
    hero_id INTEGER DEFAULT 0,
    furniture_id INTEGER NOT NULL,
    num INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id, furniture_id)
);
CREATE TABLE IF NOT EXISTS backhome_furniture (      -- sc_58001 家具持有（行化）
    uid INTEGER NOT NULL,
    furniture_id INTEGER NOT NULL,                   -- 95xxx/96xxx
    num INTEGER DEFAULT 0,
    give_num INTEGER DEFAULT 0,                      -- 赠送数
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, furniture_id)
);
CREATE TABLE IF NOT EXISTS backhome_ingredient (     -- sc_58001 食材库存（行化）
    uid INTEGER NOT NULL,
    item_id INTEGER NOT NULL,                        -- 51xxx 食材族
    num INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, item_id)
);
CREATE TABLE IF NOT EXISTS backhome_suit (           -- sc_58001 已解锁套件（行化）
    uid INTEGER NOT NULL,
    suit_id INTEGER NOT NULL,                        -- 3011001 等 21 套
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, suit_id)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_entrust (-- sc_58001 食堂委托（行化）
    uid INTEGER NOT NULL,
    pos INTEGER NOT NULL,                            -- 1~4
    task_id INTEGER DEFAULT 0,                       -- 13/7/30006/20003
    hero_list TEXT DEFAULT '',                       -- 逗号分隔 3 英雄
    num_max INTEGER DEFAULT 0,
    refresh_times INTEGER DEFAULT 0,
    start_time INTEGER DEFAULT 0,
    duration INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pos)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_dish (   -- sc_58001 食堂招牌菜（行化）
    uid INTEGER NOT NULL,
    food_id INTEGER NOT NULL,                        -- 106/121
    sell_num INTEGER DEFAULT 0,
    sold_num INTEGER DEFAULT 0,
    sell_earnings INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, food_id)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_career ( -- sc_58001 食堂工作安排（行化）
    uid INTEGER NOT NULL,
    ctype INTEGER NOT NULL,                          -- 1~3
    hero_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, ctype)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_meta (   -- sc_58001 食堂总览
    uid INTEGER NOT NULL,
    canteen_id INTEGER NOT NULL,                     -- 4
    accruing_earnings INTEGER DEFAULT 0,             -- 1847982
    mode INTEGER DEFAULT 0,                          -- 经营模式（58108 切换：0自动/1手动）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, canteen_id)
);
CREATE TABLE IF NOT EXISTS backhome_canteen_furniture (  -- 食堂家具实例（58116 升级）
    uid INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,                      -- 实例 uid（BackHomeCanteenFurnitureIDCfg.entity_id）
    type_id INTEGER DEFAULT 0,                       -- 941001 炒锅等（idcfg.type_id）
    level INTEGER DEFAULT 0,                         -- 升级等级（0/1/2，cost_material 分级）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, entity_id)
);
CREATE TABLE IF NOT EXISTS dorm_information (        -- sc_58063 宿舍资讯（行化）
    uid INTEGER NOT NULL,
    info_id INTEGER NOT NULL,                        -- 1001/2001
    param_idx INTEGER NOT NULL,                      -- 1~7
    param INTEGER DEFAULT 0,                         -- -1 占位
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, info_id, param_idx)
);
CREATE TABLE IF NOT EXISTS unclaimed (               -- sc_12023 未领取挑战补偿
    uid INTEGER NOT NULL,
    unclaimed_id INTEGER NOT NULL,                   -- 1矩阵/2BOSS/3神话/4装备侵夺/5深渊
    stage INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, unclaimed_id)
);
CREATE TABLE IF NOT EXISTS user_setting (            -- sc_21001 提醒设置
    uid INTEGER PRIMARY KEY,
    sign_in_propel_switch INTEGER DEFAULT 1,
    month_card_propel_switch INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reserve_team (            -- sc_63005 预备编队（含芯片）
    uid INTEGER NOT NULL,
    team_type INTEGER NOT NULL,                      -- 0默认/1剧情/104深渊/219区域战/411奥西里斯等
    cont_id INTEGER NOT NULL,
    team_index INTEGER NOT NULL,
    cooperate_skill INTEGER DEFAULT 0,               -- 组合技 id
    mimir_id INTEGER DEFAULT 0,                      -- 钥从 id（0=无）
    chip_ids TEXT DEFAULT '',                        -- 钥从携带芯片（逗号分隔 107,109）
    hero_chip_ids TEXT DEFAULT '',                   -- 英雄芯片（逗号分隔 109451,109452）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, team_type, cont_id, team_index)
);
CREATE TABLE IF NOT EXISTS reserve_team_hero (       -- sc_63005 编队英雄（行化）
    uid INTEGER NOT NULL,
    team_type INTEGER NOT NULL,
    cont_id INTEGER NOT NULL,
    team_index INTEGER NOT NULL,
    slot INTEGER NOT NULL,                           -- 1~3
    hero_id INTEGER NOT NULL,
    owner_id INTEGER DEFAULT 0,
    hero_type INTEGER DEFAULT 0,                     -- 1己方/2试玩
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, team_type, cont_id, team_index, slot)
);
CREATE TABLE IF NOT EXISTS chip_proposal (           -- sc_50001 管理喵芯片方案
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    secondary TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);
CREATE TABLE IF NOT EXISTS abyss (                   -- sc_55001 深渊
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 230102
    is_back INTEGER DEFAULT 0,
    history_max_layer INTEGER DEFAULT 0,
    layer_reset_time INTEGER DEFAULT 0,
    stage_reset_time INTEGER DEFAULT 0,
    last_version_max_unlock_layer INTEGER DEFAULT 0,
    refresh_timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS abyss_layer_stage (       -- sc_55001 深渊层关卡（行化）
    uid INTEGER NOT NULL,
    layer_id INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3060004
    is_completed INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, layer_id, stage_id)
);
CREATE TABLE IF NOT EXISTS abyss_reward (            -- sc_55001 已领层奖励（行化）
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_meta (         -- sc_18001 多面体/矩阵总览
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4307301
    game_state INTEGER DEFAULT 0,
    reset_times INTEGER DEFAULT 0,
    already_challenge_times INTEGER DEFAULT 0,
    is_new INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_terminal (     -- sc_18001 终端升级（行化）
    uid INTEGER NOT NULL,
    terminal_id INTEGER NOT NULL,                    -- 62~63 个
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, terminal_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_beacon (       -- sc_18001 信标（行化）
    uid INTEGER NOT NULL,
    beacon_id INTEGER NOT NULL,                      -- 1~14
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, beacon_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_hero (         -- sc_18001 玩法英雄+星盘（行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,                        -- 1035~1284
    astrolabe_1 INTEGER DEFAULT 0,
    astrolabe_2 INTEGER DEFAULT 0,
    astrolabe_3 INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_difficulty (   -- sc_18001 已通关难度（行化）
    uid INTEGER NOT NULL,
    difficulty_id INTEGER NOT NULL,                  -- 1/6/11/16~19
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_artifact (     -- sc_18001 神器图鉴样本（行化）
    uid INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,                    -- 70501~70867 等
    state INTEGER DEFAULT 0,                         -- 1待查看/2已查看
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, artifact_id)
);
CREATE TABLE IF NOT EXISTS polyhedron_run (          -- sc_18003 局内探索实时对局快照
    uid INTEGER NOT NULL PRIMARY KEY,
    state INTEGER DEFAULT 1,                         -- 1=NOTSTARTED, 2=STARTED, 3=SETTLEMENT
    tier_id INTEGER DEFAULT 1001,                    -- 当前层级 (1001~3010)
    run_data TEXT,                                   -- 局内完整对局 JSON 快照
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS polyhedron_policy_claimed (-- sc_18001 多维历程已领取等级
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, level)
);
CREATE TABLE IF NOT EXISTS music_challenge (         -- sc_61047 音乐挑战谱面成绩
    uid INTEGER NOT NULL,
    challenge_id INTEGER NOT NULL,                   -- 92~166
    score INTEGER DEFAULT 0,                         -- 最高分
    sign INTEGER DEFAULT 0,                          -- 1完成/0未完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, challenge_id)
);
CREATE TABLE IF NOT EXISTS fukubukuro (              -- sc_17021 福袋实例
    uid INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,                    -- 福袋实例 id
    item_id INTEGER DEFAULT 0,                       -- 福袋道具 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, instance_id)
);
CREATE TABLE IF NOT EXISTS fukubukuro_select (       -- sc_17021 福袋候选列表（行化）
    uid INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,
    select_idx INTEGER NOT NULL,                     -- 候选序号
    select_item_id INTEGER DEFAULT 0,
    num INTEGER DEFAULT 0,
    time_valid INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, instance_id, select_idx)
);
CREATE TABLE IF NOT EXISTS fukubukuro_cfg (          -- 福袋配置（itemcfg sub_type=513 FUKUBUKURO）
    item_id INTEGER PRIMARY KEY,                     -- 30041/30043/30060
    name TEXT DEFAULT '',
    desc TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    total_weight INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fukubukuro_cfg_select (   -- 福袋候选池（行化）
    item_id INTEGER NOT NULL,                        -- 福袋道具 id
    select_idx INTEGER NOT NULL,                     -- 候选序号 1~5
    select_item_id INTEGER NOT NULL,                 -- 候选物品 id
    num INTEGER DEFAULT 0,
    weight INTEGER DEFAULT 0,
    select_name TEXT DEFAULT '',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (item_id, select_idx)
);
CREATE TABLE IF NOT EXISTS hero_oath (               -- sc_14501/14579 英雄誓约信息（hero_oath_net_rec）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,                        -- 誓约英雄
    oath INTEGER DEFAULT 0,                          -- bool 是否已誓约
    oath_level INTEGER DEFAULT 0,                    -- 誓约等级
    nick TEXT DEFAULT '',                            -- 自定义昵称
    picture_link TEXT DEFAULT '',                    -- 誓约照片链接
    oath_time INTEGER DEFAULT 0,                     -- 誓约时间
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id)
);
CREATE TABLE IF NOT EXISTS hero_oath_plot (          -- 誓约剧情已读（oath_plot.text_list 行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    plot_idx INTEGER NOT NULL,
    text_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, plot_idx)
);
CREATE TABLE IF NOT EXISTS hero_oath_time (          -- 誓约时间列表（time_list 行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    time_idx INTEGER NOT NULL,
    oath_time INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, time_idx)
);
CREATE TABLE IF NOT EXISTS oath_assignment (         -- sc_14503 誓约任务进度行化
    uid INTEGER NOT NULL,
    assignment_id INTEGER NOT NULL,
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, assignment_id)
);
CREATE TABLE IF NOT EXISTS oath_assignment_meta (    -- sc_14503 誓约任务元信息
    uid INTEGER PRIMARY KEY,
    send_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS idol_trainee (            -- sc_58169 练舞房总览
    uid INTEGER PRIMARY KEY,
    attack_hero_id INTEGER DEFAULT 0,                -- 进攻阵容 1084
    defend_hero_id INTEGER DEFAULT 0,                -- 防守阵容 1084
    exercise_use_times INTEGER DEFAULT 0,            -- 已用训练次数 0
    camp_list TEXT DEFAULT '',                       -- 阵营 buff（逗号分隔 2,5）
    pvp_stage_id INTEGER DEFAULT 0,                  -- PVP 舞台 10013
    pvp_refresh_ts INTEGER DEFAULT 0,                -- 1786482000
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS idol_trainee_hero (       -- sc_58169 练舞五维属性（行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    attr1 INTEGER DEFAULT 0,
    attr2 INTEGER DEFAULT 0,
    attr3 INTEGER DEFAULT 0,
    attr4 INTEGER DEFAULT 0,
    attr5 INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id)
);
CREATE TABLE IF NOT EXISTS idol_trainee_pos (        -- sc_58169 舞台站位（行化）
    uid INTEGER NOT NULL,
    pos INTEGER NOT NULL,                            -- 1~5
    hero_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pos)
);
CREATE TABLE IF NOT EXISTS idol_trainee_pve (        -- sc_58189 练舞 PVE 章节进度（行化）
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,                     -- 1 挑战
    stage_id INTEGER NOT NULL,                       -- 101~103
    score INTEGER DEFAULT 0,
    is_clear INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, stage_id)
);
CREATE TABLE IF NOT EXISTS idol_trainee_rank (       -- sc_58191 练舞排行
    uid INTEGER PRIMARY KEY,
    weekly_point INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS idol_trainee_rank_got (   -- sc_58191 已领排行档位（行化）
    uid INTEGER NOT NULL,
    rank_id INTEGER NOT NULL,                        -- 1~4
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, rank_id)
);
CREATE TABLE IF NOT EXISTS summer_chess_point (      -- sc_19565 夏日棋盘积分奖励档位（行化）
    uid INTEGER NOT NULL,
    point_id INTEGER NOT NULL,                       -- 24190001~24190043
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, point_id)
);
CREATE TABLE IF NOT EXISTS summer_chess_node (       -- sc_19569 夏日棋盘节点条目（行化）
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 1/4/5/12~16/31/32
    item_id INTEGER NOT NULL,                        -- 222001~222015 等
    state INTEGER DEFAULT 0,                         -- 1=完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id, item_id)
);
CREATE TABLE IF NOT EXISTS dance_diy (               -- sc_58195 练舞房 DIY 舞蹈序列
    uid INTEGER NOT NULL,
    pos INTEGER NOT NULL,                            -- 槽位 1..N
    scene_id INTEGER DEFAULT 0,
    music_id INTEGER DEFAULT 0,
    action_list TEXT DEFAULT '',                     -- 逗号分隔动作 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pos)
);
CREATE TABLE IF NOT EXISTS idol_dance_collection (   -- sc_58203 舞蹈收集观看记录
    uid INTEGER NOT NULL,
    collect_type INTEGER NOT NULL,                   -- 1/2/3
    viewed_id_list TEXT DEFAULT '',                  -- 逗号分隔已观看 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, collect_type)
);
CREATE TABLE IF NOT EXISTS activity_extra (          -- sc_9481 活动通用标志
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4335001 古远虚影(心魔挑战)
    flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);
CREATE TABLE IF NOT EXISTS qworld_quest (            -- sc_28801 泉州箱庭主线组
    uid INTEGER NOT NULL,
    main_quest_id INTEGER NOT NULL,                  -- 11101~11350/101/201/202
    status INTEGER DEFAULT 0,                        -- 9完成/3进行中
    expired_ts INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, main_quest_id)
);
CREATE TABLE IF NOT EXISTS qworld_quest_task (       -- sc_28801 泉州箱庭子任务（行化）
    uid INTEGER NOT NULL,
    main_quest_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    status INTEGER DEFAULT 0,                        -- 1完成/0进行
    progress INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, main_quest_id, task_id)
);
CREATE TABLE IF NOT EXISTS activity_score (          -- sc_60105 常驻积分
    uid INTEGER NOT NULL,
    score_key INTEGER NOT NULL,                      -- 常驻积分 key
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, score_key)
);
CREATE TABLE IF NOT EXISTS activity_reward (         -- sc_60107 常驻积分已领奖励
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);
CREATE TABLE IF NOT EXISTS regression (              -- sc_62019 回归活动抽奖
    uid INTEGER PRIMARY KEY,
    other_sign INTEGER DEFAULT 0,
    draw_over_sign INTEGER DEFAULT 0,
    received_sign_list TEXT DEFAULT '[]',
    find_time INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS core_verification_badge ( -- sc_23485 核心验证挑战图鉴徽章（行化）
    uid INTEGER NOT NULL,
    badge_id INTEGER NOT NULL,                       -- 40605/40705/408xx/409xx/410xx/501xx/502xx
    unlock_ts INTEGER DEFAULT 0,                     -- 解锁时间戳
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, badge_id)
);
CREATE TABLE IF NOT EXISTS accumulate_sign (         -- sc_17027 累计登录签到
    uid INTEGER PRIMARY KEY,
    version INTEGER DEFAULT 0,                       -- 2
    open_sign INTEGER DEFAULT 0,
    login_days INTEGER DEFAULT 0,                    -- 104
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accumulate_sign_award (   -- sc_17027 已领累计奖励档位（行化）
    uid INTEGER NOT NULL,
    award_id INTEGER NOT NULL,                       -- 9/10（40/80 天档）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, award_id)
);
CREATE TABLE IF NOT EXISTS accumulate_sign_discount (-- sc_17033 累计签到折扣购买
    uid INTEGER PRIMARY KEY,
    version INTEGER DEFAULT 0,
    cumulative_buy_card_num INTEGER DEFAULT 0,       -- 月卡购买数 0
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accumulate_sign_bp (      -- sc_17033 已购战令档位（行化）
    uid INTEGER NOT NULL,
    bp_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, bp_id)
);
CREATE TABLE IF NOT EXISTS daily_fatigue (          -- sc_12045 每日体力甜点（午晚领蛋）
    uid INTEGER NOT NULL,
    type INTEGER NOT NULL,                           -- 11=午间, 18=晚间
    date_str TEXT NOT NULL,                          -- YYYY-MM-DD
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, type, date_str)
);
CREATE TABLE IF NOT EXISTS player_skin (             -- sc_14201 管理员玩家皮肤总览
    uid INTEGER PRIMARY KEY,
    player_id INTEGER DEFAULT 0,                     -- 1001
    using_skin INTEGER DEFAULT 0,                    -- 使用中皮肤 0
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_skin_unlocked (    -- sc_14201 已解锁皮肤（行化）
    uid INTEGER NOT NULL,
    skin_id INTEGER NOT NULL,                        -- 100102
    unlock_ts INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, skin_id)
);
CREATE TABLE IF NOT EXISTS oath_assignment (         -- sc_14503 誓约派遣任务（行化）
    uid INTEGER NOT NULL,
    assignment_id INTEGER NOT NULL,
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, assignment_id)
);
CREATE TABLE IF NOT EXISTS oath_assignment_meta (    -- sc_14503 誓约派遣总览
    uid INTEGER PRIMARY KEY,
    send_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS autochess_card (          -- sc_24515 自走棋卡牌持有（行化）
    uid INTEGER NOT NULL,
    card_id INTEGER NOT NULL,                        -- 10021~14153（135 张）
    num INTEGER DEFAULT 0,                           -- 持有数量
    seq INTEGER DEFAULT 0,                           -- 抓包素材原始顺序
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, card_id)
);
CREATE TABLE IF NOT EXISTS autochess_meta (          -- sc_24515 自走棋卡牌总览
    uid INTEGER PRIMARY KEY,
    rank_score INTEGER DEFAULT 0,                    -- 4800
    energy INTEGER DEFAULT 0,                        -- 28
    sunglasses INTEGER DEFAULT 0,                    -- 1
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS autochess_stage (         -- sc_89201 关卡通关状态
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    clear_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS autochess_medal (         -- sc_89201 徽章持有
    uid INTEGER NOT NULL,
    medal_id INTEGER NOT NULL,
    level INTEGER DEFAULT 1,
    unlock_time INTEGER DEFAULT 0,
    upgrade_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, medal_id)
);
CREATE TABLE IF NOT EXISTS autochess_session (       -- 局内状态恢复与断点
    uid INTEGER PRIMARY KEY,
    game_type INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0,
    stage_id INTEGER DEFAULT 0,
    round INTEGER DEFAULT 0,
    gold INTEGER DEFAULT 0,
    hp INTEGER DEFAULT 100,
    win_count INTEGER DEFAULT 0,
    defeat_count INTEGER DEFAULT 0,
    shop_level INTEGER DEFAULT 1,
    chess_board_json TEXT,
    bench_chess_json TEXT,
    shop_items_json TEXT,
    buff_list_json TEXT,
    update_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS friends (                 -- sc_19029 好友（行化）
    uid INTEGER NOT NULL,
    friend_uid INTEGER NOT NULL,                     -- 2149618442
    timestamp INTEGER DEFAULT 0,                     -- 加好友时间
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, friend_uid)
);
CREATE TABLE IF NOT EXISTS friend_request (          -- sc_19029 好友申请（行化）
    uid INTEGER NOT NULL,
    request_uid INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, request_uid)
);
CREATE TABLE IF NOT EXISTS friend_blacklist (        -- sc_19029 黑名单（行化）
    uid INTEGER NOT NULL,
    black_uid INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, black_uid)
);
CREATE TABLE IF NOT EXISTS friend_chat (             -- sc_19039 好友聊天离线消息
    uid INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    sender_uid INTEGER DEFAULT 0,
    peer_uid INTEGER DEFAULT 0,                       -- 会话对端 UID，用于精确清理玩家发出的消息
    content TEXT DEFAULT '',
    timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, msg_id)
);
CREATE TABLE IF NOT EXISTS momotalk_session (        -- sc_25465 MomoTalk 聊天对象会话摘要（行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,                        -- 1015 塞赫麦特/1020 梵天/1022 塞勒涅/9002 第九部门
    session_id INTEGER NOT NULL,                     -- 父ID×100+序号（900201 等）
    send_time INTEGER DEFAULT 0,                     -- 最近消息时间
    is_view INTEGER DEFAULT 0,
    content_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, session_id)
);
CREATE TABLE IF NOT EXISTS momotalk_hero (           -- ChatHeroCfg 聊天对象（静态全量）
    hero_id INTEGER PRIMARY KEY,                     -- 1011 奥努里斯 等 74 个
    name TEXT DEFAULT '',
    icon TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS momotalk_message (        -- ChatMessageCfg 会话消息（静态全量）
    message_id INTEGER PRIMARY KEY,                  -- 101101 等 266 条
    mtype INTEGER DEFAULT 0,                         -- type
    sender INTEGER DEFAULT 0,                        -- 发送对象
    content_id INTEGER DEFAULT 0,                    -- 引用 chatcontentcfg
    trigger_type INTEGER DEFAULT 0,
    trigger_condition INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS momotalk_content (        -- ChatContentCfg 对话文本（静态全量）
    content_id INTEGER PRIMARY KEY,                  -- 4105 条
    text TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS claim_ledger (            -- 通用领取台账（幂等/防重复发奖）
    uid       INTEGER NOT NULL,
    kind      TEXT NOT NULL,        -- 领取类别：'relation_story' / 'activity_tier' / ...
    key_id    INTEGER NOT NULL,     -- 该类别下的条目 id（故事 id / 档位 id ...）
    claim_ts  INTEGER DEFAULT 0,
    PRIMARY KEY (uid, kind, key_id)
);
CREATE TABLE IF NOT EXISTS ai_characters (           -- AI 虚拟角色定义（不等于 hero，独立实体）
    char_id         INTEGER PRIMARY KEY,             -- 虚拟 UID (90001001+)
    char_name       TEXT NOT NULL,                   -- 角色名称 ("海拉")
    hero_ids        TEXT DEFAULT '',                 -- 关联 Hero ID 列表 ("1094,1194")
    avatar_icon     INTEGER DEFAULT 0,               -- 头像 ID (匹配游戏资源)
    icon_frame      INTEGER DEFAULT 0,               -- 头像框 ID
    chat_bubble     INTEGER DEFAULT 9001,            -- 聊天气泡 ID (默认 9001)
    info_background INTEGER DEFAULT 0,               -- 名片背景 ID
    level           INTEGER DEFAULT 80,              -- 显示等级 (默认 80)
    sign            TEXT DEFAULT '',                 -- 个性签名
    ip_location     TEXT DEFAULT '',                 -- 归属地/阵营
    greeting_msg    TEXT DEFAULT '',                 -- 初始问候语
    system_prompt   TEXT NOT NULL,                   -- 人设 Prompt
    is_active       INTEGER DEFAULT 1,               -- 1=在好友列表中启用
    created_at      INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_chat_memory (          -- AI 角色私聊对话记忆与上下文
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             INTEGER NOT NULL,                -- 玩家 UID
    char_id         INTEGER NOT NULL,                -- 角色 UID
    role            TEXT NOT NULL,                   -- 'user' / 'assistant'
    content         TEXT NOT NULL,                   -- 对话内容
    timestamp       INTEGER DEFAULT 0,               -- 时间戳
    is_read         INTEGER DEFAULT 1                -- 是否已读
);
CREATE TABLE IF NOT EXISTS boss_challenge_affixes (  -- 梦境再构自选词缀配置
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,
    affix_index_list TEXT DEFAULT '[]',
    time_index_list TEXT DEFAULT '[]',
    diffculty_index INTEGER DEFAULT 1,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);
CREATE TABLE IF NOT EXISTS boss_challenge_scores (   -- 梦境再构首领挑战得分
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,
    score           INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);
CREATE TABLE IF NOT EXISTS boss_challenge_claimed_rewards ( -- 梦境再构已领奖励
    uid             INTEGER NOT NULL,
    reward_type     INTEGER NOT NULL,                -- 1=星级奖励, 2=积分奖励
    reward_id       INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_type, reward_id)
);
CREATE TABLE IF NOT EXISTS boss_challenge_normal (   -- 普通梦境首领通关进度与阵容
    uid             INTEGER NOT NULL,
    group_id        INTEGER NOT NULL,                -- 首领组 ID (如 5001, 5024)
    finish_stage    INTEGER DEFAULT 0,               -- 已通关关卡 ID
    used_heroes     TEXT DEFAULT '[]',               -- 锁定出战角色 [id, id, id]
    last_heroes_cfg TEXT DEFAULT '[]',               -- 预设出战阵容
    star_info       TEXT DEFAULT '[]',               -- 各关卡三星 [{"stage_id": 3010101, "star_list": [1,1,1]}]
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, group_id)
);
CREATE TABLE IF NOT EXISTS boss_challenge_advance (  -- 进阶梦境首领得分与阵容
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,                -- 进阶首领 Pool ID (如 20001)
    score           INTEGER DEFAULT 0,               -- 最高得分
    used_heroes     TEXT DEFAULT '[]',               -- 锁定出战角色 [id, id, id]
    last_heroes_cfg TEXT DEFAULT '[]',               -- 预设出战阵容
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);
CREATE TABLE IF NOT EXISTS core_verification_claimed_tasks ( -- 迭代校验已领任务奖励
    uid             INTEGER NOT NULL,
    task_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);
CREATE TABLE IF NOT EXISTS core_verification_affixes ( -- 迭代校验自选词缀
    uid             INTEGER NOT NULL,
    mode_id         INTEGER NOT NULL,
    affix_list      TEXT DEFAULT '[]',
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, mode_id)
);
CREATE TABLE IF NOT EXISTS core_verification_stage_record ( -- 迭代校验常规关卡记录
    uid             INTEGER NOT NULL,
    cycle           INTEGER NOT NULL,
    info_id         INTEGER NOT NULL,
    boss_type       INTEGER NOT NULL,
    difficult       INTEGER NOT NULL,
    sign            INTEGER DEFAULT 0,
    min_time        INTEGER DEFAULT 0,
    score           INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, cycle, info_id)
);
CREATE TABLE IF NOT EXISTS core_verification_hero_lock ( -- 迭代校验出战锁定
    uid             INTEGER NOT NULL,
    cycle           INTEGER NOT NULL,
    boss_type       INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, cycle, boss_type, hero_id)
);
CREATE TABLE IF NOT EXISTS core_verification_super_score ( -- 极值挑战单关积分
    uid             INTEGER NOT NULL,
    stage_id        INTEGER NOT NULL,
    score           INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);
CREATE TABLE IF NOT EXISTS core_verification_cl_progress ( -- 挑战模式关卡进度与词条
    uid             INTEGER NOT NULL,
    activity_id     INTEGER NOT NULL,
    mode            INTEGER NOT NULL,
    stage_id        INTEGER NOT NULL,
    is_cleared      INTEGER DEFAULT 0,
    score           INTEGER DEFAULT 0,
    min_time        INTEGER DEFAULT 0,
    heroes_json     TEXT DEFAULT '[]',
    select_buffs    TEXT DEFAULT '[]',
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, stage_id)
);
CREATE TABLE IF NOT EXISTS core_verification_cl_task_claimed ( -- 挑战模式已领任务
    uid             INTEGER NOT NULL,
    activity_id     INTEGER NOT NULL,
    task_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, task_id)
);
CREATE TABLE IF NOT EXISTS core_verification_cl_badge_unlocked ( -- 挑战模式图鉴徽章
    uid             INTEGER NOT NULL,
    badge_id        INTEGER NOT NULL,
    unlock_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, badge_id)
);
CREATE TABLE IF NOT EXISTS core_verification_affix_catalog ( -- 迭代校验词条元数据表（支持自定义）
    id              INTEGER PRIMARY KEY,             -- 词条 ID（如 721510, 723010, 1011 等）
    name            TEXT DEFAULT '',                 -- 词条名称
    desc            TEXT DEFAULT '',                 -- 效果描述
    category        TEXT DEFAULT 'regular',          -- 分类: regular / mode1_buff / mode1_debuff / mode2_buff / mode2_debuff / mode3 / mode4
    point           INTEGER DEFAULT 0,               -- 积分/挑战加分
    cost            INTEGER DEFAULT 0,               -- 算力点数消耗（Mode 2）
    affix_type      INTEGER DEFAULT 0,               -- 关联词缀类型
    level           INTEGER DEFAULT 1,               -- 词缀等级
    is_custom       INTEGER DEFAULT 0,               -- 1=自定义, 0=原生
    extra_json      TEXT DEFAULT '{}',               -- 额外扩展数据
    update_ts       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_timer_watermark (   -- 全局惰性时间戳与会话水位线表
    uid                  INTEGER PRIMARY KEY,
    last_pulse_ts        INTEGER DEFAULT 0,          -- 上次物理脉冲时间戳
    last_daily_5am_ts    INTEGER DEFAULT 0,          -- 上次结算的每日 05:00 周期起点
    last_weekly_mon_ts   INTEGER DEFAULT 0,          -- 上次结算的周一 05:00 周期起点
    last_weekly_thu_ts   INTEGER DEFAULT 0,          -- 上次结算的周四 05:00 周期起点
    last_monthly_ts      INTEGER DEFAULT 0,          -- 上次结算的月度 05:00 周期起点
    last_heartbeat_ts    INTEGER DEFAULT 0,          -- 上次客户端活跃发包/心跳时间戳
    today_online_seconds INTEGER DEFAULT 0,          -- 今日累计活跃在线秒数
    today_online_date    TEXT DEFAULT '',            -- 今日在线时长对应日期 (YYYY-MM-DD)
    is_online            INTEGER DEFAULT 0,          -- 当前客户端连接在线标记 (1/0)
    last_logout_ts       INTEGER DEFAULT 0,          -- 上次离线时间戳
    update_ts            INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS combo_skill_state (      -- 连携技能（协作技能网 p73）等级与进度状态
    uid                  INTEGER NOT NULL,
    skill_id             INTEGER NOT NULL,          -- ComboSkillCfg.id (1~85)，cs_73018 上报的 cooperate_unique_skill_id
    level                INTEGER NOT NULL DEFAULT 1, -- 当前等级 1~3（maxLevel）
    progress_json        TEXT NOT NULL DEFAULT '{}', -- {condition_id: times} 升级进度计数器
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, skill_id)
);
CREATE TABLE IF NOT EXISTS trust_displace_counter ( -- 好感度礼物置换每日限次计数（sc_73005 limit_list 数据源）
    uid                  INTEGER NOT NULL,
    item_id              INTEGER NOT NULL,          -- 目标礼物 id（30012~30015）
    day_key              TEXT NOT NULL,             -- 每日 5 点跨天日键（如 20260906_5）
    use                  INTEGER NOT NULL DEFAULT 0,-- 当日已置换数量
    PRIMARY KEY (uid, item_id, day_key)
);
CREATE TABLE IF NOT EXISTS combo_skill_counter (    -- 连携技能出场计量表（cooperation_skill_server 管理）
    uid                  INTEGER NOT NULL,
    combo_id             INTEGER NOT NULL,          -- ComboSkillCfg.id (1~85)
    total_times          INTEGER NOT NULL DEFAULT 0,-- 总触发次数（战斗/扫荡胜利）
    normal_times         INTEGER NOT NULL DEFAULT 0,-- 普通关卡触发次数
    hard_times           INTEGER NOT NULL DEFAULT 0,-- 特定高难关卡触发次数（梦境魇渊Boss/黑区高难）
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, combo_id)
);
CREATE TABLE IF NOT EXISTS stage_team (             -- 关卡现役编队表（team_server 唯一管理，官方关卡键原子级分立）
    uid                  INTEGER NOT NULL,
    stage_type           INTEGER NOT NULL,          -- 玩法类型（cs_54030/63006 上报的 common_info.type）
    activity_id          INTEGER NOT NULL,          -- 关卡键 = GetHeroTeamActivityID(type, dest)：
                                                    --   普通关卡=dest 本身；梦境(10)/进阶(100)/矩阵(14)/
                                                    --   多维(52)/刻印突破(40)/公会(32,33)等按玩法共享
    team_index           INTEGER NOT NULL DEFAULT 0,-- 预设槽位（客户端 teams[].id）
    hero_json            TEXT NOT NULL DEFAULT '[]',-- 出战修正者 id 有序数组（上报次序不可乱，[0]=队长位；
                                                    --   试用英雄 hero_type=2 不持久化）
    cooperate_skill      INTEGER NOT NULL DEFAULT 0,-- 本场使用连携 ID
    mimir_id             INTEGER NOT NULL DEFAULT 0,-- 助战弥弥尔（管理器钥从）id
    mimir_chips          TEXT NOT NULL DEFAULT '',   -- 弥弥尔携带芯片（逗号分隔）
    hero_chips           TEXT NOT NULL DEFAULT '',   -- 助战英雄芯片（逗号分隔）
    clear_times          INTEGER NOT NULL DEFAULT 0, -- 该关卡现役队伍累计通关次数
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_type, activity_id, team_index)
);
CREATE TABLE IF NOT EXISTS user_periodic_gift (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    uid                INTEGER NOT NULL,
    goods_id           INTEGER NOT NULL DEFAULT 0,
    desc_id            INTEGER NOT NULL DEFAULT 0,
    template_id        INTEGER NOT NULL DEFAULT 0,
    total_days         INTEGER NOT NULL DEFAULT 0,
    remain_days        INTEGER NOT NULL DEFAULT 0,
    daily_rewards_json TEXT NOT NULL DEFAULT '[]',
    last_dispatch_date TEXT NOT NULL DEFAULT '',
    status             INTEGER NOT NULL DEFAULT 1,
    create_ts          INTEGER DEFAULT 0,
    update_ts          INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_periodic_gift_uid_status ON user_periodic_gift(uid, status);
CREATE TABLE IF NOT EXISTS server_global_config (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS user_recharge (
    uid                      INTEGER PRIMARY KEY,
    total_recharge_num       INTEGER DEFAULT 0,    -- 累充总额（分，例如 600 为 6 元）
    time_limit_recharge_num  INTEGER DEFAULT 0,    -- 限时累充总额（分）
    total_recharge_version   INTEGER DEFAULT 1,    -- 累充版本
    time_limit_version       INTEGER DEFAULT 1,    -- 限时版本
    first_recharge_ids       TEXT DEFAULT '[]',    -- 已充首充的 goods_id 列表 (JSON)
    claimed_total_bonus      TEXT DEFAULT '[]',    -- 已领取的常驻累充档位列表 (JSON)
    claimed_limit_bonus      TEXT DEFAULT '[]',    -- 已领取的限时累充档位列表 (JSON)
    update_ts                INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admin_cat_explore_data (
    uid                      INTEGER PRIMARY KEY,
    weekly_time              INTEGER DEFAULT 0,    -- 本周累计探索天数 (0~7)
    daily_time               INTEGER DEFAULT 0,    -- 今日探索时间戳 (秒)
    weekly_reward_state      INTEGER DEFAULT 0,    -- 本周周常大奖领取状态 (0: 未领, 1: 已领)
    weekly_scene_open        INTEGER DEFAULT 0,    -- 每周首次进入场景标记 (0: 未进, 1: 已进)
    total_explore_c          INTEGER DEFAULT 0,    -- 历史累计探索货币产出 (总代币数)
    last_weekly_reset_ts     INTEGER DEFAULT 0,    -- 上次周常刷新时间戳
    update_ts                INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admin_cat_list (
    uid                      INTEGER,
    mimir_id                 INTEGER,
    skill_level              INTEGER DEFAULT 1,    -- 技能等级 (1~10)
    unlock_ts                INTEGER DEFAULT 0,    -- 解锁时间戳
    PRIMARY KEY (uid, mimir_id)
);
CREATE TABLE IF NOT EXISTS admin_cat_explore_queue (
    uid                      INTEGER,
    area_id                  INTEGER,
    mimir_id                 INTEGER,
    start_time               INTEGER,
    stop_time                INTEGER,
    target_explore_hour      INTEGER,
    events_json              TEXT DEFAULT '[]',    -- 随机奇遇时间轴事件 (JSON)
    PRIMARY KEY (uid, area_id)
);
"""

# 列迁移（幂等：已存在则跳过）——新表建表走 SCHEMA，加列走这里
_COLUMN_MIGRATIONS = [
    ("currency", "last_fatigue_recover_time", "INTEGER DEFAULT 0"),  # 活力(货币id=4)恢复时间点
    ("currency", "update_ts", "INTEGER DEFAULT 0"),                   # 更新时间戳（最新值仲裁）
    ("material", "update_ts", "INTEGER DEFAULT 0"),                   # 更新时间戳（最新值仲裁）
    ("equip", "is_init", "INTEGER DEFAULT 0"),                        # 装备初始化标记
    ("hero", "is_favorite", "INTEGER DEFAULT 0"),                     # 收藏英雄标记
    ("hero_archive", "name", "TEXT DEFAULT ''"),                      # 英雄名（调试用）
    ("hero_piece", "name", "TEXT DEFAULT ''"),                        # 英雄名（调试用）
    ("material", "name", "TEXT DEFAULT ''"),                          # 材料名（调试用）
    ("currency", "source_type", "TEXT DEFAULT ''"),                   # 货币来源(free/iOS/nonIOS/activity)
    ("shop_goods", "shop_id", "INTEGER DEFAULT 0"),                   # 商品归属商店（goods_shop_map 提取）
    ("game_user", "cur_portrait", "INTEGER DEFAULT 0"),               # 当前头像
    ("game_user", "cur_icon_frame", "INTEGER DEFAULT 0"),             # 当前头像框
    ("game_user", "cur_home_bg", "INTEGER DEFAULT 0"),                # 当前主页背景
    ("game_user", "cur_card_bg", "INTEGER DEFAULT 0"),                # 当前名片背景
    ("game_user", "cur_bubble", "INTEGER DEFAULT 0"),                 # 当前聊天气泡
    ("game_user", "cur_momotalk_frame", "INTEGER DEFAULT 1"),          # [Fix by Gemini 3.7-flash] 当前 MomoTalk 头像框
    ("momotalk_session", "save_list", "TEXT DEFAULT '[]'"),            # [Fix by Gemini 3.7-flash] 对话断点列表 JSON
    ("core_verification_claimed_tasks", "cycle", "INTEGER DEFAULT 0"), # 周期领奖标记（0=首通永久, >0=周常周期）
    ("momotalk_session", "current_content_id", "INTEGER DEFAULT 0"),   # [Fix by Gemini 3.7-flash] 当前对话断点 ID

    ("game_user", "cur_sticker_bg", "INTEGER DEFAULT 0"),             # 当前贴纸背景
    ("game_user", "show_hero", "TEXT DEFAULT '[]'"),                  # 展示英雄
    ("game_user", "board_hero", "INTEGER DEFAULT 0"),                 # 看板娘
    ("game_user", "cur_scene", "INTEGER DEFAULT 0"),                  # 当前主页场景
    ("matrix_system", "ready_hero_skins", "TEXT DEFAULT '[]'"),       # 本期 5 出战皮肤
    ("matrix_system", "ready_hero_json", "TEXT DEFAULT '[]'"),        # 本期 5 出战 {standard_id,skin_id}
    ("matrix_user", "current_progress_json", "TEXT DEFAULT '{}'"),    # 进行中矩阵局
    ("rogueteam_progress", "status", "INTEGER DEFAULT 0"),            # 入口状态（22493 f1=1）
    ("rogueteam_progress", "progress_ref_json", "TEXT DEFAULT '[]'"), # 进度引用 50101~50113（24165，语义待定）
    ("skin_draw_pool", "pool_name", "TEXT DEFAULT ''"),               # 卡池名（永夜眷恋 等）
    ("skin_draw_pool", "reward_item_id", "INTEGER DEFAULT 0"),        # 奖励物品 id
    ("skin_draw_pool", "reward_name", "TEXT DEFAULT ''"),             # 奖励物品名（item_catalog）
    ("club", "leader_online_ts", "INTEGER DEFAULT 0"),                # 会长在线时间戳（31001）
    ("club", "leader_vitality", "INTEGER DEFAULT 0"),                 # 会长周活力
    ("club", "leader_total_vitality", "INTEGER DEFAULT 0"),           # 会长总活力
    ("club", "assist_hero_ids", "TEXT DEFAULT ''"),                   # 公会助战英雄（逗号分隔）
    ("club", "last_share_ts", "INTEGER DEFAULT 0"),                   # 分享冷却
    ("club", "welfare_state", "INTEGER DEFAULT 0"),                   # 社区福利状态（31207）
    # 2026-08-15 操作表补列（子代理挖掘）
    ("battle_equip", "suit_id", "INTEGER DEFAULT 0"),                 # 战斗装备·上阵套装（43004）
    ("backhome_hero", "skin_id", "INTEGER DEFAULT 0"),                # 后宅英雄皮肤（58126）
    ("backhome_dorm", "be_visited_num", "INTEGER DEFAULT 0"),         # 被访次数（58054）
    ("backhome_canteen_meta", "mode", "INTEGER DEFAULT 0"),           # 经营模式（58108）
    ("backhome_canteen_meta", "pending_earnings", "INTEGER DEFAULT 0"),           # 待领取营收（lazy 结算增量，58106 领取/58003 下发）
    ("backhome_canteen_meta", "last_receive_earnings_time", "INTEGER DEFAULT 0"), # 上次领取营收时间（58107 领取冷却起点）
    # 2026-08-18 英雄关系网与好感度（73xxx 协议落库）
    ("hero", "trust_level", "INTEGER DEFAULT 0"),
    ("hero", "trust_exp", "INTEGER DEFAULT 0"),
    ("hero", "trust_mood", "INTEGER DEFAULT 1"),
    ("hero", "relation_net", "TEXT DEFAULT '[]'"),
    # 2026-08-26 梦境再构（Boss Challenge）周常与日常惰性刷新列
    ("boss_challenge_progress", "cycle_id", "INTEGER DEFAULT 0"),
    ("boss_challenge_progress", "area_id", "INTEGER DEFAULT 4"),
    ("boss_challenge_progress", "advance_id", "INTEGER DEFAULT 101"),
    ("boss_challenge_progress", "use_times", "INTEGER DEFAULT 0"),
    ("boss_challenge_progress", "last_daily_ts", "INTEGER DEFAULT 0"),
    ("boss_challenge_progress", "last_weekly_ts", "INTEGER DEFAULT 0"),
    # 回归活动（Regression 62xxx）签到与找回列
    ("regression", "received_sign_list", "TEXT DEFAULT '[]'"),
    ("regression", "find_time", "INTEGER DEFAULT 0"),
    # 虚构推演（RogueTeam 88xxx）步数与倍率
    ("rogueteam_session", "room_passed_count", "INTEGER DEFAULT 0"),
    ("rogueteam_session", "battle_passed_count", "INTEGER DEFAULT 0"),
    ("rogueteam_session", "score_rate", "INTEGER DEFAULT 100"),
    ("game_user", "last_daily_task_refresh_ts", "INTEGER DEFAULT 0"),
    ("game_user", "last_weekly_task_refresh_ts", "INTEGER DEFAULT 0"),
    ("game_user", "last_login_task_cycle_ts", "INTEGER DEFAULT 0"),
    ("task_cfg", "additional_parameter", "TEXT DEFAULT '[]'"),
    ("friend_chat", "peer_uid", "INTEGER DEFAULT 0"),
]


class AccountDB:
    """线程安全 SQLite 封装：每线程独立连接（sqlite3 连接不可跨线程）。"""

    def __init__(self, path=DEFAULT_DB):
        self.path = path
        self._local = threading.local()
        self._ai_chat_lock = threading.Lock()
        self.init_schema()

    def _conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30.0)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def close(self):
        """关闭当前线程的 sqlite 连接。"""
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self._local.conn = None

    def transaction(self):
        """事务上下文管理器：with db.transaction(): ... 内所有 execute 不自动 commit，
        正常退出统一 commit；异常回滚（原子性，操作调度 apply 用）。"""
        return _Transaction(self)

    def _in_txn(self):
        return getattr(self._local, "in_txn", False)

    def init_schema(self):
        c = self._conn()
        # 权威建库来源：lib/db_schema.sql（从实库导出的 217 张表，全部 IF NOT EXISTS，幂等）。
        # 内嵌的 SCHEMA 字符串已严重落后（实测新建库会缺 17 张表、hero 少 24 列，
        # `SELECT level FROM hero` 直接 no such column），只作为找不到 SQL 文件时的兜底。
        sql_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_schema.sql")
        if os.path.exists(sql_file):
            with open(sql_file, encoding="utf-8") as fh:
                c.executescript(fh.read())
        c.executescript(SCHEMA)      # 兜底 + 覆盖 SQL 文件里可能还没有的新表
        # 列迁移（幂等；表可能由独立脚本建/不存在，迁移前必须确认表存在）
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table, col, ddl in _COLUMN_MIGRATIONS:
            if table not in tables:
                continue  # 表不存在（独立建表脚本未跑/已删除）→ 跳过，不崩
            has = any(r[1] == col for r in c.execute(f'PRAGMA table_info("{table}")').fetchall())
            if not has:
                c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {ddl}')
        c.commit()
        # 启动时自动材料与货币自愈校验
        self.reconcile_all_items()
        # 启动时自动初始化 AI 角色种子数据
        self.init_ai_characters()
        # 启动时自动初始化因果观测（WarChess）章节数据
        self.init_warchess_chapters()
        # 确保默认测试/管理员账号存在于非内存主库 users 表中
        if getattr(self, "path", None) != ":memory:":
            c.execute("""
                INSERT OR IGNORE INTO users (uid, nick, level, exp, sign, portrait, icon_frame)
                VALUES (10001, '管理员_10001', 80, 0, '心之所向，剑之所指。', 1084, 1)
            """)
            c.commit()

    # ---------- 通用 CRUD（uid 隔离） ----------

    def execute(self, sql, args=()):
        c = self._conn()
        cur = c.execute(sql, args)
        if not self._in_txn():
            c.commit()
        return cur

    def executemany(self, sql, seq_of_args):
        c = self._conn()
        cur = c.executemany(sql, seq_of_args)
        if not self._in_txn():
            c.commit()
        return cur

    def query(self, sql, args=()):
        c = self._conn()
        return [dict(r) for r in c.execute(sql, args).fetchall()]

    def get_system_config(self, key: str, default: str = "") -> str:
        """获取系统级全局配置项值。"""
        try:
            rows = self.query("SELECT value FROM server_global_config WHERE key = ?", (key,))
            if rows and rows[0]["value"] is not None:
                return str(rows[0]["value"])
        except Exception:
            pass
        return default

    def set_system_config(self, key: str, value: str):
        """设置系统级全局配置项值（upsert）。"""
        self.execute(
            "INSERT INTO server_global_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )

    def get_user_recharge(self, uid: int) -> dict:
        """获取玩家累计充值与首充状态数据。"""
        row = self.get("user_recharge", uid)
        if not row:
            return {
                "uid": uid,
                "total_recharge_num": 0,
                "time_limit_recharge_num": 0,
                "total_recharge_version": 2,
                "time_limit_version": 2,
                "first_recharge_ids": [],
                "claimed_total_bonus": [],
                "claimed_limit_bonus": [],
                "update_ts": 0,
            }
        import json as _j
        def _parse(v):
            try:
                return _j.loads(v) if isinstance(v, str) else (v or [])
            except Exception:
                return []
        tot_ver = int(row.get("total_recharge_version") or 2)
        if tot_ver < 2:
            tot_ver = 2
        lim_ver = int(row.get("time_limit_version") or 2)
        if lim_ver < 2:
            lim_ver = 2
        return {
            "uid": uid,
            "total_recharge_num": int(row.get("total_recharge_num") or 0),
            "time_limit_recharge_num": int(row.get("time_limit_recharge_num") or 0),
            "total_recharge_version": tot_ver,
            "time_limit_version": lim_ver,
            "first_recharge_ids": _parse(row.get("first_recharge_ids")),
            "claimed_total_bonus": _parse(row.get("claimed_total_bonus")),
            "claimed_limit_bonus": _parse(row.get("claimed_limit_bonus")),
            "update_ts": int(row.get("update_ts") or 0),
        }

    def save_user_recharge(self, uid: int, data: dict):
        """保存玩家累计充值与首充状态数据。"""
        import json as _j
        import time as _t
        tot_ver = int(data.get("total_recharge_version") or 2)
        if tot_ver < 2:
            tot_ver = 2
        lim_ver = int(data.get("time_limit_version") or 2)
        if lim_ver < 2:
            lim_ver = 2
        self.upsert("user_recharge", uid, {
            "total_recharge_num": int(data.get("total_recharge_num") or 0),
            "time_limit_recharge_num": int(data.get("time_limit_recharge_num") or 0),
            "total_recharge_version": tot_ver,
            "time_limit_version": lim_ver,
            "first_recharge_ids": _j.dumps(data.get("first_recharge_ids") or []),
            "claimed_total_bonus": _j.dumps(data.get("claimed_total_bonus") or []),
            "claimed_limit_bonus": _j.dumps(data.get("claimed_limit_bonus") or []),
            "update_ts": int(_t.time()),
        })

    # ---------- 管理员猫咪探索（弥弥尔探索）数据接口 ----------

    def get_admin_cat_explore_data(self, uid: int) -> dict:
        """获取玩家猫咪探索主数据。若不存在则返回 None。"""
        row = self.get("admin_cat_explore_data", uid)
        if not row:
            return None
        return {
            "uid": uid,
            "weekly_time": int(row.get("weekly_time") or 0),
            "daily_time": int(row.get("daily_time") or 0),
            "weekly_reward_state": int(row.get("weekly_reward_state") or 0),
            "weekly_scene_open": int(row.get("weekly_scene_open") or 0),
            "total_explore_c": int(row.get("total_explore_c") or 0),
            "last_weekly_reset_ts": int(row.get("last_weekly_reset_ts") or 0),
            "update_ts": int(row.get("update_ts") or 0),
        }

    def save_admin_cat_explore_data(self, uid: int, data: dict):
        """保存玩家猫咪探索主数据。"""
        now = int(time.time())
        self.execute(
            """
            INSERT INTO admin_cat_explore_data (uid, weekly_time, daily_time, weekly_reward_state, weekly_scene_open, total_explore_c, last_weekly_reset_ts, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                weekly_time=excluded.weekly_time,
                daily_time=excluded.daily_time,
                weekly_reward_state=excluded.weekly_reward_state,
                weekly_scene_open=excluded.weekly_scene_open,
                total_explore_c=excluded.total_explore_c,
                last_weekly_reset_ts=excluded.last_weekly_reset_ts,
                update_ts=excluded.update_ts
            """,
            (
                uid,
                int(data.get("weekly_time", 0)),
                int(data.get("daily_time", 0)),
                int(data.get("weekly_reward_state", 0)),
                int(data.get("weekly_scene_open", 0)),
                int(data.get("total_explore_c", 0)),
                int(data.get("last_weekly_reset_ts", 0)),
                now
            )
        )

    def get_admin_cat_list(self, uid: int) -> list:
        """获取玩家已解锁猫咪列表。"""
        rows = self.query(
            "SELECT mimir_id, skill_level FROM admin_cat_list WHERE uid = ? ORDER BY mimir_id ASC",
            (uid,)
        )
        return [{"mimir_id": int(r["mimir_id"]), "skill_level": int(r["skill_level"])} for r in rows]

    def unlock_admin_cat(self, uid: int, mimir_id: int, skill_level: int = 1):
        """解锁新猫咪。"""
        now = int(time.time())
        self.execute(
            """
            INSERT INTO admin_cat_list (uid, mimir_id, skill_level, unlock_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(uid, mimir_id) DO UPDATE SET
                skill_level=excluded.skill_level
            """,
            (uid, mimir_id, skill_level, now)
        )

    def update_admin_cat_skill(self, uid: int, mimir_id: int, skill_level: int):
        """更新猫咪技能等级。"""
        self.execute(
            "UPDATE admin_cat_list SET skill_level = ? WHERE uid = ? AND mimir_id = ?",
            (skill_level, uid, mimir_id)
        )

    def get_admin_cat_queues(self, uid: int) -> list:
        """获取玩家当前探索队列。"""
        rows = self.query(
            "SELECT area_id, mimir_id, start_time, stop_time, target_explore_hour, events_json FROM admin_cat_explore_queue WHERE uid = ? ORDER BY area_id ASC",
            (uid,)
        )
        res = []
        for r in rows:
            try:
                evs = json.loads(r.get("events_json") or "[]")
            except Exception:
                evs = []
            res.append({
                "area_id": int(r["area_id"]),
                "mimir_id": int(r["mimir_id"]),
                "start_time": int(r["start_time"]),
                "stop_time": int(r["stop_time"]),
                "target_explore_hour": int(r["target_explore_hour"]),
                "explore_event": evs,
            })
        return res

    def save_admin_cat_queue(self, uid: int, area_id: int, mimir_id: int, start_time: int, stop_time: int, target_explore_hour: int, events: list):
        """保存单条探索队列。"""
        ev_str = json.dumps(events, ensure_ascii=False)
        self.execute(
            """
            INSERT INTO admin_cat_explore_queue (uid, area_id, mimir_id, start_time, stop_time, target_explore_hour, events_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid, area_id) DO UPDATE SET
                mimir_id=excluded.mimir_id,
                start_time=excluded.start_time,
                stop_time=excluded.stop_time,
                target_explore_hour=excluded.target_explore_hour,
                events_json=excluded.events_json
            """,
            (uid, area_id, mimir_id, start_time, stop_time, target_explore_hour, ev_str)
        )

    def remove_admin_cat_queue(self, uid: int, area_id: int):
        """移除探索队列。"""
        self.execute(
            "DELETE FROM admin_cat_explore_queue WHERE uid = ? AND area_id = ?",
            (uid, area_id)
        )

    def upsert(self, table, uid, row: dict, keys=("uid",)):
        """通用 upsert：row 含 uid；keys 是主键列（默认 ('uid',)）。"""
        row = dict(row)
        row["uid"] = uid
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        updates = ", ".join(f"{k}=excluded.{k}" for k in cols if k not in keys)
        if updates:
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
                   f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}")
        else:
            sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
                   f"ON CONFLICT({', '.join(keys)}) DO NOTHING")
        self.execute(sql, [row[k] for k in cols])

    def get(self, table, uid, where="", args=()):
        rows = self.query(f"SELECT * FROM {table} WHERE uid=? {where}", (uid,) + tuple(args))
        return rows[0] if rows else None

    def list_all(self, table, uid):
        return self.query(f"SELECT * FROM {table} WHERE uid=?", (uid,))

    # ---------- 业务便捷接口 ----------

    def is_currency(self, item_id):
        """[Fix by Gemini 3.7-flash] 权威判断某个 item_id 是否属于 currency 货币类。"""
        iid = int(item_id or 0)
        if iid <= 0:
            return False
        if iid < 100 or iid in (53029, 53111):
            return True
        try:
            rows = self.query("SELECT category FROM item_catalog WHERE id=?", (iid,))
            if rows and rows[0]["category"] == "currency":
                return True
        except Exception:
            pass
        return False

    def set_currency(self, uid, cid, num, ts=None):
        """设置货币数量（写入最新 update_ts，并清除对立表残留）。"""
        cid = int(cid)
        if not self.is_currency(cid):
            return self.set_material(uid, cid, num, ts=ts)
        ts = int(ts if ts is not None else time.time())
        self.upsert("currency", uid, {"id": cid, "num": max(0, int(num)), "update_ts": ts}, keys=("uid", "id"))
        self.execute("DELETE FROM material WHERE uid=? AND id=?", (uid, cid))

    def add_currency(self, uid, cid, delta, ts=None):
        """增加货币数量（以最新 update_ts 值为基准累加，并写入新时间戳）。"""
        cid = int(cid)
        if not self.is_currency(cid):
            return self.add_material(uid, cid, delta, ts=ts)
        ts = int(ts if ts is not None else time.time())
        cur = self.get_item_num(uid, cid)
        new_num = max(0, cur + int(delta))
        self.set_currency(uid, cid, new_num, ts=ts)

    def get_currency(self, uid):
        return self.list_all("currency", uid)

    def set_material(self, uid, mid, num, ts=None):
        """设置材料数量（写入最新 update_ts，并清除对立表残留）。"""
        mid = int(mid)
        if self.is_currency(mid):
            return self.set_currency(uid, mid, num, ts=ts)
        ts = int(ts if ts is not None else time.time())
        self.upsert("material", uid, {"id": mid, "num": max(0, int(num)), "update_ts": ts}, keys=("uid", "id"))
        self.execute("DELETE FROM currency WHERE uid=? AND id=?", (uid, mid))

    def add_material(self, uid, mid, delta, ts=None):
        """增加材料数量（以最新 update_ts 值为基准累加，并写入新时间戳）。"""
        mid = int(mid)
        if self.is_currency(mid):
            return self.add_currency(uid, mid, delta, ts=ts)
        ts = int(ts if ts is not None else time.time())
        cur = self.get_item_num(uid, mid)
        new_num = max(0, cur + int(delta))
        self.set_material(uid, mid, new_num, ts=ts)

    def get_item_num(self, uid, item_id):
        """统一获取任意道具余额（以 update_ts 最新记录为准；时间戳相同时以权威表为准）。"""
        iid = int(item_id or 0)
        row_c = self.query("SELECT num, update_ts FROM currency WHERE uid=? AND id=?", (uid, iid))
        row_m = self.query("SELECT num, update_ts FROM material WHERE uid=? AND id=?", (uid, iid))
        c_num = row_c[0]["num"] if row_c else None
        c_ts = (row_c[0]["update_ts"] or 0) if row_c else 0
        m_num = row_m[0]["num"] if row_m else None
        m_ts = (row_m[0]["update_ts"] or 0) if row_m else 0

        if c_num is not None and m_num is not None:
            if c_ts > m_ts:
                return c_num
            elif m_ts > c_ts:
                return m_num
            else:
                return c_num if self.is_currency(iid) else m_num
        elif c_num is not None:
            return c_num
        elif m_num is not None:
            return m_num
        return 0

    def item_add(self, uid, item_id, delta, ts=None):
        """[Fix by Gemini 3.7-flash] 通用条目增加路由（自动按权威类别写入对应表并盖最新时间戳）。"""
        iid = int(item_id or 0)
        delta = int(delta or 0)
        if iid <= 0 or delta == 0:
            return
        if self.is_currency(iid):
            self.add_currency(uid, iid, delta, ts=ts)
        else:
            self.add_material(uid, iid, delta, ts=ts)

    def add_item(self, uid, item_id, count=1, ts=None):
        """兼容别名方法：通用条目增加路由"""
        return self.item_add(uid, item_id, count, ts=ts)

    def get_currency_num(self, uid, cid):
        """兼容别名方法：获取指定货币余额"""
        return self.get_item_num(uid, cid)

    def item_cost(self, uid, item_id, cost, ts=None):
        """[Fix by Gemini 3.7-flash] 通用条目扣减（以最新时间戳值为准原子扣减并写新时间戳）。"""
        iid = int(item_id or 0)
        cost = int(cost or 0)
        if iid <= 0 or cost <= 0:
            return True
        have = self.get_item_num(uid, iid)
        if have < cost:
            return False
        if self.is_currency(iid):
            self.set_currency(uid, iid, have - cost, ts=ts)
        else:
            self.set_material(uid, iid, have - cost, ts=ts)
        return True

    def reconcile_all_items(self):
        """[Fix by Gemini 3.7-flash] 全局材料与货币对齐矫正器（以 update_ts 最新时间戳为准仲裁权威数据）。"""
        try:
            # 1. 扫描 material 表中属于 currency 的错位记录
            mat_rows = self.query("""
                SELECT m.uid, m.id, m.num, m.update_ts 
                FROM material m 
                JOIN item_catalog ic ON m.id = ic.id 
                WHERE ic.category = 'currency' OR m.id < 100 OR m.id IN (53029, 53111)
            """)
            for r in mat_rows:
                uid, iid, m_num = r["uid"], r["id"], r["num"]
                m_ts = r["update_ts"] or 0
                row_c = self.query("SELECT num, update_ts FROM currency WHERE uid=? AND id=?", (uid, iid))
                c_num = row_c[0]["num"] if row_c else 0
                c_ts = (row_c[0]["update_ts"] or 0) if row_c else 0

                # 仲裁：以时间戳最新者为准
                if m_ts >= c_ts and m_num > 0:
                    final_num = m_num
                    final_ts = m_ts if m_ts > 0 else int(time.time())
                else:
                    final_num = c_num
                    final_ts = c_ts if c_ts > 0 else int(time.time())

                self.execute("""
                    INSERT INTO currency (uid, id, num, update_ts) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid, id) DO UPDATE SET num = excluded.num, update_ts = excluded.update_ts
                """, (uid, iid, final_num, final_ts))
                self.execute("DELETE FROM material WHERE uid=? AND id=?", (uid, iid))

            # 2. 扫描 currency 表中属于 material 的错位记录
            cur_rows = self.query("""
                SELECT c.uid, c.id, c.num, c.update_ts 
                FROM currency c 
                JOIN item_catalog ic ON c.id = ic.id 
                WHERE ic.category = 'material' AND c.id >= 100 AND c.id NOT IN (53029, 53111)
            """)
            for r in cur_rows:
                uid, iid, c_num = r["uid"], r["id"], r["num"]
                c_ts = r["update_ts"] or 0
                row_m = self.query("SELECT num, update_ts FROM material WHERE uid=? AND id=?", (uid, iid))
                m_num = row_m[0]["num"] if row_m else 0
                m_ts = (row_m[0]["update_ts"] or 0) if row_m else 0

                # 仲裁：以时间戳最新者为准
                if c_ts >= m_ts and c_num > 0:
                    final_num = c_num
                    final_ts = c_ts if c_ts > 0 else int(time.time())
                else:
                    final_num = m_num
                    final_ts = m_ts if m_ts > 0 else int(time.time())

                self.execute("""
                    INSERT INTO material (uid, id, num, update_ts) VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid, id) DO UPDATE SET num = excluded.num, update_ts = excluded.update_ts
                """, (uid, iid, final_num, final_ts))
                self.execute("DELETE FROM currency WHERE uid=? AND id=?", (uid, iid))
        except Exception:
            pass

    def add_ingredient(self, uid, item_id, delta):
        row = self.query("SELECT num FROM backhome_ingredient WHERE uid=? AND item_id=?", (uid, item_id))
        cur = row[0]["num"] if row else 0
        new_num = max(0, cur + delta)
        self.execute("INSERT OR REPLACE INTO backhome_ingredient (uid, item_id, num) VALUES (?, ?, ?)",
                     (uid, item_id, new_num))
        return new_num

    def get_material(self, uid):
        return self.list_all("material", uid)

    def set_sign(self, uid, activity_id, **kw):
        row = {"activity_id": activity_id}
        row.update({k: v for k, v in kw.items() if v is not None})
        if "sign_list" in kw and isinstance(kw["sign_list"], (list, tuple)):
            row["sign_list"] = json.dumps(kw["sign_list"], ensure_ascii=False)
        self.upsert("sign", uid, row, keys=("uid", "activity_id"))

    def get_sign(self, uid, activity_id=None):
        if activity_id is None:
            rows = self.list_all("sign", uid)
        else:
            rows = [self.get("sign", uid, "AND activity_id=?", (activity_id,))]
        for r in rows:
            if r and r.get("sign_list"):
                try:
                    r["sign_list"] = json.loads(r["sign_list"])
                except (TypeError, ValueError):
                    pass
        return rows

    def set_validation(self, uid, kind, version="", detail=None):
        self.upsert("validation", uid, {"kind": kind, "version": version,
                                        "detail": json.dumps(detail or {}, ensure_ascii=False)},
                    keys=("uid", "kind"))

    def get_validation(self, uid, kind=None):
        if kind is None:
            return self.list_all("validation", uid)
        r = self.get("validation", uid, "AND kind=?", (kind,))
        if r and r.get("detail"):
            try:
                r["detail"] = json.loads(r["detail"])
            except (TypeError, ValueError):
                pass
        return r

    # ---------- item_catalog（全量条目目录，全局无 uid） ----------

    def import_catalog(self, items):
        """批量导入条目目录（id/name/type/rare/category）。items: list[dict]。"""
        c = self._conn()
        c.executemany(
            "INSERT OR REPLACE INTO item_catalog (id, name, type, rare, category) VALUES (?,?,?,?,?)",
            [(it.get("id"), it.get("name") or "", it.get("type"), it.get("rare"),
              it.get("category", "other")) for it in items])
        c.commit()

    def get_catalog(self, category=None, types=None):
        """查条目目录；category 或 types 过滤，按 id 升序。"""
        sql = "SELECT * FROM item_catalog WHERE 1=1"
        args = []
        if category:
            sql += " AND category=?"
            args.append(category)
        if types:
            ts = ",".join("?" for _ in types)
            sql += f" AND type IN ({ts})"
            args += list(types)
        sql += " ORDER BY id"
        return self.query(sql, args)

    def catalog_count(self):
        return self.query("SELECT COUNT(*) AS n FROM item_catalog")[0]["n"]

    # ---------- 任务接口（cs_28010 单领 / cs_28014 一键领） ----------

    def _is_recurring_task(self, task_id):
        """判断是否为周期性任务（日常/周常/战令等），此类任务跨天/跨周重置，严禁写入永久 claim_ledger。"""
        tid = int(task_id)
        if (6001 <= tid <= 6099) or (5001 <= tid <= 5099) or (10000 <= tid <= 10015) or (71000 <= tid <= 81999):
            return True
        rows = self.query("SELECT task_type FROM task_cfg WHERE task_id=?", (tid,))
        if rows and rows[0].get("task_type") in (5, 6, 7, 8, 901, 902, 903, 1101, 3001, 3002):
            return True
        return False

    def is_task_claimed(self, uid, task_id):
        """任务是否已领取（幂等判断）：返回 True/False。
        周期性任务仅依据 task 表本身的 claimed_ts 与 complete_flag 判断；
        一次性任务兼顾 claim_ledger，杜绝图鉴/一次性成就被重复触发领奖。"""
        tid = int(task_id)
        if not self._is_recurring_task(tid):
            if self.is_claimed(uid, "task", tid) or self.is_claimed(uid, "illustrated_task", tid):
                return True
        rows = self.query("SELECT claimed_ts, complete_flag FROM task WHERE uid=? AND task_id=?",
                          (uid, tid))
        if not rows:
            return False
        return bool(rows[0].get("claimed_ts"))

    def claim_task(self, uid, task_id, now_ts=0):
        """领取任务：置 claimed_ts=now、complete_flag=1。
        一次性成就/图鉴等非周期任务记入 claim_ledger；周期性任务（日常/周常等）绝不计入 claim_ledger。
        返回 True（已更新）。任务不存在时返回 False（不入账）。"""
        import time as _t
        ts = now_ts or int(_t.time())
        tid = int(task_id)
        cur = self.execute(
            "UPDATE task SET claimed_ts=?, complete_flag=1 WHERE uid=? AND task_id=?",
            (ts, uid, tid))
        if not self._is_recurring_task(tid):
            self.mark_claimed(uid, "task", tid, ts)
            if 227000 <= tid <= 227999:
                self.mark_claimed(uid, "illustrated_task", tid, ts)
        return cur.rowcount > 0

    # ---------- 活跃度接口（ActivityPt / 28016 / 28019） ----------

    def get_activity_pt_dict(self, uid):
        """获取玩家活跃度数据字典：{1: {'active_point': X, 'get_id_list': [20, 40, ...]}, 3: ...}"""
        rows = self.query("SELECT activity_pt_id, active_point, get_id_list FROM activity_pt WHERE uid=?", (uid,))
        res = {}
        for r in rows:
            pt_id = int(r["activity_pt_id"])
            try:
                got = json.loads(r.get("get_id_list") or "[]")
            except Exception:
                got = []
            res[pt_id] = {
                "activity_pt_id": pt_id,
                "active_point": int(r.get("active_point") or 0),
                "get_id_list": got if isinstance(got, list) else []
            }
        # 确保 1(每日) 和 3(每周) 存在
        for default_id in (1, 3):
            if default_id not in res:
                self.execute(
                    "INSERT OR IGNORE INTO activity_pt (uid, activity_pt_id, active_point, get_id_list) VALUES (?,?,0,'[]')",
                    (uid, default_id)
                )
                res[default_id] = {
                    "activity_pt_id": default_id,
                    "active_point": 0,
                    "get_id_list": []
                }
        return res

    def add_activity_point(self, uid, activity_pt_id, delta):
        """增加指定活跃度积分（1=每日 / 3=每周）"""
        if delta <= 0:
            return
        self.execute(
            "INSERT OR IGNORE INTO activity_pt (uid, activity_pt_id, active_point, get_id_list) VALUES (?,?,0,'[]')",
            (uid, int(activity_pt_id))
        )
        self.execute(
            "UPDATE activity_pt SET active_point = active_point + ? WHERE uid=? AND activity_pt_id=?",
            (int(delta), uid, int(activity_pt_id))
        )

    def claim_activity_pt_reward(self, uid, activity_pt_id, need_pt):
        """记录领取活跃度宝箱档位（幂等）：返回 (True, got_list) 或 (False, got_list)"""
        rows = self.query(
            "SELECT active_point, get_id_list FROM activity_pt WHERE uid=? AND activity_pt_id=?",
            (uid, int(activity_pt_id))
        )
        if not rows:
            return False, []
        try:
            got = json.loads(rows[0]["get_id_list"] or "[]")
        except Exception:
            got = []
        if need_pt in got:
            return False, got
        got.append(int(need_pt))
        self.execute(
            "UPDATE activity_pt SET get_id_list=? WHERE uid=? AND activity_pt_id=?",
            (json.dumps(got), uid, int(activity_pt_id))
        )
        return True, got

    # ---------- 任务周期重置与自愈（05:00 跨天/跨周） ----------

    def check_and_refresh_periodic_tasks(self, uid):
        """
        [DEPRECATED] 该老时钟逻辑已由全局 LazyTimer (timer_listeners.py) 统一接管。
        保留空方法以兼容旧代码调用，避免产生重复重置与多头累加冲突。
        """
        pass

    # ---------- 战令 / 通行证数据驱动与可靠落库 ----------

    def get_or_create_battlepass(self, uid):
        """获取或初始化战令数据记录，保证 uid 对应的 battlepass 行始终存在。"""
        rows = self.query("SELECT * FROM battlepass WHERE uid=?", (uid,))
        if not rows:
            now_ts = int(time.time())
            start_ts = now_ts - 7 * 86400
            end_ts = now_ts + 60 * 86400
            from lazy_timer import get_weekly_mon_5am_ts
            next_ref = get_weekly_mon_5am_ts(now_ts) + 7 * 86400
            self.execute(
                "INSERT OR IGNORE INTO battlepass "
                "(uid, battlepass_list_id, pay_level, is_start, weekly_gain_exp, start_timestamp, end_timestamp, next_refresh_timestamp, receive_info, update_ts) "
                "VALUES (?, 20032, 0, 1, 0, ?, ?, ?, '[]', ?)",
                (uid, start_ts, end_ts, next_ref, now_ts)
            )
            rows = self.query("SELECT * FROM battlepass WHERE uid=?", (uid,))
        return rows[0] if rows else {}

    def save_battlepass_receive_info(self, uid, receive_info):
        """保存战令已领取记录（receive_info），保证行存在且即时持久化落库。"""
        self.get_or_create_battlepass(uid)
        now_ts = int(time.time())
        rec_json = json.dumps(receive_info) if not isinstance(receive_info, str) else receive_info
        self.execute("UPDATE battlepass SET receive_info=?, update_ts=? WHERE uid=?", (rec_json, now_ts, uid))

    def set_battlepass_pay_level(self, uid, pay_level):
        """设置战令付费档位（201 进阶合约 / 202 深度合约）。"""
        self.get_or_create_battlepass(uid)
        now_ts = int(time.time())
        self.execute("UPDATE battlepass SET pay_level=?, update_ts=? WHERE uid=?", (int(pay_level), now_ts, uid))

    def get_or_create_newbie_activity(self, uid):
        """获取或初始化新手活动记录 (newbie_activity 表)。"""
        rows = self.query("SELECT * FROM newbie_activity WHERE uid=?", (uid,))
        if not rows:
            now_ts = int(time.time())
            completed_time = now_ts + 30 * 86400
            self.execute(
                "INSERT OR IGNORE INTO newbie_activity "
                "(uid, completed_time, now_sign_times, last_sign_ts, fr_first_gear, fr_second_gear, "
                "fr_now_sign, fr_last_sign_ts, fr_new6, fr_new18, mc_flag, mc_role_flag, mc_sign_times, "
                "mc_sign_reward, mc_new_role, mc_new_sign, bp_reward, bp_new, trigger_time, max_phase, version_id, update_ts) "
                "VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, 7, 3, ?)",
                (uid, completed_time, now_ts, now_ts)
            )
            rows = self.query("SELECT * FROM newbie_activity WHERE uid=?", (uid,))
        return dict(rows[0]) if rows else {}

    def get_newbie_activity(self, uid):
        return self.get_or_create_newbie_activity(uid)

    def save_newbie_activity(self, uid, data):
        """保存新手活动记录。"""
        self.get_or_create_newbie_activity(uid)
        now_ts = int(time.time())
        fields = [
            "completed_time", "now_sign_times", "last_sign_ts", "fr_first_gear", "fr_second_gear",
            "fr_now_sign", "fr_last_sign_ts", "fr_new6", "fr_new18", "mc_flag", "mc_role_flag",
            "mc_sign_times", "mc_sign_reward", "mc_new_role", "mc_new_sign", "bp_reward", "bp_new",
            "trigger_time", "max_phase", "version_id"
        ]
        sets = []
        vals = []
        for f in fields:
            if f in data:
                sets.append(f"{f}=?")
                vals.append(data[f])
        if not sets:
            return
        sets.append("update_ts=?")
        vals.append(now_ts)
        vals.append(uid)
        sql = f"UPDATE newbie_activity SET {', '.join(sets)} WHERE uid=?"
        self.execute(sql, tuple(vals))

    # ---------- 红点接口（sc_56001 / cs_56002） ----------

    def set_red_dot(self, uid, red_dot_id, state=0):
        """写入红点状态：state=0 已处理 / 1 待展示。cs_56002 上报已读 → state=0。"""
        self.upsert("redpoint", uid, {"red_dot_id": red_dot_id, "state": state},
                    keys=("uid", "red_dot_id"))

    def get_red_dot(self, uid, state=None):
        """查询红点列表；state 指定则按状态过滤。返回 [{'red_dot_id':..,'state':..}]"""
        where = ""
        args = ()
        if state is not None:
            where = "AND state=?"
            args = (state,)
        return self.query(f"SELECT red_dot_id, state, finish_ts FROM redpoint "
                          f"WHERE uid=? {where} ORDER BY red_dot_id", (uid,) + args)

    def get_red_dot_finished(self, uid):
        """sc_56001 的 client_finished_red_dot 列表（已处理 key，含 301/400 等小 id）。"""
        return [r["red_dot_id"] for r in self.get_red_dot(uid, state=0)]

    def get_red_dot_pending(self, uid):
        """sc_56001 的 red_dot 白名单列表（待展示 key）。"""
        return [r["red_dot_id"] for r in self.get_red_dot(uid, state=1)]

    def seed_red_dot_finished(self, uid, key_list, ts=0):
        """种子灌入：把一批 key 标记为已处理（state=0）。"""
        for k in key_list:
            self.set_red_dot(uid, int(k), 0)
            if ts:
                self.execute("UPDATE redpoint SET finish_ts=? WHERE uid=? AND red_dot_id=?",
                             (ts, uid, int(k)))

    # ---------- 通用领取台账（幂等发奖：claim_ledger） ----------

    def is_claimed(self, uid, kind, key_id):
        """该条目是否已领取过。用于一次性奖励防重复入账。"""
        rows = self.query(
            "SELECT 1 FROM claim_ledger WHERE uid=? AND kind=? AND key_id=?",
            (uid, str(kind), int(key_id)))
        return bool(rows)

    def mark_claimed(self, uid, kind, key_id, now_ts=0):
        """标记为已领取。返回 True=本次新标记，False=之前已领过（幂等，可据此跳过发奖）。"""
        import time as _t
        cur = self.execute(
            "INSERT OR IGNORE INTO claim_ledger (uid, kind, key_id, claim_ts) VALUES (?,?,?,?)",
            (uid, str(kind), int(key_id), now_ts or int(_t.time())))
        return cur.rowcount > 0

    # ---------------- MomoTalk 随身通讯（# [Fix by Gemini 3.7-flash]） ----------------

    def get_momotalk_data(self, uid):
        """获取玩家 MomoTalk 全量数据（包括 74 位角色全部会话列表与持久化进度）。
        全解锁可见，未进行会话初始无进度(save_list=[])，随着玩家交互实时入库。"""
        import json
        import time as _t
        now_ts = int(_t.time())

        # 1. 玩家头像框
        user_row = self.get("game_user", uid)
        cur_icon = int(user_row.get("cur_momotalk_frame", 1) if user_row else 1)
        icon_list = [1, 2, 3, 4, 5]

        # 2. 查询已持久化的会话进度
        cur = self.execute(
            "SELECT session_id, hero_id, send_time, is_view, current_content_id, save_list "
            "FROM momotalk_session WHERE uid=?", (uid,)
        )
        persisted = {}
        for r in cur.fetchall():
            s_id, h_id, s_time, is_v, c_id, s_list_raw = r
            try:
                s_list = json.loads(s_list_raw) if s_list_raw else []
            except Exception:
                s_list = []
            persisted[s_id] = {
                "id": int(s_id),
                "send_time": int(s_time) if s_time else now_ts,
                "is_view": int(is_v),
                "current_content_id": int(c_id),
                "save_list": [{"content_id": int(x.get("content_id", 0)), "state": int(x.get("state", 0))} for x in s_list] if isinstance(s_list, list) else []
            }

        # 3. 从 momotalk_message 读取全量 266 条会话（静态全量字典）
        cur = self.execute(
            "SELECT message_id, mtype, sender, content_id FROM momotalk_message ORDER BY message_id ASC"
        )
        msg_rows = cur.fetchall()

        # 按 type -> sender (hero_id) 分组
        type_groups = {}  # {mtype: {sender: [session_rec, ...]}}
        for m_id, mtype, sender, first_content_id in msg_rows:
            mtype = int(mtype) or 1
            sender = int(sender) or 0
            m_id = int(m_id)
            if mtype not in type_groups:
                type_groups[mtype] = {}
            if sender not in type_groups[mtype]:
                type_groups[mtype][sender] = []

            if m_id in persisted:
                sess_rec = persisted[m_id]
            else:
                sess_rec = {
                    "id": m_id,
                    "send_time": now_ts,
                    "save_list": [],
                    "is_view": 0,
                    "current_content_id": 0,
                }
            type_groups[mtype][sender].append(sess_rec)

        type_hero_list = []
        for mtype, heroes in sorted(type_groups.items()):
            hero_sessions = []
            for sender_id, sessions in sorted(heroes.items()):
                hero_sessions.append({
                    "sender_id": sender_id,
                    "session_list": sessions
                })
            type_hero_list.append({
                "type": mtype,
                "hero_session": hero_sessions
            })

        return {
            "icon": cur_icon,
            "icon_list": icon_list,
            "type_hero": type_hero_list
        }

    def save_momotalk_break(self, uid, session_id, content_id):
        """保存 MomoTalk 对话新断点（cs_91014 -> sc_91015）。"""
        import json
        import time as _t
        now_ts = int(_t.time())
        session_id = int(session_id)
        content_id = int(content_id)
        hero_id = session_id // 100

        cur = self.execute(
            "SELECT is_view, save_list FROM momotalk_session WHERE uid=? AND session_id=?",
            (uid, session_id)
        )
        row = cur.fetchone()
        if row:
            is_v, s_list_raw = row
            try:
                s_list = json.loads(s_list_raw) if s_list_raw else []
            except Exception:
                s_list = []
            found = False
            for item in s_list:
                if item.get("content_id") == content_id:
                    found = True
                    break
            if not found:
                s_list.append({"content_id": content_id, "state": 0})
            self.execute(
                "UPDATE momotalk_session SET current_content_id=?, save_list=?, update_ts=? "
                "WHERE uid=? AND session_id=?",
                (content_id, json.dumps(s_list), now_ts, uid, session_id)
            )
        else:
            s_list = [{"content_id": content_id, "state": 0}]
            self.execute(
                "INSERT OR REPLACE INTO momotalk_session (uid, hero_id, session_id, send_time, is_view, current_content_id, save_list, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, hero_id, session_id, now_ts, 0, content_id, json.dumps(s_list), now_ts)
            )

    def finish_momotalk_break(self, uid, session_id, content_id, state):
        """完成 MomoTalk 对话断点/选项分支（cs_91002 -> sc_91003）。"""
        import json
        import time as _t
        now_ts = int(_t.time())
        session_id = int(session_id)
        content_id = int(content_id)
        state = int(state)
        hero_id = session_id // 100

        cur = self.execute(
            "SELECT is_view, current_content_id, save_list FROM momotalk_session WHERE uid=? AND session_id=?",
            (uid, session_id)
        )
        row = cur.fetchone()
        if row:
            is_v, cur_c_id, s_list_raw = row
            try:
                s_list = json.loads(s_list_raw) if s_list_raw else []
            except Exception:
                s_list = []
            updated = False
            for item in s_list:
                if item.get("content_id") == content_id:
                    item["state"] = state
                    updated = True
            if not updated:
                s_list.append({"content_id": content_id, "state": state})
            self.execute(
                "UPDATE momotalk_session SET save_list=?, update_ts=? WHERE uid=? AND session_id=?",
                (json.dumps(s_list), now_ts, uid, session_id)
            )
        else:
            s_list = [{"content_id": content_id, "state": state}]
            self.execute(
                "INSERT OR REPLACE INTO momotalk_session (uid, hero_id, session_id, send_time, is_view, current_content_id, save_list, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, hero_id, session_id, now_ts, 0, content_id, json.dumps(s_list), now_ts)
            )

    def set_momotalk_read(self, uid, session_id):
        """设置 MomoTalk 会话已读（cs_91006 -> sc_91007）。"""
        import time as _t
        now_ts = int(_t.time())
        session_id = int(session_id)
        hero_id = session_id // 100

        cur = self.execute(
            "SELECT session_id FROM momotalk_session WHERE uid=? AND session_id=?",
            (uid, session_id)
        )
        if cur.fetchone():
            self.execute(
                "UPDATE momotalk_session SET is_view=1, update_ts=? WHERE uid=? AND session_id=?",
                (now_ts, uid, session_id)
            )
        else:
            self.execute(
                "INSERT OR REPLACE INTO momotalk_session (uid, hero_id, session_id, send_time, is_view, current_content_id, save_list, update_ts) "
                "VALUES (?, ?, ?, ?, 1, 0, '[]', ?)",
                (uid, hero_id, session_id, now_ts, now_ts)
            )

    def set_momotalk_frame(self, uid, icon):
        """更换 MomoTalk 头像框（cs_91016 -> sc_91017）。"""
        icon = int(icon)
        self.upsert("game_user", uid, {"cur_momotalk_frame": icon})

    def set_chat_bubble(self, uid, bubble_id):
        """[Fix by Gemini 3.7-flash] 更换聊天气泡/聊天框（cs_32120 -> sc_32121）。"""
        self.upsert("game_user", uid, {"cur_bubble": int(bubble_id)})

    # ---------- [Unified Service Refactor] 邮件与修正者信件系统委托层 ----------
    # 全量委托至独立领域服务 mail_service.MailService 处理，保持 account_db 接口向下兼容

    def get_special_letters(self, uid):
        """获取修正者专属信件列表（全量 175 封及已读状态）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).get_special_letters(uid)

    def set_letter_read(self, uid, letter_id):
        """标记修正者信件为已读（联动同角色全部信件）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).read_special_letter(uid, letter_id)

    def get_mail_list(self, uid):
        """获取普通收件箱邮件列表（sc_30003）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).get_inbox_list(uid)

    def get_collect_mails(self, uid):
        """获取收藏邮件列表（sc_30021）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).get_collect_list(uid)

    def get_mail_detail(self, uid, mail_id):
        """获取邮件详情（sc_30009 / sc_30023）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).get_mail_detail(uid, mail_id)

    def set_mail_collect(self, uid, mail_id, opt):
        """收藏/取消收藏邮件（sc_30015）。委托 MailService。"""
        from mail_service import MailService
        MailService.get_instance(self).toggle_collect(uid, mail_id, opt)
        return MailService.get_instance(self).get_mail_detail(uid, mail_id)

    def claim_mail_attachment(self, uid, mail_id):
        """领取邮件附件（支持单封 mid>0 或一键领取 mid=0）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).claim_mail_attachments(uid, mail_id)

    def delete_mail(self, uid, mail_id):
        """删除邮件（支持单封 mid>0 或一键删除已读 mid=0）。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).delete_mails(uid, mail_id)

    def send_gm_mail(self, uid, title, content, attachments=None, sender="隐科组总务部"):
        """[GM Console] 向指定 UID 邮箱发送一封 GM 邮件并写入数据库。委托 MailService。"""
        from mail_service import MailService
        return MailService.get_instance(self).send_mail(uid, title, content, attachments=attachments, sender=sender)

    # ------------------------------------------------------------------
    # [Fix by Gemini 3.7-flash] 系统开关管理（system_closed 备份与读取）
    # ------------------------------------------------------------------
    def get_closed_systems(self, uid):
        """[Fix by Gemini 3.7-flash] 获取指定用户当前关闭的系统 ID 集合。"""
        rows = self.query("SELECT system_id FROM system_closed WHERE uid=? AND is_open=0", (uid,))
        return {r["system_id"] for r in rows}

    def close_system(self, uid, system_id, system_name="", remark=""):
        """[Fix by Gemini 3.7-flash] 备份并关闭指定系统。"""
        now_ts = int(time.time())
        self.execute("""
            INSERT OR REPLACE INTO system_closed (uid, system_id, system_name, is_open, update_ts, remark)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (uid, int(system_id), system_name, now_ts, remark))

    def open_system(self, uid, system_id):
        """[Fix by Gemini 3.7-flash] 重新开启指定系统。"""
        now_ts = int(time.time())
        self.execute("""
            UPDATE system_closed SET is_open=1, update_ts=? WHERE uid=? AND system_id=?
        """, (now_ts, uid, int(system_id)))

    # ------------------------------------------------------------------
    # [Fix by Gemini 3.7-flash] 家园/后宅/游园街（BackHome / Dorm / Canteen）
    # ------------------------------------------------------------------
    def get_archive_id(self, hero_id_or_archive_id):
        """将角色自机卡号或档案 ID 映射为标准的 63 档案 ID。"""
        if not hero_id_or_archive_id:
            return 0
        hid = int(hero_id_or_archive_id)
        return HERO_TO_ARCHIVE.get(hid, hid)

    def get_primary_hero_id(self, archives_id):
        """获取 63 档案对应的官方主自机卡形态 ID。"""
        if not archives_id:
            return 0
        aid = int(archives_id)
        return ARCHIVE_MAP.get(aid, aid)

    def ensure_all_backhome_heroes(self, uid):
        """确保玩家在 backhome_hero 表中拥有完整的全部 63 位官方角色档案底表记录。"""
        existing = self.query("SELECT archives_id FROM backhome_hero WHERE uid=?", (uid,))
        exist_aids = set(r["archives_id"] for r in existing)
        now_ts = int(time.time())
        for aid, primary_hid in ARCHIVE_MAP.items():
            if aid not in exist_aids:
                self.execute("""
                    INSERT OR IGNORE INTO backhome_hero 
                    (uid, archives_id, hero_id, fatigue, feed_times, total_feed_times, is_lock, update_ts, skin_id)
                    VALUES (?, ?, ?, 140, 0, 100, 0, ?, 0)
                """, (uid, aid, primary_hid, now_ts))

    def get_backhome_concise(self, uid):
        """[Fix by Gemini 3.7-flash] 获取后宅/家园登录简要数据（sc_58001），包含所有英雄疲劳度与岗位。"""
        self.ensure_all_backhome_heroes(uid)
        now_ts = int(time.time())
        # 1. 餐厅设施等级（优先从 backhome_canteen_furniture 读取，缺失则初始化落库）
        c_furn_rows = self.query("SELECT entity_id, level FROM backhome_canteen_furniture WHERE uid=?", (uid,))
        if not c_furn_rows:
            default_furns = [
                (1, 941001, 1), (2, 941002, 1), (3, 941003, 1), (4, 941004, 1),
                (10, 941010, 1), (11, 941011, 1), (12, 941012, 1), (13, 941013, 1),
                (14, 941014, 1), (15, 941015, 1), (16, 941016, 1), (17, 941017, 1)
            ]
            for eid, tid, lvl in default_furns:
                self.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_furniture (uid, entity_id, type_id, level, update_ts)
                    VALUES (?, ?, ?, ?, ?)
                """, (uid, eid, tid, lvl, now_ts))
            c_furn_rows = self.query("SELECT entity_id, level FROM backhome_canteen_furniture WHERE uid=?", (uid,))
        canteen_furnitures = [{"uid": r["entity_id"], "level": r["level"]} for r in c_furn_rows]

        # 套装列表（优先从 backhome_suit 读取，缺失则初始化落库）
        s_rows = self.query("SELECT suit_id FROM backhome_suit WHERE uid=?", (uid,))
        if not s_rows:
            default_suits = [
                3011001, 3010002, 3010001, 3103001, 3103002, 3103003, 3103004, 3003001,
                3104001, 3104002, 3104003, 3104004, 3004002, 3004001, 3101001, 3101002,
                3101003, 3101004, 3001002, 3001001, 3003002
            ]
            for sid in default_suits:
                self.execute("INSERT OR IGNORE INTO backhome_suit (uid, suit_id, update_ts) VALUES (?, ?, ?)", (uid, sid, now_ts))
            s_rows = self.query("SELECT suit_id FROM backhome_suit WHERE uid=?", (uid,))
        suit_ids = [r["suit_id"] for r in s_rows]

        # 2. 餐厅数据
        c_meta = self.query("SELECT * FROM backhome_canteen_meta WHERE uid=?", (uid,))
        c_careers = self.query("SELECT * FROM backhome_canteen_career WHERE uid=?", (uid,))
        c_dishes = self.query("SELECT * FROM backhome_canteen_dish WHERE uid=?", (uid,))
        c_entrusts = self.query("SELECT * FROM backhome_canteen_entrust WHERE uid=?", (uid,))
        default_tasks = [1, 20001, 30001, 20003]
        if not c_entrusts:
            for p, tid in enumerate(default_tasks, 1):
                self.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, num_max, refresh_times, start_time, duration, update_ts)
                    VALUES (?, ?, ?, 3, 0, 0, 1200, ?)
                """, (uid, p, tid, now_ts))
            c_entrusts = self.query("SELECT * FROM backhome_canteen_entrust WHERE uid=?", (uid,))

        careers_list = [{"type": r["ctype"], "hero_id": r["hero_id"]} for r in c_careers]
        signature_dish_list = self._calculate_canteen_dishes(uid)
        entrust_list = []
        for r in c_entrusts:
            heros = [int(x) for x in r["hero_list"].split(",") if x] if r.get("hero_list") else []
            entrust_list.append({
                "pos": r.get("pos") or 1,
                "id": r["task_id"] or (r.get("pos") or 1),
                "hero_list": heros,
                "tags": [],
                "num_max": 3,
                "refresh_times": r.get("refresh_times") or 0,
                "start_time": r.get("start_time") or 0,
                "duration": r.get("duration") or 1200
            })

        canteens = [{
            "id": 4,
            "careers": careers_list,
            "signature_dish": signature_dish_list,
            "entrust": entrust_list,
            "accruing_earnings": c_meta[0]["accruing_earnings"] if c_meta else 1847982
        }]

        # 3. 英雄疲劳度与投喂状态（全量 63 官方角色档案库，archives_id 与 hero_id 映射正确）
        hero_rows = self.query("SELECT * FROM backhome_hero WHERE uid=?", (uid,))
        hmap = {r["archives_id"]: r for r in hero_rows}
        backhome_hero = []
        for aid, primary_hid in sorted(ARCHIVE_MAP.items()):
            hr = hmap.get(aid)
            fatigue = hr["fatigue"] if hr else 140
            feed_times = hr["feed_times"] if hr else 0
            total_feed = hr["total_feed_times"] if hr else 100
            is_lock = hr["is_lock"] if hr else 0
            backhome_hero.append({
                "archives_id": aid,
                "hero_id": primary_hid,
                "fatigue": fatigue,
                "feed_times": feed_times,
                "total_feed_times": total_feed,
                "is_lock": is_lock
            })

        # 4. 宿舍简要数据（全部 28 间已解锁宿舍，严格校验档案归属）
        dorm_rows = self.query("SELECT * FROM backhome_dorm WHERE uid=? ORDER BY dorm_id ASC", (uid,))
        dmap = {r["dorm_id"]: r for r in dorm_rows}
        gift_rows = self.query("SELECT dorm_id, furniture_id, num FROM backhome_dorm_gift WHERE uid=?", (uid,))
        gift_map = {}
        for g in gift_rows:
            gift_map.setdefault(g["dorm_id"], []).append({"id": g["furniture_id"], "num": g["num"]})

        dh_rows = self.query("SELECT dorm_id, hero_id FROM backhome_dorm_hero WHERE uid=?", (uid,))
        dh_map = {}
        for dh in dh_rows:
            dh_map.setdefault(dh["dorm_id"], []).append(dh["hero_id"])

        dorm = []
        for r in dorm_rows:
            did = r["dorm_id"]
            assigned_heroes = dh_map.get(did, [])
            mapped_aids = []
            for h in assigned_heroes:
                aid = self.get_archive_id(h)
                if aid not in mapped_aids:
                    mapped_aids.append(aid)
            dorm.append({
                "id": did,
                "pos_id": r["pos_id"],
                "archives_id": mapped_aids,
                "exp": r["exp"],
                "liked_num": r.get("liked_num") or 0,
                "give_furnitures": gift_map.get(did, [])
            })

        # 5. 食材
        ing_rows = self.query("SELECT * FROM backhome_ingredient WHERE uid=?", (uid,))
        ingredients = [{"id": r["item_id"], "num": r["num"]} for r in ing_rows]

        # 6. 家具
        fur_rows = self.query("SELECT * FROM backhome_furniture WHERE uid=?", (uid,))
        furnitures = [{"furniture_id": r["furniture_id"], "num": r["num"], "give_num": r.get("give_num") or 0} for r in fur_rows]

        return {
            "canteens": canteens,
            "canteen_furnitures": canteen_furnitures,
            "backhome_hero": backhome_hero,
            "last_fatigue_update_time": now_ts,
            "dorm": dorm,
            "ingredients": ingredients,
            "daily_game_currency_num": 0,
            "exhibition_id": 5,
            "furnitures": furnitures,
            "backhome_suit_id_list": suit_ids,
            "share_is_open": 1,
            "received_be_visited_gift_num": 0
        }

    def _get_canteen_food_cfg(self):
        global _CANTEEN_FOOD_CFG
        if _CANTEEN_FOOD_CFG is None:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canteen_food_cfg.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        _CANTEEN_FOOD_CFG = json.load(f)
                except Exception:
                    _CANTEEN_FOOD_CFG = {}
            else:
                _CANTEEN_FOOD_CFG = {}
        return _CANTEEN_FOOD_CFG

    _BACKHOME_HERO_SKILL_CFG = None

    def _get_backhome_hero_skill_cfg(self):
        """读取后宅英雄技能配置缓存 (backhome_hero_skill_cfg.json)。"""
        global _BACKHOME_HERO_SKILL_CFG
        if _BACKHOME_HERO_SKILL_CFG is None:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backhome_hero_skill_cfg.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        _BACKHOME_HERO_SKILL_CFG = json.load(f)
                except Exception:
                    _BACKHOME_HERO_SKILL_CFG = {"skills": {}, "hero_skills": {}, "npc_skills": {}}
            else:
                _BACKHOME_HERO_SKILL_CFG = {"skills": {}, "hero_skills": {}, "npc_skills": {}}
        return _BACKHOME_HERO_SKILL_CFG

    def get_canteen_skill_effects(self, duty_heroes):
        """
        [Phase 3] 根据当前在岗英雄列表计算生效的餐厅技能效果。
        duty_heroes: [{'job_type': int, 'hero_id': int, 'archives_id': int, ...}]
        返回:
          cook_speedup: {cook_type: int}
          price_rise: {cook_type: int}
          fatigue_reduce: {archives_id: int}
          popular_rise: int
        """
        cfg = self._get_backhome_hero_skill_cfg()
        skills = cfg.get("skills") or {}
        hero_skills = cfg.get("hero_skills") or {}
        npc_skills = cfg.get("npc_skills") or {}

        cook_speedup = {}
        price_rise = {}
        fatigue_reduce = {}
        popular_rise = 0

        for h in duty_heroes:
            job_type = h.get("job_type") or 1
            hid = h.get("hero_id")
            aid = h.get("archives_id") or hid
            sids = hero_skills.get(str(hid)) or hero_skills.get(str(aid)) or npc_skills.get(str(hid)) or []

            for sid in sids:
                s = skills.get(str(sid))
                if not s:
                    continue
                cond = s.get("condition") or []
                # condition: [1, required_job_type] (1=厨师, 2=服务员, 3=收银员)
                if cond and len(cond) >= 2 and cond[0] == 1:
                    if cond[1] != job_type:
                        continue  # 岗位不匹配，技能不生效

                stype = s.get("type")
                params = s.get("param") or []

                if stype == 1:  # FoodCostQucik (做菜加速)
                    if len(params) >= 2:
                        ctype, pct = params[0], params[1]
                        cook_speedup[ctype] = cook_speedup.get(ctype, 0) + pct
                elif stype == 2:  # FoodCostRise (售价上浮)
                    if len(params) >= 2:
                        ctype, pct = params[0], params[1]
                        price_rise[ctype] = price_rise.get(ctype, 0) + pct
                elif stype == 3:  # FatigueRecuse (体力减耗)
                    if len(params) >= 1:
                        pct = params[0]
                        fatigue_reduce[aid] = fatigue_reduce.get(aid, 0) + pct
                        if hid != aid:
                            fatigue_reduce[hid] = fatigue_reduce.get(hid, 0) + pct
                elif stype == 4:  # PopularRise (人气增加)
                    if len(params) >= 1:
                        popular_rise += params[0]

        return {
            "cook_speedup": cook_speedup,
            "price_rise": price_rise,
            "fatigue_reduce": fatigue_reduce,
            "popular_rise": popular_rise
        }

    DORM_EXP_LEVEL_THRESHOLDS = [
        (48000, 10, 1.50),
        (33000, 9, 1.40),
        (21000, 8, 1.35),
        (12000, 7, 1.30),
        (6000, 6, 1.25),
        (3000, 5, 1.20),
        (1800, 4, 1.15),
        (1000, 3, 1.10),
        (400, 2, 1.05),
        (0, 1, 1.00),
    ]

    def get_dorm_level_and_multiplier(self, exp):
        """根据宿舍好感经验计算宿舍等级与疲劳恢复倍率。"""
        exp = int(exp or 0)
        for th, lvl, mult in self.DORM_EXP_LEVEL_THRESHOLDS:
            if exp >= th:
                return lvl, mult
        return 1, 1.00

    def calculate_dorm_hero_fatigue_recovery(self, uid):
        """[Phase 3] 惰性计算后宅宿舍英雄的疲劳恢复（受宿舍好感等级与被动恢复技能影响，上限120）。"""
        now_ts = int(time.time())

        # 1. 查在岗英雄（在岗不恢复体力）
        careers = self.query("SELECT hero_id FROM backhome_canteen_career WHERE uid=?", (uid,))
        duty_ids = set()
        for r in careers:
            if r.get("hero_id"):
                h = int(r["hero_id"])
                duty_ids.add(h)
                duty_ids.add(self.get_archive_id(h))

        # 2. 查私人宿舍与入住英雄
        dorms = self.query("SELECT dorm_id, exp FROM backhome_dorm WHERE uid=?", (uid,))
        dorm_exp_map = {r["dorm_id"]: (r.get("exp") or 0) for r in dorms}

        dorm_heroes = self.query("SELECT dorm_id, hero_id FROM backhome_dorm_hero WHERE uid=?", (uid,))
        dorm_hero_map = {}
        for dh in dorm_heroes:
            dorm_hero_map.setdefault(dh["dorm_id"], []).append(int(dh["hero_id"]))

        # 3. 查技能配置
        cfg = self._get_backhome_hero_skill_cfg()
        skills = cfg.get("skills") or {}
        hero_skills = cfg.get("hero_skills") or {}
        npc_skills = cfg.get("npc_skills") or {}

        # 4. 遍历各个宿舍
        handled_heroes = set()
        BASE_RATE_PER_SEC = 6.0 / 3600.0  # 基准每小时 6 点 (即每 600 秒 1 点)

        for did, hids in dorm_hero_map.items():
            exp = dorm_exp_map.get(did, 0)
            lvl, lvl_mult = self.get_dorm_level_and_multiplier(exp)

            # 计算同宿舍全队恢复加成 (Skill type 6: FatigueRecoverAllFast)
            team_bonus = 0
            for hid in hids:
                sids = hero_skills.get(str(hid)) or npc_skills.get(str(hid)) or []
                for sid in sids:
                    s = skills.get(str(sid))
                    if s and s.get("type") == 6:
                        params = s.get("param") or []
                        if params:
                            team_bonus += params[0]

            for hid in hids:
                handled_heroes.add(hid)
                handled_heroes.add(self.get_archive_id(hid))
                if hid in duty_ids or self.get_archive_id(hid) in duty_ids:
                    continue  # 在岗工作不回复

                # 个人恢复加成 (Skill type 5: FatigueRecoverSelfFast)
                self_bonus = 0
                sids = hero_skills.get(str(hid)) or npc_skills.get(str(hid)) or []
                for sid in sids:
                    s = skills.get(str(sid))
                    if s and s.get("type") == 5:
                        params = s.get("param") or []
                        if params:
                            self_bonus += params[0]

                # 最终恢复速度 (点/秒)
                rate = BASE_RATE_PER_SEC * lvl_mult * ((100.0 + team_bonus + self_bonus) / 100.0)

                aid = self.get_archive_id(hid)
                h_rows = self.query("SELECT archives_id, fatigue, update_ts FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
                if h_rows:
                    h = h_rows[0]
                    cur_f = int(h.get("fatigue") or 0)
                    if cur_f < 120:
                        last_ts = int(h.get("update_ts") or 0)
                        elapsed = max(0, now_ts - last_ts) if last_ts > 0 else 0
                        rec = int(elapsed * rate)
                        if rec > 0:
                            new_f = min(120, cur_f + rec)
                            self.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? WHERE uid=? AND archives_id=?", (new_f, now_ts, uid, aid))

        # 5. 未入住私人宿舍的其他英雄（按 Lv 1 基础速度回复）
        all_heroes = self.query("SELECT archives_id, hero_id, fatigue, update_ts FROM backhome_hero WHERE uid=?", (uid,))
        for h in all_heroes:
            hid = int(h["hero_id"])
            aid = int(h["archives_id"])
            if hid in handled_heroes or aid in handled_heroes or hid in duty_ids or aid in duty_ids:
                continue
            cur_f = int(h.get("fatigue") or 0)
            if cur_f < 120:
                last_ts = int(h.get("update_ts") or 0)
                elapsed = max(0, now_ts - last_ts) if last_ts > 0 else 0
                rec = int(elapsed * BASE_RATE_PER_SEC)
                if rec > 0:
                    new_f = min(120, cur_f + rec)
                    self.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? WHERE uid=? AND archives_id=?", (new_f, now_ts, uid, aid))

    def _calculate_canteen_dishes(self, uid):
        """[Phase 3] 惰性计算在售招牌菜的制作与售卖进度，融合在岗英雄做菜加速、售价上浮、体力减耗技能并遵守 14000 收益上限。"""
        c_dishes = self.query("SELECT * FROM backhome_canteen_dish WHERE uid=?", (uid,))
        now_ts = int(time.time())
        MAX_EARNINGS_CAP = 14000
        BASE_FATIGUE_COST_PER_MEAL = 0.05

        # 1. 查询岗位英雄当前疲劳度
        careers = self.query("SELECT * FROM backhome_canteen_career WHERE uid=?", (uid,))
        duty_heroes = []
        for car in careers:
            hid = car.get("hero_id")
            ctype = car.get("ctype") or 1
            if hid:
                aid = self.get_archive_id(hid)
                h_rows = self.query("SELECT archives_id, hero_id, fatigue FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
                if h_rows:
                    hero_info = dict(h_rows[0])
                    hero_info["job_type"] = ctype
                    hero_info["hero_id"] = hid
                    hero_info["archives_id"] = aid
                    duty_heroes.append(hero_info)

        # 2. 查询技能影响
        effects = self.get_canteen_skill_effects(duty_heroes)
        cook_speedup = effects["cook_speedup"]
        price_rise = effects["price_rise"]
        fatigue_reduce = effects["fatigue_reduce"]

        # 3. 计算三位英雄能够支撑的最大营业时间（以最先体力耗尽者为准）
        t_arrival = 56.0  # 客流基准间隔 56 秒
        max_work_seconds = 0
        if duty_heroes:
            hero_work_capacities = []
            for h in duty_heroes:
                aid = h["archives_id"]
                cur_f = float(h.get("fatigue") or 0)
                red_pct = min(90, fatigue_reduce.get(aid, 0))
                # 单次服务消耗体力
                f_cost = BASE_FATIGUE_COST_PER_MEAL * (100.0 - red_pct) / 100.0
                if f_cost > 0:
                    max_meals = cur_f / f_cost
                    hero_work_capacities.append(max_meals * t_arrival)
                else:
                    hero_work_capacities.append(999999)
            max_work_seconds = int(min(hero_work_capacities)) if hero_work_capacities else 0

        food_cfgs = self._get_canteen_food_cfg()

        # 4. 查询当前未领取的收益总额（纳管已有待领取与本轮增量，确保硬封顶 14000）
        meta = self.query("SELECT * FROM backhome_canteen_meta WHERE uid=?", (uid,))
        existing_pending = int(meta[0]["pending_earnings"] or 0) if meta else 0
        cur_unclaimed = 0

        signature_dish_list = []
        total_time_spent = 0

        for r in c_dishes:
            fid = r["food_id"]
            sell_num = r.get("sell_num") or 0
            sold_num = r.get("sold_num") or 0
            sell_earnings = r.get("sell_earnings") or 0
            up_ts = r.get("update_ts") or now_ts

            cfg = food_cfgs.get(str(fid), {"cost_time": 720, "sell": 30, "cook_type": 3})
            base_cost_time = cfg.get("cost_time") or 720
            base_sell_price = cfg.get("sell") or 30
            cook_type = cfg.get("cook_type") or 3

            # 技能加成后的实际耗时与售价
            speedup_pct = cook_speedup.get(cook_type, 0) + cook_speedup.get(0, 0)
            actual_cost_time = max(1, int(math.ceil(base_cost_time * (100.0 - min(90, speedup_pct)) / 100.0)))

            rise_pct = price_rise.get(cook_type, 0) + price_rise.get(0, 0)
            actual_sell_price = max(1, int(math.floor(base_sell_price * (100.0 + rise_pct) / 100.0)))

            if sell_num > sold_num and up_ts > 0 and max_work_seconds > 0:
                elapsed = max(0, min(now_ts - up_ts, max_work_seconds))
                # 计算受 14000 上限限制的剩余可售卖份数（扣除已有待领取与本轮增量）
                remaining_cap_money = max(0, MAX_EARNINGS_CAP - (existing_pending + cur_unclaimed))
                max_dishes_by_cap = remaining_cap_money // actual_sell_price if actual_sell_price > 0 else 99999

                incremental_sold = min(sell_num - sold_num, int(elapsed // actual_cost_time), max_dishes_by_cap)
                if incremental_sold > 0:
                    sold_num += incremental_sold
                    inc_income = incremental_sold * actual_sell_price
                    sell_earnings += inc_income
                    cur_unclaimed += inc_income
                    actual_work_time = incremental_sold * actual_cost_time
                    total_time_spent = max(total_time_spent, actual_work_time)
                    new_up_ts = up_ts + actual_work_time
                    self.execute("""
                        UPDATE backhome_canteen_dish
                        SET sold_num=?, sell_earnings=?, update_ts=?
                        WHERE uid=? AND food_id=?
                    """, (sold_num, sell_earnings, new_up_ts, uid, fid))

            signature_dish_list.append({
                "food_id": fid,
                "sell_num": sell_num,
                "sold_num": sold_num,
                "sell_earnings": sell_earnings
            })

        # 5. 扣减工作英雄体力（考虑英雄各自的减耗技能）
        if total_time_spent > 0 and duty_heroes:
            for h in duty_heroes:
                aid = h["archives_id"]
                cur_f = float(h.get("fatigue") or 0)
                red_pct = min(90, fatigue_reduce.get(aid, 0))
                hero_fatigue_rate = (BASE_FATIGUE_COST_PER_MEAL / t_arrival) * ((100.0 - red_pct) / 100.0)
                fatigue_spent = int(round(total_time_spent * hero_fatigue_rate))
                new_f = max(0, int(cur_f - fatigue_spent))
                self.execute("""
                    UPDATE backhome_hero SET fatigue=?, update_ts=?
                    WHERE uid=? AND archives_id=?
                """, (new_f, now_ts, uid, aid))

        # 6. 增量营收落库为待领取（此前 cur_unclaimed 算完即丢弃 → 58003 business.earnings 恒 0、58106 领 0）
        if cur_unclaimed > 0:
            self.execute(
                "UPDATE backhome_canteen_meta SET pending_earnings=COALESCE(pending_earnings,0)+?, update_ts=? "
                "WHERE uid=?", (cur_unclaimed, now_ts, uid))

        return signature_dish_list

    def settle_canteen_earnings(self, uid):
        """lazy 结算并返回当前待领取营收总额（58003 下发 / 58106 领取共用）。

        结算规则在 _calculate_canteen_dishes：按菜品 sold 推进（受
        min(岗位体力)/成本耗时/14000 上限 三重停止条件约束），增量累进 meta.pending_earnings。
        """
        self._calculate_canteen_dishes(uid)
        rows = self.query("SELECT pending_earnings FROM backhome_canteen_meta WHERE uid=?", (uid,))
        return int(rows[0]["pending_earnings"] or 0) if rows else 0

    def get_backhome_detail(self, uid):
        """[Fix by Gemini 3.7-flash] 获取后宅/家园全量详情数据（sc_58003）。"""
        # 0. 推进宿舍英雄疲劳度恢复
        self.calculate_dorm_hero_fatigue_recovery(uid)

        # 1. 餐厅数据
        c_meta = self.query("SELECT * FROM backhome_canteen_meta WHERE uid=?", (uid,))
        c_careers = self.query("SELECT * FROM backhome_canteen_career WHERE uid=?", (uid,))
        c_entrusts = self.query("SELECT * FROM backhome_canteen_entrust WHERE uid=?", (uid,))
        c_dishes = self.query("SELECT * FROM backhome_canteen_dish WHERE uid=?", (uid,))
        now_ts = int(time.time())
        default_tasks = [1, 20001, 30001, 20003]
        if not c_entrusts:
            for p, tid in enumerate(default_tasks, 1):
                self.execute("""
                    INSERT OR REPLACE INTO backhome_canteen_entrust (uid, pos, task_id, num_max, refresh_times, start_time, duration, update_ts)
                    VALUES (?, ?, ?, 3, 0, 0, 1200, ?)
                """, (uid, p, tid, now_ts))
            c_entrusts = self.query("SELECT * FROM backhome_canteen_entrust WHERE uid=?", (uid,))

        careers_list = [{"type": r["ctype"], "hero_id": r["hero_id"]} for r in c_careers]
        signature_dish_list = self._calculate_canteen_dishes(uid)
        # 待领取营收：_calculate_canteen_dishes 已把增量累进 meta.pending_earnings，这里读出下发
        # （此前硬编码 0 → 客户端"总收益"恒 0，单菜品 sell_earnings 却正常显示）
        _pm = self.query(
            "SELECT pending_earnings, last_receive_earnings_time FROM backhome_canteen_meta WHERE uid=?",
            (uid,))
        pending = int(_pm[0]["pending_earnings"] or 0) if _pm else 0
        last_recv = int(_pm[0]["last_receive_earnings_time"] or 0) if _pm else 0
        entrust_list = []
        for r in c_entrusts:
            heros = [int(x) for x in r["hero_list"].split(",") if x] if r.get("hero_list") else []
            entrust_list.append({
                "pos": r.get("pos") or 1,
                "id": r["task_id"] or (r.get("pos") or 1),
                "hero_list": heros,
                "tags": [],
                "num_max": r.get("num_max") or 3,
                "refresh_times": r.get("refresh_times") or 0,
                "start_time": r.get("start_time") or 0,
                "duration": r.get("duration") or 1200
            })

        canteens = [{
            "id": 4,
            "accruing_earnings": c_meta[0]["accruing_earnings"] if c_meta else 1847982,
            "careers": careers_list,
            "signature_dish": signature_dish_list,
            "entrust": entrust_list,
            "business": {
                "earnings": pending,
                "last_earnings_update_time": now_ts,
                "last_receive_earnings_time": last_recv or now_ts,
            },
            "attractive": {"dynamic": 0, "dynamic_update_time": now_ts},
            "special_event": []
        }]

        # 2. 食材数据
        ing_rows = self.query("SELECT * FROM backhome_ingredient WHERE uid=?", (uid,))
        ingredients = [{"id": r["item_id"], "num": r["num"]} for r in ing_rows]

        # 3. 菜谱列表 (下发全部 24 种菜品，num 严格等于持久化的累计完成道数 sold_num，供厨具升级校验)
        food_cfgs = self._get_canteen_food_cfg()
        dish_rows = self.query("SELECT food_id, sold_num FROM backhome_canteen_dish WHERE uid=?", (uid,))
        dish_sold_map = {r["food_id"]: (r.get("sold_num") or 0) for r in dish_rows}

        all_food_ids = sorted([int(k) for k in food_cfgs.keys()]) if food_cfgs else list(range(101, 125))
        food = []
        for fid in all_food_ids:
            food.append({
                "id": fid,
                "proficiency": 100,
                "num": dish_sold_map.get(fid, 0)
            })

        # 4. 宿舍房间与英雄（含家具摆设布局，自动核准家具持有数配额，杜绝跨房克隆）
        self.reconcile_all_dorm_layouts(uid)
        dorm_rows = self.query("SELECT * FROM backhome_dorm WHERE uid=?", (uid,))
        dorm_heroes = self.query("SELECT * FROM backhome_dorm_hero WHERE uid=?", (uid,))
        layouts = self.query("SELECT dorm_id, layout_json FROM backhome_dorm_layout WHERE uid=?", (uid,))
        layout_map = {}
        for ly in layouts:
            if ly.get("layout_json"):
                try:
                    layout_map[ly["dorm_id"]] = json.loads(ly["layout_json"])
                except Exception:
                    pass

        d_hero_map = {}
        for dh in dorm_heroes:
            d_hero_map.setdefault(dh["dorm_id"], []).append(dh["hero_id"])

        # 查所有宿舍礼物
        gift_rows = self.query("SELECT dorm_id, furniture_id, num FROM backhome_dorm_gift WHERE uid=?", (uid,))
        gift_map = {}
        for g in gift_rows:
            gift_map.setdefault(g["dorm_id"], []).append({"id": g["furniture_id"], "num": g["num"]})

        # 官方 hero_id 到 archives_id 的映射表
        hero_to_archive = {
            1011: 1011, 1111: 1011, 1211: 1011, 1012: 1012, 1013: 1013, 1015: 1015, 1016: 1016, 1017: 1017,
            1019: 1019, 1119: 1019, 1020: 1020, 1021: 1021, 1022: 1022, 1024: 1024, 1026: 1026, 1027: 1027,
            1127: 1027, 1028: 1028, 1032: 1032, 1132: 1032, 1033: 1033, 1133: 1033, 1034: 1034, 1035: 1035,
            1037: 1037, 1137: 1037, 1038: 1038, 1138: 1038, 1039: 1039, 1139: 1039, 1041: 1041, 1042: 1042,
            1043: 1043, 1044: 1044, 1045: 1045, 1046: 1046, 1047: 1047, 1048: 1048, 1148: 1048, 1248: 1048,
            1049: 1049, 1050: 1050, 1150: 1050, 1052: 1052, 1053: 1053, 1054: 1054, 1055: 1055, 1056: 1056,
            1156: 1056, 1058: 1058, 1158: 1058, 1059: 1059, 1060: 1060, 1061: 1061, 1066: 1066, 1166: 1066,
            1067: 1067, 1068: 1068, 1070: 1070, 1170: 1070, 1071: 1071, 1072: 1072, 1073: 1073, 1074: 1074,
            1075: 1075, 1076: 1076, 1077: 1077, 1080: 1080, 1081: 1081, 1083: 1083, 1084: 1084, 1184: 1084,
            1284: 1084, 1085: 1085, 1089: 1089, 1093: 1093, 1094: 1094, 1194: 1094, 1095: 1095, 1096: 1096,
            1097: 1097, 1197: 1097, 1099: 1099, 1199: 1099
        }

        d_hero_map = {}
        for dh in dorm_heroes:
            aid = hero_to_archive.get(dh["hero_id"], dh["hero_id"])
            if aid not in d_hero_map.setdefault(dh["dorm_id"], []):
                d_hero_map[dh["dorm_id"]].append(aid)

        dorms = []
        for r in dorm_rows:
            did = r["dorm_id"]
            d_layout = layout_map.get(did, {
                "temp_id": 0,
                "furniture_pos_list": [
                    {"type": 1, "default_suit_id": 0, "furniture_pos": []},
                    {"type": 2, "default_suit_id": 0, "furniture_pos": []},
                    {"type": 3, "default_suit_id": 0, "furniture_pos": []},
                    {"type": 4, "default_suit_id": 0, "furniture_pos": []},
                    {"type": 5, "default_suit_id": 0, "furniture_pos": []}
                ]
            })
            dorms.append({
                "id": did,
                "pos_id": r["pos_id"],
                "archives_id": d_hero_map.get(did, []),
                "exp": r["exp"],
                "liked_num": r.get("liked_num") or 0,
                "give_furnitures": gift_map.get(did, []),
                "layout": d_layout
            })

        # 5. 家具列表
        fur_rows = self.query("SELECT * FROM backhome_furniture WHERE uid=?", (uid,))
        furnitures = [{"furniture_id": r["furniture_id"], "num": r["num"], "give_num": r.get("give_num") or 0} for r in fur_rows]

        return {
            "result": 0,
            "canteens": canteens,
            "ingredients": ingredients,
            "food": food,
            "dorms": dorms,
            "exhibition_id": 5,
            "template": self.get_dorm_templates(uid),
            "furnitures": furnitures
        }

    def save_dorm_template(self, uid, template_id, name, type_val, architecture_id, pos, layout_info):
        """[Phase 1] 保存宿舍家具预设模板（cs_58040 -> sc_58041）。"""
        now_ts = int(time.time())
        layout_json = json.dumps(layout_info, ensure_ascii=False) if isinstance(layout_info, (dict, list)) else str(layout_info or "[]")
        self.execute("""
            INSERT OR REPLACE INTO backhome_dorm_template (uid, template_id, name, type, architecture_id, pos, layout_json, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (uid, int(template_id), str(name), int(type_val), int(architecture_id), int(pos), layout_json, now_ts))

    def revise_dorm_template_name(self, uid, template_id, name):
        """[Phase 1] 修改宿舍家具预设模板名称（cs_58142 -> sc_58143）。"""
        now_ts = int(time.time())
        self.execute("""
            UPDATE backhome_dorm_template SET name=?, update_ts=? WHERE uid=? AND template_id=?
        """, (str(name), now_ts, uid, int(template_id)))

    def delete_dorm_template(self, uid, template_id):
        """[Phase 1] 删除宿舍家具预设模板（cs_58144 -> sc_58145）。"""
        self.execute("DELETE FROM backhome_dorm_template WHERE uid=? AND template_id=?", (uid, int(template_id)))

    def get_dorm_templates(self, uid):
        """[Phase 1] 获取全部预设模板列表（供 58003 详情下发，格式匹配 template_net_rec）。"""
        rows = self.query("SELECT * FROM backhome_dorm_template WHERE uid=? ORDER BY template_id ASC", (uid,))
        templates = []
        for r in rows:
            furniture_pos_list = []
            if r.get("layout_json"):
                try:
                    ly = json.loads(r["layout_json"])
                    if isinstance(ly, dict):
                        furniture_pos_list = ly.get("furniture_pos_list") or []
                    elif isinstance(ly, list):
                        furniture_pos_list = ly
                except Exception:
                    furniture_pos_list = []
            templates.append({
                "id": r["template_id"],
                "type": r.get("type") or 1,
                "name": r.get("name") or "",
                "furniture_pos_list": furniture_pos_list,
                "pos": r.get("pos") or 0
            })
        return templates

    def set_canteen_career(self, uid, ctype, hero_id):
        """[Fix by Gemini 3.7-flash] 设置餐厅岗位安排（cs_58104 -> sc_58105）。"""
        now_ts = int(time.time())
        self.execute("""
            INSERT OR REPLACE INTO backhome_canteen_career (uid, ctype, hero_id, update_ts)
            VALUES (?, ?, ?, ?)
        """, (uid, int(ctype), int(hero_id), now_ts))

    def set_backhome_hero_lock(self, uid, hero_id, lock_type):
        """[Fix by Gemini 3.7-flash] 锁定/解锁后宅英雄（cs_58218 -> sc_58219）。"""
        now_ts = int(time.time())
        aid = self.get_archive_id(hero_id)
        self.execute("""
            UPDATE backhome_hero SET is_lock=?, update_ts=? WHERE uid=? AND archives_id=?
        """, (int(lock_type), now_ts, uid, aid))

    def validate_and_sanitize_dorm_layout(self, uid, dorm_id, layout_data):
        """[Phase 2] 校验并修剪宿舍布局中的家具，确保跨宿舍家具占用数不超玩家仓库总量（hasPlaceNum <= num）。"""
        if not layout_data:
            return layout_data

        # 1. 查玩家拥有的家具总数 (furniture_id -> total_num)
        fur_rows = self.query("SELECT furniture_id, num, give_num FROM backhome_furniture WHERE uid=?", (uid,))
        owned = {r["furniture_id"]: int(r["num"] or 0) for r in fur_rows}

        # 2. 查除当前房间外，其余所有房间当前占用的家具数量 (furniture_id -> other_placed_count)
        other_layouts = self.query("SELECT dorm_id, layout_json FROM backhome_dorm_layout WHERE uid=? AND dorm_id != ?", (uid, int(dorm_id)))
        other_placed = {}
        for row in other_layouts:
            lj_str = row.get("layout_json")
            if not lj_str:
                continue
            try:
                lj = json.loads(lj_str)
            except Exception:
                continue

            f_pos_list = []
            if isinstance(lj, dict):
                f_pos_list = lj.get("furniture_pos_list") or []
                if not f_pos_list and lj.get("temp_id"):
                    t_rows = self.query("SELECT layout_json FROM backhome_dorm_template WHERE uid=? AND template_id=?", (uid, int(lj["temp_id"])))
                    if t_rows and t_rows[0].get("layout_json"):
                        try:
                            t_lj = json.loads(t_rows[0]["layout_json"])
                            f_pos_list = t_lj if isinstance(t_lj, list) else (t_lj.get("furniture_pos_list") or [])
                        except Exception:
                            pass
            elif isinstance(lj, list):
                f_pos_list = lj

            for entry in f_pos_list:
                for item in entry.get("furniture_pos") or []:
                    fid = int(item.get("furniture_id") or 0)
                    if fid:
                        other_placed[fid] = other_placed.get(fid, 0) + 1

        # 3. 提取目标房间申请摆放的 layout_data
        target_pos_list = []
        if isinstance(layout_data, dict):
            target_pos_list = layout_data.get("furniture_pos_list") or []
            if not target_pos_list and layout_data.get("temp_id"):
                t_rows = self.query("SELECT layout_json FROM backhome_dorm_template WHERE uid=? AND template_id=?", (uid, int(layout_data["temp_id"])))
                if t_rows and t_rows[0].get("layout_json"):
                    try:
                        t_lj = json.loads(t_rows[0]["layout_json"])
                        target_pos_list = t_lj if isinstance(t_lj, list) else (t_lj.get("furniture_pos_list") or [])
                    except Exception:
                        pass
        elif isinstance(layout_data, list):
            target_pos_list = layout_data

        # 4. 针对当前房间进行配额校验与超额修剪
        cur_room_placed = {}
        sanitized_pos_list = []
        for entry in target_pos_list:
            new_entry = dict(entry)
            raw_fur_pos = entry.get("furniture_pos") or []
            sanitized_fur_pos = []
            for item in raw_fur_pos:
                fid = int(item.get("furniture_id") or 0)
                if not fid:
                    sanitized_fur_pos.append(item)
                    continue

                total_owned = owned.get(fid, 0)
                placed_in_others = other_placed.get(fid, 0)
                available = max(0, total_owned - placed_in_others)

                if cur_room_placed.get(fid, 0) < available:
                    sanitized_fur_pos.append(item)
                    cur_room_placed[fid] = cur_room_placed.get(fid, 0) + 1
                else:
                    # 超额修剪：该家具在仓库中没有多余配额，跳过不放置
                    pass

            new_entry["furniture_pos"] = sanitized_fur_pos
            sanitized_pos_list.append(new_entry)

        sanitized_layout = {
            "temp_id": 0,
            "furniture_pos_list": sanitized_pos_list
        }
        return sanitized_layout

    def reconcile_all_dorm_layouts(self, uid):
        """[Phase 2] 全局核查并修剪该用户所有宿舍的已放置家具，保证总占用数 hasPlaceNum <= num。"""
        fur_rows = self.query("SELECT furniture_id, num, give_num FROM backhome_furniture WHERE uid=?", (uid,))
        owned = {r["furniture_id"]: int(r["num"] or 0) for r in fur_rows}

        layouts = self.query("SELECT dorm_id, layout_json FROM backhome_dorm_layout WHERE uid=? ORDER BY dorm_id ASC", (uid,))
        if not layouts:
            return

        now_ts = int(time.time())
        global_placed = {}

        for row in layouts:
            did = row["dorm_id"]
            lj_str = row.get("layout_json")
            if not lj_str:
                continue
            try:
                lj = json.loads(lj_str)
            except Exception:
                continue

            target_pos_list = []
            if isinstance(lj, dict):
                target_pos_list = lj.get("furniture_pos_list") or []
                if not target_pos_list and lj.get("temp_id"):
                    t_rows = self.query("SELECT layout_json FROM backhome_dorm_template WHERE uid=? AND template_id=?", (uid, int(lj["temp_id"])))
                    if t_rows and t_rows[0].get("layout_json"):
                        try:
                            t_lj = json.loads(t_rows[0]["layout_json"])
                            target_pos_list = t_lj if isinstance(t_lj, list) else (t_lj.get("furniture_pos_list") or [])
                        except Exception:
                            pass
            elif isinstance(lj, list):
                target_pos_list = lj

            sanitized_pos_list = []
            changed = False
            for entry in target_pos_list:
                new_entry = dict(entry)
                raw_fur_pos = entry.get("furniture_pos") or []
                sanitized_fur_pos = []
                for item in raw_fur_pos:
                    fid = int(item.get("furniture_id") or 0)
                    if not fid:
                        sanitized_fur_pos.append(item)
                        continue

                    total_owned = owned.get(fid, 0)
                    if global_placed.get(fid, 0) < total_owned:
                        sanitized_fur_pos.append(item)
                        global_placed[fid] = global_placed.get(fid, 0) + 1
                    else:
                        changed = True  # 超额裁剪
                new_entry["furniture_pos"] = sanitized_fur_pos
                sanitized_pos_list.append(new_entry)

            if changed or (isinstance(lj, dict) and lj.get("temp_id")):
                new_layout = {
                    "temp_id": 0,
                    "furniture_pos_list": sanitized_pos_list
                }
                self.execute("""
                    UPDATE backhome_dorm_layout SET layout_json=?, update_ts=? WHERE uid=? AND dorm_id=?
                """, (json.dumps(new_layout, ensure_ascii=False), now_ts, uid, did))

    def save_dorm_layout(self, uid, dorm_id, layout_data):
        """[Fix by Gemini 3.7-flash] 保存宿舍房间3D家具摆设布局（cs_58010 -> sc_58011），带库存互斥校验。"""
        now_ts = int(time.time())
        sanitized = self.validate_and_sanitize_dorm_layout(uid, dorm_id, layout_data)
        layout_json = json.dumps(sanitized, ensure_ascii=False)
        self.execute("""
            INSERT OR REPLACE INTO backhome_dorm_layout (uid, dorm_id, layout_json, update_ts)
            VALUES (?, ?, ?, ?)
        """, (uid, int(dorm_id), layout_json, now_ts))

    def unlock_dorm(self, uid, dorm_id, pos_id):
        """[Fix by Gemini 3.7-flash] 解锁新宿舍房间（cs_58130 -> sc_58131），扣除小窝资金并给初始家具。"""
        now_ts = int(time.time())
        did = int(dorm_id)
        pos = int(pos_id)
        cost = 800 if did >= 8 else 0
        rem_mat = None
        if cost > 0:
            m_rows = self.query("SELECT num FROM material WHERE uid=? AND id=41701", (uid,))
            cur_num = m_rows[0]["num"] if m_rows else 0
            rem_mat = max(0, cur_num - cost)
            self.execute("UPDATE material SET num=? WHERE uid=? AND id=41701", (rem_mat, uid))

        self.execute("""
            INSERT OR REPLACE INTO backhome_dorm (uid, dorm_id, pos_id, exp, liked_num, update_ts, be_visited_num)
            VALUES (?, ?, ?, 0, 0, ?, 0)
        """, (uid, did, pos, now_ts))

        # 赠送初始基础家具（床 951013、桌 951014、椅 951027）
        for fid in [951013, 951014, 951027]:
            self.execute("""
                INSERT INTO backhome_furniture (uid, furniture_id, num, give_num, update_ts)
                VALUES (?, ?, 1, 0, ?)
                ON CONFLICT(uid, furniture_id) DO UPDATE SET num=num+1, update_ts=?
            """, (uid, fid, now_ts, now_ts))

        return rem_mat

    def add_furniture(self, uid, furniture_id, count=1):
        """[Phase 5] 购买/获得家具落库 backhome_furniture。"""
        fid = int(furniture_id)
        cnt = int(count)
        now_ts = int(time.time())
        self.execute("""
            INSERT INTO backhome_furniture (uid, furniture_id, num, give_num, update_ts)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(uid, furniture_id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
        """, (uid, fid, cnt, now_ts))

    def unlock_suit(self, uid, suit_id):
        """[Phase 5] 解锁大厅/宿舍图纸套装 backhome_suit。"""
        sid = int(suit_id)
        now_ts = int(time.time())
        self.execute("""
            INSERT INTO backhome_suit (uid, suit_id, update_ts)
            VALUES (?, ?, ?)
            ON CONFLICT(uid, suit_id) DO UPDATE SET update_ts = excluded.update_ts
        """, (uid, sid, now_ts))

    def add_canteen_ingredient(self, uid, item_id, count=1):
        """[Phase 5] 购买/获得食堂食材落库 backhome_ingredient。"""
        iid = int(item_id)
        cnt = int(count)
        now_ts = int(time.time())
        self.execute("""
            INSERT INTO backhome_ingredient (uid, item_id, num, update_ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(uid, item_id) DO UPDATE SET num = num + excluded.num, update_ts = excluded.update_ts
        """, (uid, iid, cnt, now_ts))

    def calculate_dorm_hero_fatigue_recovery(self, uid, now_ts=None):
        """[Phase 5] 设施场景体力动态自然恢复（对齐官方 DormHeroTemplate.GetRecoverFatigueNum 算法）：
        - 食堂打工/委托中: 0 恢复
        - 私人宿舍: 按宿舍等级 Lv 1~10 (100%~150%) 恢复 6.0~9.0 点/小时 (1.00~1.50 点/600秒)
        - 公共大厅: 按 100% 恢复 6.0 点/小时 (1.00 点/600秒)
        - 舞蹈训练营: 有专属房按专属房算，无专属房按大厅算
        - 空闲未入驻: 按 25% 恢复 1.5 点/小时 (0.25 点/600秒)
        - 体力上限为 140
        """
        now_ts = int(time.time()) if now_ts is None else int(now_ts)
        hero_to_archive = {
            1011: 1011, 1111: 1011, 1211: 1011, 1012: 1012, 1013: 1013, 1015: 1015, 1016: 1016, 1017: 1017,
            1019: 1019, 1119: 1019, 1020: 1020, 1021: 1021, 1022: 1022, 1024: 1024, 1026: 1026, 1027: 1027,
            1127: 1027, 1028: 1028, 1032: 1032, 1132: 1032, 1033: 1033, 1133: 1033, 1034: 1034, 1035: 1035,
            1037: 1037, 1137: 1037, 1038: 1038, 1138: 1038, 1039: 1039, 1139: 1039, 1041: 1041, 1042: 1042,
            1043: 1043, 1044: 1044, 1045: 1045, 1046: 1046, 1047: 1047, 1048: 1048, 1148: 1048, 1248: 1048,
            1049: 1049, 1050: 1050, 1150: 1050, 1052: 1052, 1053: 1053, 1054: 1054, 1055: 1055, 1056: 1056,
            1156: 1056, 1058: 1058, 1158: 1058, 1059: 1059, 1060: 1060, 1061: 1061, 1066: 1066, 1166: 1066,
            1067: 1067, 1068: 1068, 1070: 1070, 1170: 1070, 1071: 1071, 1072: 1072, 1073: 1073, 1074: 1074,
            1075: 1075, 1076: 1076, 1077: 1077, 1080: 1080, 1081: 1081, 1083: 1083, 1084: 1084, 1184: 1084,
            1284: 1084, 1085: 1085, 1089: 1089, 1093: 1093, 1094: 1094, 1194: 1094, 1095: 1095, 1096: 1096,
            1097: 1097, 1197: 1097, 1099: 1099, 1199: 1099
        }

        # 1. 查询所有在岗（食堂主厨、前台、跑堂）英雄
        job_rows = self.query("SELECT hero_id FROM backhome_canteen_career WHERE uid=?", (uid,)) or []
        job_archs = {hero_to_archive.get(r["hero_id"], r["hero_id"]) for r in job_rows if r.get("hero_id")}

        # 2. 查询所有进行中的委托英雄
        entrust_rows = self.query("SELECT hero_list, start_time, duration FROM backhome_canteen_entrust WHERE uid=?", (uid,)) or []
        entrust_archs = set()
        for er in entrust_rows:
            st = er.get("start_time") or 0
            dur = er.get("duration") or 0
            if now_ts < (st + dur):
                h_str = er.get("hero_list") or ""
                for h in h_str.split(","):
                    h = h.strip()
                    if h and h.isdigit():
                        entrust_archs.add(hero_to_archive.get(int(h), int(h)))

        # 3. 查询宿舍房间等级与入住映射
        dorm_rows = self.query("SELECT dorm_id, exp FROM backhome_dorm WHERE uid=?", (uid,)) or []
        dorm_levels = {}
        for r in dorm_rows:
            lvl, _ = self.get_dorm_level_and_multiplier(r.get("exp") or 0)
            dorm_levels[int(r["dorm_id"])] = lvl

        dorm_hero_rows = self.query("SELECT dorm_id, hero_id FROM backhome_dorm_hero WHERE uid=?", (uid,)) or []
        hero_dorm_map = {}
        for dhr in dorm_hero_rows:
            arch = hero_to_archive.get(dhr["hero_id"], dhr["hero_id"])
            hero_dorm_map[arch] = int(dhr["dorm_id"])

        # 4. 查询舞蹈训练营站位英雄
        idol_rows = self.query("SELECT hero_id FROM idol_trainee_pos WHERE uid=? AND hero_id > 0", (uid,)) or []
        idol_archs = {hero_to_archive.get(r["hero_id"], r["hero_id"]) for r in idol_rows if r.get("hero_id")}

        # 私人宿舍各等级 600s 基础点数（Lv 1~10）
        level_multipliers = [1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.50]

        # 5. 遍历 backhome_hero 结算疲劳
        heroes = self.query("SELECT archives_id, hero_id, fatigue, update_ts FROM backhome_hero WHERE uid=?", (uid,)) or []
        updated_heroes = []

        for h in heroes:
            arch = int(h["archives_id"])
            cur_fatigue = int(h.get("fatigue") or 0)
            last_ts = int(h.get("update_ts") or now_ts)
            dt = max(0, now_ts - last_ts)

            if cur_fatigue >= 120:
                if last_ts != now_ts:
                    self.execute("UPDATE backhome_hero SET update_ts=? WHERE uid=? AND archives_id=?", (now_ts, uid, arch))
                continue

            # 判定场景速率
            if arch in job_archs or arch in entrust_archs:
                pts_per_600s = 0.0
            elif arch in hero_dorm_map and hero_dorm_map[arch] >= 6:
                # 私人宿舍
                did = hero_dorm_map[arch]
                dlv = dorm_levels.get(did, 1)
                idx = min(max(1, dlv), 10) - 1
                pts_per_600s = level_multipliers[idx]
            elif arch in hero_dorm_map and hero_dorm_map[arch] < 6:
                # 大厅
                pts_per_600s = 1.0
            elif arch in idol_archs:
                # 训练营
                if arch in hero_dorm_map and hero_dorm_map[arch] >= 6:
                    did = hero_dorm_map[arch]
                    dlv = dorm_levels.get(did, 1)
                    idx = min(max(1, dlv), 10) - 1
                    pts_per_600s = level_multipliers[idx]
                else:
                    pts_per_600s = 1.0
            else:
                # 未入住闲置
                pts_per_600s = 0.25

            if pts_per_600s <= 0 or dt <= 0:
                continue

            # 计算增加点数
            pts_added = int(dt * (pts_per_600s / 600.0))
            if pts_added > 0:
                new_fatigue = min(120, cur_fatigue + pts_added)
                if new_fatigue >= 120:
                    new_ts = now_ts
                else:
                    advanced_sec = int(pts_added * (600.0 / pts_per_600s))
                    new_ts = last_ts + advanced_sec
                self.execute("UPDATE backhome_hero SET fatigue=?, update_ts=? WHERE uid=? AND archives_id=?",
                             (new_fatigue, new_ts, uid, arch))
                updated_heroes.append((arch, new_fatigue))

        return updated_heroes

    def deploy_dorm_hero(self, uid, dorm_id, hero_ids):
        """[Fix by Gemini 3.7-flash] 安排英雄入住房间（cs_58132 -> sc_58133，支持单人或多人列表）。"""
        now_ts = int(time.time())
        did = int(dorm_id)
        hlist = hero_ids if isinstance(hero_ids, list) else [hero_ids]
        hlist = [int(x) for x in hlist if x]

        # 映射到 archives_id
        hero_to_archive = {
            1011: 1011, 1111: 1011, 1211: 1011, 1012: 1012, 1013: 1013, 1015: 1015, 1016: 1016, 1017: 1017,
            1019: 1019, 1119: 1019, 1020: 1020, 1021: 1021, 1022: 1022, 1024: 1024, 1026: 1026, 1027: 1027,
            1127: 1027, 1028: 1028, 1032: 1032, 1132: 1032, 1033: 1033, 1133: 1033, 1034: 1034, 1035: 1035,
            1037: 1037, 1137: 1037, 1038: 1038, 1138: 1038, 1039: 1039, 1139: 1039, 1041: 1041, 1042: 1042,
            1043: 1043, 1044: 1044, 1045: 1045, 1046: 1046, 1047: 1047, 1048: 1048, 1148: 1048, 1248: 1048,
            1049: 1049, 1050: 1050, 1150: 1050, 1052: 1052, 1053: 1053, 1054: 1054, 1055: 1055, 1056: 1056,
            1156: 1056, 1058: 1058, 1158: 1058, 1059: 1059, 1060: 1060, 1061: 1061, 1066: 1066, 1166: 1066,
            1067: 1067, 1068: 1068, 1070: 1070, 1170: 1070, 1071: 1071, 1072: 1072, 1073: 1073, 1074: 1074,
            1075: 1075, 1076: 1076, 1077: 1077, 1080: 1080, 1081: 1081, 1083: 1083, 1084: 1084, 1184: 1084,
            1284: 1084, 1085: 1085, 1089: 1089, 1093: 1093, 1094: 1094, 1194: 1094, 1095: 1095, 1096: 1096,
            1097: 1097, 1197: 1097, 1099: 1099, 1199: 1099
        }
        hlist = [hero_to_archive.get(x, x) for x in hlist]

        # 如果是私人房间（6~15），该房间只容纳1位英雄，且严格遵守“一人一房、不可交换”规则
        if did >= 6:
            if not hlist:
                return 0
            target_hid = hlist[0]
            # 1. 检查目标宿舍是否已有其他角色入住
            cur_rows = self.query("SELECT hero_id FROM backhome_dorm_hero WHERE uid=? AND dorm_id=?", (uid, did))
            if cur_rows:
                existing_hid = hero_to_archive.get(cur_rows[0]["hero_id"], cur_rows[0]["hero_id"])
                if existing_hid != target_hid:
                    # 房间已有角色入驻，不可顶替
                    return 7130  # BACKHOME_HERO_OCCUPYED

            # 2. 检查待入驻角色是否已在其他私人宿舍入住
            other_rows = self.query("SELECT dorm_id FROM backhome_dorm_hero WHERE uid=? AND hero_id=? AND dorm_id >= 6", (uid, target_hid))
            if other_rows:
                assigned_did = int(other_rows[0]["dorm_id"])
                if assigned_did != did:
                    # 角色已有专属房间，不可更换
                    return 210099  # DORM_HERO_SAME_SET

            self.execute("DELETE FROM backhome_dorm_hero WHERE uid=? AND dorm_id=?", (uid, did))
            self.execute("DELETE FROM backhome_dorm_hero WHERE uid=? AND hero_id=?", (uid, target_hid))
            self.execute("""
                INSERT INTO backhome_dorm_hero (uid, dorm_id, hero_id, update_ts)
                VALUES (?, ?, ?, ?)
            """, (uid, did, target_hid, now_ts))
            return 0
        else:
            # 大厅 (did < 6)
            for hid in hlist:
                self.execute("DELETE FROM backhome_dorm_hero WHERE uid=? AND hero_id=? AND dorm_id < 6", (uid, hid))
                self.execute("""
                    INSERT INTO backhome_dorm_hero (uid, dorm_id, hero_id, update_ts)
                    VALUES (?, ?, ?, ?)
                """, (uid, did, hid, now_ts))
            return 0

    def recall_dorm_hero(self, uid, dorm_id, hero_id):
        """[Fix by Gemini 3.7-flash] 召回私人宿舍英雄（cs_58134 -> sc_58135）。"""
        self.execute("""
            DELETE FROM backhome_dorm_hero WHERE uid=? AND dorm_id=? AND hero_id=?
        """, (uid, int(dorm_id), int(hero_id)))

    def gift_furniture_to_hero(self, uid, hero_id, furniture_items):
        """[Fix by Gemini 3.7-flash] 赠送专属家具增加好感度经验并落库（cs_58136 -> sc_58137，支持 repeated 列表）。"""
        now_ts = int(time.time())
        hid = int(hero_id)
        flist = furniture_items if isinstance(furniture_items, list) else [furniture_items]
        # 查英雄所在的私人房间
        rows = self.query("SELECT dorm_id FROM backhome_dorm_hero WHERE uid=? AND hero_id=?", (uid, hid))
        did = rows[0]["dorm_id"] if rows else None

        # 确保存储表存在
        self.execute("""
            CREATE TABLE IF NOT EXISTS backhome_dorm_gift (
                uid INTEGER NOT NULL,
                dorm_id INTEGER NOT NULL,
                hero_id INTEGER NOT NULL,
                furniture_id INTEGER NOT NULL,
                num INTEGER DEFAULT 1,
                update_ts INTEGER DEFAULT 0,
                PRIMARY KEY (uid, dorm_id, furniture_id)
            )
        """)

        for item in flist:
            if not item:
                continue
            fur_id = int(item.get("id") or item.get("furniture_id") or 0)
            num = int(item.get("num") or 1)
            if did and fur_id:
                # 每件家具增加经验（基础1000点）
                self.execute("""
                    UPDATE backhome_dorm SET exp=exp+?, update_ts=? WHERE uid=? AND dorm_id=?
                """, (1000 * num, now_ts, uid, did))
                # 写入宿舍礼物表
                self.execute("""
                    INSERT INTO backhome_dorm_gift (uid, dorm_id, hero_id, furniture_id, num, update_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uid, dorm_id, furniture_id) DO UPDATE SET num=num+excluded.num, update_ts=excluded.update_ts
                """, (uid, did, hid, fur_id, num, now_ts))
            if fur_id:
                self.execute("""
                    UPDATE backhome_furniture SET give_num=give_num+?, update_ts=? WHERE uid=? AND furniture_id=?
                """, (num, now_ts, uid, fur_id))
            if hid:
                # 赠送专属家具增加英雄好感经验（每件+500）
                self.execute("""
                    UPDATE hero SET trust_exp=trust_exp+?
                    WHERE uid=? AND (id=? OR id=? OR id=?)
                """, (500 * num, uid, hid, hid // 100, hid % 10000))

    def feed_dorm_hero(self, uid, feed_type, hero_id):
        """[Fix by Gemini 3.7-flash] 给英雄喂食恢复疲劳度 +20（允许超上限至 140），单日限 1 次（cs_58138 -> sc_58139）。"""
        now_ts = int(time.time())
        ftype = int(feed_type or 1)
        hid = int(hero_id or 0)
        fatigue_list = []
        valid_archives = set(ARCHIVE_MAP.keys())
        if ftype == 1 or hid == 0:
            # 一键喂养全部英雄：对所有今日未投喂(feed_times < 1)且疲劳未超标(fatigue <= 120)的角色各投喂 1 次(+20，允许达140，好感+50)
            rows = self.query("SELECT archives_id, hero_id, fatigue, feed_times FROM backhome_hero WHERE uid=?", (uid,))
            for r in rows:
                aid = r["archives_id"]
                if aid not in valid_archives:
                    continue
                ftimes = int(r["feed_times"] or 0)
                cur_fat = int(r["fatigue"] or 0)
                if ftimes >= 1 or cur_fat > 120:
                    continue
                new_fat = min(140, cur_fat + 20)
                self.execute("""
                    UPDATE backhome_hero SET fatigue=?, feed_times=feed_times+1, total_feed_times=total_feed_times+1, update_ts=?
                    WHERE uid=? AND archives_id=?
                """, (new_fat, now_ts, uid, aid))
                self.execute("""
                    UPDATE hero SET trust_exp=trust_exp+50
                    WHERE uid=? AND (id=? OR id=? OR id=?)
                """, (uid, aid, aid // 100, aid % 10000))
                fatigue_list.append({"archives_id": aid, "fatigue": new_fat})
        else:
            # 单独喂养指定英雄：若未达到每日限次且疲劳未超标(fatigue <= 120)，恢复 +20 点疲劳（允许达 140），好感+50
            aid = self.get_archive_id(hid)
            rows = self.query("SELECT archives_id, hero_id, fatigue, feed_times FROM backhome_hero WHERE uid=? AND archives_id=?", (uid, aid))
            if rows:
                r = rows[0]
                if aid in valid_archives:
                    ftimes = int(r["feed_times"] or 0)
                    cur_fat = int(r["fatigue"] or 0)
                    if ftimes < 1 and cur_fat <= 120:
                        new_fat = min(140, cur_fat + 20)
                        self.execute("""
                            UPDATE backhome_hero SET fatigue=?, feed_times=feed_times+1, total_feed_times=total_feed_times+1, update_ts=?
                            WHERE uid=? AND archives_id=?
                        """, (new_fat, now_ts, uid, aid))
                        self.execute("""
                            UPDATE hero SET trust_exp=trust_exp+50
                            WHERE uid=? AND (id=? OR id=? OR id=?)
                        """, (uid, aid, aid // 100, aid % 10000))
                        fatigue_list.append({"archives_id": aid, "fatigue": new_fat})
        return fatigue_list

    def set_dorm_hero_skin(self, uid, hero_id, skin_id):
        """[Fix by Gemini 3.7-flash] 更换后宅英雄皮肤（cs_58126 -> sc_58127）。"""
        now_ts = int(time.time())
        self.execute("""
            UPDATE backhome_hero SET skin_id=?, update_ts=? WHERE uid=? AND (hero_id=? OR archives_id=?)
        """, (int(skin_id), now_ts, uid, int(hero_id), int(hero_id)))

    def revise_dorm_positions(self, uid, dorm_pos_list):
        """[Fix by Gemini 3.7-flash] 修改私人宿舍门牌位置（cs_58146 -> sc_58147）。"""
        now_ts = int(time.time())
        for entry in (dorm_pos_list or []):
            did = int(entry.get("architecture_id") or 0)
            pos = int(entry.get("pos_id") or 0)
            if did and pos:
                self.execute("""
                    UPDATE backhome_dorm SET pos_id=?, update_ts=? WHERE uid=? AND dorm_id=?
                """, (pos, now_ts, uid, did))

    def get_all_shop_cfg(self):
        """动态生成全量商店配置数据（sc_20007）：委托给 ShopService 领域服务"""
        from shop_service import ShopService
        return ShopService.get_instance(db=self).get_shop_cfg_payload()

    def get_all_shop_data(self, uid):
        """动态生成全量 116 商店数据（sc_20009）：委托给 ShopService 领域服务"""
        from shop_service import ShopService
        return ShopService.get_instance(db=self).get_shop_data_payload(uid)

    def get_single_shop_data(self, uid, shop_id):
        """获取单商店变动信息（sc_20005 changed_shop_info）：委托给 ShopService 领域服务"""
        from shop_service import ShopService
        return ShopService.get_instance(db=self).get_single_shop_payload(uid, shop_id)

    _SKIN_DRAW_CFG = None

    def _load_skin_draw_pool_cfg(self):
        if self._SKIN_DRAW_CFG is None:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_draw_pool_cfg.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        self._SKIN_DRAW_CFG = json.load(f)
                except Exception:
                    self._SKIN_DRAW_CFG = {}
            else:
                self._SKIN_DRAW_CFG = {}
        return self._SKIN_DRAW_CFG

    def get_skin_draw_pool_state(self, uid, pool_id):
        """获取玩家在指定卡池中的库存状态：{drop_id: {'drop_id': int, 'remain_num': int, 'drawn_num': int}}。
        若尚未初始化，则从 skin_draw_pool_cfg.json 中读取初始 total 自动入库初始化。
        """
        pid = int(pool_id)
        rows = self.query(
            "SELECT drop_id, remain_num, drawn_num FROM user_skin_draw_pool WHERE uid=? AND pool_id=?",
            (uid, pid))
        if rows:
            return {int(r["drop_id"]): {"drop_id": int(r["drop_id"]), "remain_num": int(r["remain_num"]), "drawn_num": int(r["drawn_num"])} for r in rows}
        
        # 初次访问，从配置初始化
        cfgs = self._load_skin_draw_pool_cfg()
        pcfg = cfgs.get(str(pid)) or cfgs.get(pid)
        state = {}
        now = int(time.time())
        if pcfg and "drops" in pcfg:
            for did_str, dinfo in pcfg["drops"].items():
                did = int(did_str)
                tot = int(dinfo.get("total") or 0)
                state[did] = {"drop_id": did, "remain_num": tot, "drawn_num": 0}
                self.upsert("user_skin_draw_pool", uid,
                            {"pool_id": pid, "drop_id": did, "remain_num": tot, "drawn_num": 0, "update_ts": now},
                            keys=("uid", "pool_id", "drop_id"))
        return state

    def update_skin_draw_pool_item(self, uid, pool_id, drop_id, remain_num, drawn_num):
        """更新单个 drop_id 的剩余和已抽数量。"""
        now = int(time.time())
        self.upsert("user_skin_draw_pool", uid,
                    {"pool_id": int(pool_id), "drop_id": int(drop_id),
                     "remain_num": int(remain_num), "drawn_num": int(drawn_num), "update_ts": now},
                    keys=("uid", "pool_id", "drop_id"))

    def get_skin_story_state(self, uid, activity_id):
        """获取指定换装主活动已完成的 story_id 列表。"""
        rows = self.query(
            "SELECT story_id FROM user_skin_story WHERE uid=? AND activity_id=?",
            (uid, int(activity_id)))
        return [int(r["story_id"]) for r in rows]

    def finish_skin_story(self, uid, activity_id, story_id):
        """标记换装活动剧情已完成。"""
        now = int(time.time())
        self.upsert("user_skin_story", uid,
                    {"activity_id": int(activity_id), "story_id": int(story_id), "finish_ts": now},
                    keys=("uid", "activity_id", "story_id"))

    # ---------- AI 虚拟好友与对话记忆 ----------

    def sync_ai_characters(self, roster_path=None):
        """同步/增量更新 AI 角色列表（数据驱动）。
        优先读取 data/ai_characters_roster.json。
        对不存在的 char_id 进行 INSERT；对已有记录保持原有配置不覆盖（保护自定义修改与历史）。
        """
        import json
        import os

        target_path = roster_path
        if not target_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            target_path = os.path.join(base_dir, "data", "ai_characters_roster.json")

        if not os.path.exists(target_path):
            return 0

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                roster_data = json.load(f)
        except Exception as e:
            logger.warning(f"加载 ai_characters_roster.json 失败: {e}")
            return 0

        now = int(time.time())
        inserted_count = 0
        for item in roster_data:
            char_id = int(item["char_id"])
            try:
                existing = self.query("SELECT char_id FROM ai_characters WHERE char_id = ?", (char_id,))
                if not existing:
                    self.execute(
                        "INSERT INTO ai_characters (char_id, char_name, hero_ids, avatar_icon, icon_frame, "
                        "chat_bubble, info_background, level, sign, ip_location, greeting_msg, system_prompt, "
                        "is_active, created_at, update_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            char_id,
                            str(item.get("char_name") or ""),
                            str(item.get("hero_ids") or ""),
                            int(item.get("avatar_icon") or 0),
                            int(item.get("icon_frame") or 2001),
                            int(item.get("chat_bubble") or 9001),
                            int(item.get("info_background") or 4001),
                            int(item.get("level") or 80),
                            str(item.get("sign") or ""),
                            str(item.get("ip_location") or "深空之眼"),
                            str(item.get("greeting_msg") or ""),
                            str(item.get("system_prompt") or ""),
                            int(item.get("is_active", 1)),
                            now,
                            now,
                        ),
                    )
                    inserted_count += 1
                else:
                    avatar = int(item.get("avatar_icon") or 0)
                    if avatar > 0:
                        self.execute("UPDATE ai_characters SET avatar_icon = ? WHERE char_id = ?", (avatar, char_id))
                    if item.get("sign") == "":
                        self.execute("UPDATE ai_characters SET sign = '' WHERE char_id = ?", (char_id,))
                    hids = str(item.get("hero_ids") or "").strip()
                    if hids:
                        self.execute("UPDATE ai_characters SET hero_ids = ? WHERE char_id = ?", (hids, char_id))
            except Exception as e:
                logger.warning(f"同步 AI 角色 {char_id} 失败: {e}")

        # 清除/停用可能存在的联动角色（如亚莉莎 1045、雪儿 1046）
        try:
            self.execute("DELETE FROM ai_characters WHERE char_name IN ('亚莉莎', '雪儿') OR hero_ids LIKE '%1045%' OR hero_ids LIKE '%1046%'")
        except Exception:
            pass

        # 清除老旧脏数据好友（如孤立历史测试账号 2149618442）
        try:
            self.execute("DELETE FROM friends WHERE friend_uid = 2149618442")
        except Exception:
            pass

        return inserted_count

    def init_ai_characters(self):
        """初始化 AI 角色种子数据（幂等：优先读取 roster 配置文件，缺失时保底）。"""
        self.sync_ai_characters()
        existing_any = self.query("SELECT char_id FROM ai_characters LIMIT 1")
        if existing_any:
            return

        default_chars = [
            (90001001, "海拉", "1094,1194", 2111941, 2001, 9008, 4006, 80,
             "", "欧莫菲斯", "嗯，你来了。我……在等你。",
             "# Role Definition: 海拉（Hela / 暗星·海拉 / 悼亡之蝶·海拉）\n\n你现在完全扮演手游《深空之眼》（Aether Gazer）中的修正者——海拉（Hela）。",
             1),
        ]
        now = int(time.time())
        for row in default_chars:
            char_id = row[0]
            try:
                existing = self.query("SELECT char_id FROM ai_characters WHERE char_id = ?", (char_id,))
                if not existing:
                    self.execute(
                        "INSERT INTO ai_characters (char_id, char_name, hero_ids, avatar_icon, icon_frame, "
                        "chat_bubble, info_background, level, sign, ip_location, greeting_msg, system_prompt, "
                        "is_active, created_at, update_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11], row[12], now, now)
                    )
            except Exception as e:
                pass

    def get_ai_character_by_hero_id(self, hero_id):
        """根据 hero_id 查找对应的 AI 角色。"""
        hero_str = str(hero_id).strip()
        chars = self.get_active_ai_characters()
        for c in chars:
            hids = [h.strip() for h in (c.get("hero_ids") or "").split(",") if h.strip()]
            if hero_str in hids:
                return c
        return None

    def get_active_ai_characters(self):
        """获取所有已启用的 AI 角色。"""
        return self.query("SELECT * FROM ai_characters WHERE is_active = 1 ORDER BY char_id ASC")

    def get_ai_character(self, char_id):
        """获取单个 AI 角色。"""
        rows = self.query("SELECT * FROM ai_characters WHERE char_id = ?", (int(char_id),))
        return rows[0] if rows else None

    def save_ai_chat_message(self, uid, char_id, role, content, timestamp=None):
        """记录一条聊天消息并同步到 friend_chat 表。"""
        now = int(timestamp or time.time())
        uid_int = int(uid)
        char_id_int = int(char_id)
        role_str = str(role)
        content_str = str(content)
        if role_str not in ("user", "assistant"):
            raise ValueError(f"不支持的 AI 聊天角色: {role_str}")

        # 同一秒内的玩家消息与 AI 回复不能共用 msg_id；锁内探测并递增到空闲编号。
        with self._ai_chat_lock:
            msg_id = int(now * 1000 + (char_id_int % 1000))
            with self.transaction():
                while self.query("SELECT 1 FROM friend_chat WHERE uid=? AND msg_id=?", (uid_int, msg_id)):
                    msg_id += 1
                self.execute(
                    "INSERT INTO ai_chat_memory (uid, char_id, role, content, timestamp, is_read) VALUES (?, ?, ?, ?, ?, 1)",
                    (uid_int, char_id_int, role_str, content_str, now)
                )
                sender_uid = uid_int if role_str == "user" else char_id_int
                self.execute(
                    "INSERT INTO friend_chat (uid, msg_id, sender_uid, peer_uid, content, timestamp, update_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid_int, msg_id, sender_uid, char_id_int, content_str, now, now)
                )
        return msg_id

    def get_ai_chat_history(self, uid, char_id, limit=20):
        """获取最近 N 轮对话历史（按时间正序）。"""
        rows = self.query(
            "SELECT role, content, timestamp FROM ai_chat_memory WHERE uid = ? AND char_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (int(uid), int(char_id), int(limit))
        )
        return list(reversed(rows))

    def get_all_ai_characters(self):
        """获取所有 AI 角色（包含未启用）。"""
        return self.query("SELECT * FROM ai_characters ORDER BY char_id ASC")

    def update_ai_character(self, char_id, char_name=None, sign=None, ip_location=None, greeting_msg=None, system_prompt=None, is_active=None):
        """更新指定 AI 角色的人设配置。"""
        updates = []
        params = []
        now = int(time.time())
        if char_name is not None:
            updates.append("char_name = ?")
            params.append(str(char_name))
        if sign is not None:
            updates.append("sign = ?")
            params.append(str(sign))
        if ip_location is not None:
            updates.append("ip_location = ?")
            params.append(str(ip_location))
        if greeting_msg is not None:
            updates.append("greeting_msg = ?")
            params.append(str(greeting_msg))
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(str(system_prompt))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if not updates:
            return False
        updates.append("update_ts = ?")
        params.append(now)
        params.append(int(char_id))
        sql = f"UPDATE ai_characters SET {', '.join(updates)} WHERE char_id = ?"
        cur = self.execute(sql, tuple(params))
        return cur.rowcount > 0

    def get_user_custom_prompts(self, uid):
        """获取玩家自定义的角色专属人格提示词字典。"""
        if not uid:
            return {}
        u_rows = self.query("SELECT extra FROM users WHERE uid = ?", (int(uid),))
        if not u_rows or not u_rows[0].get("extra"):
            return {}
        extra = u_rows[0]["extra"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                return {}
        return extra.get("ai_custom_prompts", {})

    def get_character_persona_prompt(self, uid, char_id):
        """获取指定角色的有效人格提示词。
        优先读取玩家 users.extra['ai_custom_prompts']，支持 char_id 与关联 hero_id 双向索引；
        其次读取 ai_characters.system_prompt；若均未配置或为空，则返回空字符串 ''（严格留空）。
        """
        cid_str = str(char_id)
        c_row = self.get_ai_character(char_id)
        if not c_row and hasattr(self, "get_ai_character_by_hero_id"):
            c_row = self.get_ai_character_by_hero_id(char_id)

        if uid:
            prompts = self.get_user_custom_prompts(uid)
            if cid_str in prompts and str(prompts[cid_str] or "").strip():
                return str(prompts[cid_str]).strip()
            if c_row:
                c_cid_str = str(c_row.get("char_id", ""))
                if c_cid_str and c_cid_str in prompts and str(prompts[c_cid_str] or "").strip():
                    return str(prompts[c_cid_str]).strip()
                for hid in str(c_row.get("hero_ids", "")).split(","):
                    hid = hid.strip()
                    if hid and hid in prompts and str(prompts[hid] or "").strip():
                        return str(prompts[hid]).strip()

        # 全局兜底
        if c_row and c_row.get("system_prompt"):
            return str(c_row["system_prompt"] or "").strip()
        return ""

    def set_character_persona_prompt(self, uid, char_id, prompt):
        """设置或清空玩家为指定角色配置的专属人格提示词。
        如果 prompt 为空字符串或 None，则置空该角色的配置，恢复为未配置留空状态。
        """
        if not uid:
            return False
        with self._ai_chat_lock:
            u_rows = self.query("SELECT extra FROM users WHERE uid = ?", (int(uid),))
            if not u_rows:
                return False
            raw_extra = u_rows[0].get("extra") or "{}"
            extra = json.loads(raw_extra) if isinstance(raw_extra, str) else dict(raw_extra)
            custom_prompts = extra.setdefault("ai_custom_prompts", {})
            cid_str = str(char_id)
            clean_prompt = str(prompt or "").strip()
            if clean_prompt:
                custom_prompts[cid_str] = clean_prompt
            else:
                custom_prompts[cid_str] = ""
            self.execute("UPDATE users SET extra = ? WHERE uid = ?", (json.dumps(extra, ensure_ascii=False), int(uid)))
            return True

    def clear_ai_chat_history(self, uid, char_id=None):
        """清空指定玩家与 AI 角色的聊天记录（支持清空单角色或清空全部），并记录截断时间戳。"""
        uid_int = int(uid)
        now_ts = int(time.time())
        memory_deleted = 0
        friend_deleted = 0
        with self.transaction():
            if char_id is not None:
                char_id_int = int(char_id)
                # 先利用仍存在的 ai_chat_memory 精确识别迁移前没有 peer_uid 的玩家消息。
                cur = self.execute(
                    "DELETE FROM friend_chat WHERE uid=? AND (peer_uid=? OR sender_uid=? OR "
                    "(sender_uid=? AND EXISTS (SELECT 1 FROM ai_chat_memory m WHERE m.uid=? "
                    "AND m.char_id=? AND m.role='user' AND m.timestamp=friend_chat.timestamp "
                    "AND m.content=friend_chat.content)))",
                    (uid_int, char_id_int, char_id_int, uid_int, uid_int, char_id_int)
                )
                friend_deleted = max(0, cur.rowcount)
                cur = self.execute(
                    "DELETE FROM ai_chat_memory WHERE uid=? AND char_id=?",
                    (uid_int, char_id_int)
                )
                memory_deleted = max(0, cur.rowcount)
            else:
                ai_ids = [int(r["char_id"]) for r in self.query("SELECT char_id FROM ai_characters")]
                if ai_ids:
                    placeholders = ",".join("?" for _ in ai_ids)
                    args = [uid_int] + ai_ids + ai_ids + [uid_int, uid_int]
                    cur = self.execute(
                        f"DELETE FROM friend_chat WHERE uid=? AND (peer_uid IN ({placeholders}) "
                        f"OR sender_uid IN ({placeholders}) OR (sender_uid=? AND EXISTS ("
                        "SELECT 1 FROM ai_chat_memory m WHERE m.uid=? AND m.role='user' "
                        "AND m.timestamp=friend_chat.timestamp AND m.content=friend_chat.content)))",
                        tuple(args)
                    )
                    friend_deleted = max(0, cur.rowcount)
                cur = self.execute("DELETE FROM ai_chat_memory WHERE uid=?", (uid_int,))
                memory_deleted = max(0, cur.rowcount)

            # 在 users.extra 中持久化记录截断时间戳 clear_ts，确保大模型上下文自动过滤更早的历史记录
            u_rows = self.query("SELECT extra FROM users WHERE uid = ?", (uid_int,))
            if u_rows:
                raw_extra = u_rows[0].get("extra") or "{}"
                try:
                    extra = json.loads(raw_extra) if isinstance(raw_extra, str) else dict(raw_extra)
                except Exception:
                    extra = {}
                clear_map = extra.setdefault("ai_chat_clear_ts", {})
                if char_id is not None:
                    clear_map[str(int(char_id))] = now_ts
                else:
                    clear_map["all"] = now_ts
                self.execute("UPDATE users SET extra = ? WHERE uid = ?", (json.dumps(extra, ensure_ascii=False), uid_int))

        return {"memory_deleted": memory_deleted, "friend_deleted": friend_deleted, "clear_ts": now_ts}

    def get_ai_chat_clear_ts(self, uid, char_id=None) -> int:
        """获取指定玩家对指定角色（或全角色）的最晚清除截断时间戳。"""
        if not uid:
            return 0
        u_rows = self.query("SELECT extra FROM users WHERE uid = ?", (int(uid),))
        if not u_rows:
            return 0
        raw_extra = u_rows[0].get("extra") or "{}"
        try:
            extra = json.loads(raw_extra) if isinstance(raw_extra, str) else dict(raw_extra)
        except Exception:
            return 0
        clear_map = extra.get("ai_chat_clear_ts", {})
        if not isinstance(clear_map, dict):
            return 0
        try:
            all_ts = int(clear_map.get("all", 0) or 0)
        except (ValueError, TypeError):
            all_ts = 0
        try:
            char_ts = int(clear_map.get(str(int(char_id)), 0) or 0) if char_id is not None else 0
        except (ValueError, TypeError):
            char_ts = 0
        return max(all_ts, char_ts)


    def get_ai_chat_stats(self, uid):
        """统计 AI 对话总轮次与各修正者活跃分布。"""
        total_rows = self.query("SELECT COUNT(*) as count FROM ai_chat_memory WHERE uid = ?", (int(uid),))
        total_turns = total_rows[0]["count"] if total_rows else 0
        char_counts = self.query(
            "SELECT char_id, COUNT(*) as msg_count, MAX(timestamp) as last_ts "
            "FROM ai_chat_memory WHERE uid = ? GROUP BY char_id",
            (int(uid),)
        )
        return {
            "total_messages": total_turns,
            "characters": {r["char_id"]: {"count": r["msg_count"], "last_ts": r["last_ts"]} for r in char_counts}
        }

    # ---------- 四大高难周常玩法数据持久化 ----------

    def get_boss_challenge_progress(self, uid):
        """获取梦境再构主进度（含模式、周期、次数、刷新时间戳）。"""
        rows = self.query("SELECT * FROM boss_challenge_progress WHERE uid=?", (int(uid),))
        return rows[0] if rows else None

    def set_boss_challenge_mode(self, uid, select_mode):
        """切换梦境再构模式与子模式：
        select in (3, 4) -> area_id = select, mode = 1 (常规梦境)
        select in (101, 102) -> advance_id = select, mode = 2 (扭曲梦境)
        select == 1 -> mode = 1
        select == 2 -> mode = 2
        select == 0 -> mode = 0 (重置为未选)
        """
        now = int(time.time())
        m = int(select_mode or 0)
        area_id = None
        advance_id = None
        mode = 0
        if m in (3, 4):
            area_id = m
            mode = 1
        elif m in (101, 102):
            advance_id = m
            mode = 2
        elif m in (1, 2):
            mode = m
        else:
            mode = 0

        prog = self.get_boss_challenge_progress(uid)
        cur_area = area_id if area_id is not None else (int(prog["area_id"]) if prog and prog.get("area_id") else 4)
        cur_adv = advance_id if advance_id is not None else (int(prog["advance_id"]) if prog and prog.get("advance_id") else 101)

        self.execute(
            "INSERT INTO boss_challenge_progress (uid, mode, area_id, advance_id, update_ts) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET mode=excluded.mode, area_id=excluded.area_id, advance_id=excluded.advance_id, update_ts=excluded.update_ts",
            (int(uid), mode, cur_area, cur_adv, now)
        )

    def check_and_lazy_refresh_boss_challenge(self, uid, now_ts=None):
        """
        梦境再构惰性刷新机制：
        1. 周四 05:00 周期轮换：重置得分、重置通关进度、重置已领奖励、释放锁定角色、重置挑战次数。
        2. 每日 05:00 日常刷新：重置每日已用挑战次数 (use_times = 0)。
        """
        if now_ts is None:
            now_ts = int(time.time())
        import datetime
        dt = datetime.datetime.fromtimestamp(now_ts)
        
        # 计算当前周期的周四 05:00 起点
        target_weekday = 3 # Thursday
        days_ahead = target_weekday - dt.weekday()
        next_thu_dt = dt.replace(hour=5, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_ahead)
        if next_thu_dt.timestamp() <= now_ts:
            next_thu_dt += datetime.timedelta(days=7)
        cur_thu_5am = int((next_thu_dt - datetime.timedelta(days=7)).timestamp())
        
        # 计算今天的 05:00 起点
        today_5am_dt = dt.replace(hour=5, minute=0, second=0, microsecond=0)
        if today_5am_dt.timestamp() > now_ts:
            today_5am_dt -= datetime.timedelta(days=1)
        cur_today_5am = int(today_5am_dt.timestamp())
        
        cycle_id = int((now_ts - 1700000000) // 604800)

        prog = self.get_boss_challenge_progress(uid)
        if not prog:
            self.execute(
                "INSERT OR REPLACE INTO boss_challenge_progress (uid, mode, cycle_id, area_id, use_times, last_daily_ts, last_weekly_ts, update_ts) "
                "VALUES (?, 0, ?, 4, 0, ?, ?, ?)",
                (int(uid), cycle_id, cur_today_5am, cur_thu_5am, now_ts)
            )
            return

        last_weekly = int(prog.get("last_weekly_ts") or 0)
        last_daily = int(prog.get("last_daily_ts") or 0)

        if last_weekly < cur_thu_5am:
            # 触发周常周期重置
            self.execute("DELETE FROM boss_challenge_normal WHERE uid=?", (int(uid),))
            self.execute("DELETE FROM boss_challenge_advance WHERE uid=?", (int(uid),))
            self.execute("DELETE FROM boss_challenge_scores WHERE uid=?", (int(uid),))
            self.execute("DELETE FROM boss_challenge_claimed_rewards WHERE uid=?", (int(uid),))
            self.execute(
                "UPDATE boss_challenge_progress SET cycle_id=?, use_times=0, last_daily_ts=?, last_weekly_ts=?, update_ts=? WHERE uid=?",
                (cycle_id, cur_today_5am, cur_thu_5am, now_ts, int(uid))
            )
        elif last_daily < cur_today_5am:
            # 仅触发每日挑战次数重置
            self.execute(
                "UPDATE boss_challenge_progress SET use_times=0, last_daily_ts=?, update_ts=? WHERE uid=?",
                (cur_today_5am, now_ts, int(uid))
            )

    def save_boss_challenge_affixes(self, uid, boss_id, affix_list, time_list, diff_idx):
        """保存梦境再构 Boss 自选词缀与难度。"""
        now = int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO boss_challenge_affixes (uid, boss_id, affix_index_list, time_index_list, diffculty_index, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(uid), int(boss_id), json.dumps(affix_list), json.dumps(time_list), int(diff_idx), now)
        )

    def record_boss_challenge_normal_clear(self, uid, group_id, stage_id, star_list=None, heroes=None):
        """记录普通梦境首领通关进度、三星状态、锁定角色并累加已用挑战次数。"""
        now = int(time.time())
        star_list = star_list or [1, 1, 1]
        heroes = heroes or []
        
        rows = self.query("SELECT * FROM boss_challenge_normal WHERE uid=? AND group_id=?", (int(uid), int(group_id)))
        star_info_list = []
        if rows and rows[0].get("star_info"):
            try:
                star_info_list = json.loads(rows[0]["star_info"])
            except Exception:
                star_info_list = []
        
        # 更新或追加对应 stage_id 的 star_list
        found = False
        for entry in star_info_list:
            if entry.get("stage_id") == int(stage_id):
                entry["star_list"] = star_list
                found = True
                break
        if not found:
            star_info_list.append({"stage_id": int(stage_id), "star_list": star_list})

        self.execute(
            "INSERT INTO boss_challenge_normal (uid, group_id, finish_stage, used_heroes, star_info, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, group_id) DO UPDATE SET finish_stage=excluded.finish_stage, used_heroes=excluded.used_heroes, "
            "star_info=excluded.star_info, update_ts=excluded.update_ts",
            (int(uid), int(group_id), int(stage_id), json.dumps(heroes), json.dumps(star_info_list), now)
        )
        # 累加挑战次数
        self.execute(
            "UPDATE boss_challenge_progress SET use_times = use_times + 1, update_ts = ? WHERE uid = ?",
            (now, int(uid))
        )

    def record_boss_challenge_advance_clear(self, uid, boss_id, score, heroes=None):
        """记录进阶梦境首领最高得分与锁定出战角色。"""
        now = int(time.time())
        heroes = heroes or []
        rows = self.query("SELECT score FROM boss_challenge_advance WHERE uid=? AND boss_id=?", (int(uid), int(boss_id)))
        prev_score = rows[0]["score"] if rows and rows[0].get("score") is not None else 0
        new_score = max(int(prev_score), int(score))

        self.execute(
            "INSERT INTO boss_challenge_advance (uid, boss_id, score, used_heroes, update_ts) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, boss_id) DO UPDATE SET score=excluded.score, used_heroes=excluded.used_heroes, update_ts=excluded.update_ts",
            (int(uid), int(boss_id), new_score, json.dumps(heroes), now)
        )
        self.execute(
            "INSERT OR REPLACE INTO boss_challenge_scores (uid, boss_id, score, update_ts) VALUES (?, ?, ?, ?)",
            (int(uid), int(boss_id), new_score, now)
        )

    def reset_boss_challenge_normal_boss(self, uid, group_id):
        """重置普通梦境指定首领（清空通关进度与三星，释放锁定角色）。"""
        now = int(time.time())
        self.execute(
            "UPDATE boss_challenge_normal SET finish_stage=0, used_heroes='[]', star_info='[]', update_ts=? WHERE uid=? AND group_id=?",
            (now, int(uid), int(group_id))
        )

    def reset_boss_challenge_advance_boss(self, uid, boss_id):
        """重置进阶梦境指定首领（清空该首领得分，释放锁定角色）。"""
        now = int(time.time())
        self.execute(
            "UPDATE boss_challenge_advance SET score=0, used_heroes='[]', update_ts=? WHERE uid=? AND boss_id=?",
            (now, int(uid), int(boss_id))
        )
        self.execute(
            "UPDATE boss_challenge_scores SET score=0, update_ts=? WHERE uid=? AND boss_id=?",
            (now, int(uid), int(boss_id))
        )

    def reset_boss_challenge_advance_all(self, uid):
        """重置整轮进阶梦境（清空全部首领得分，释放所有锁定角色，并将模式设为 0 待选）。"""
        now = int(time.time())
        self.execute(
            "UPDATE boss_challenge_advance SET score=0, used_heroes='[]', update_ts=? WHERE uid=?",
            (now, int(uid))
        )
        self.execute(
            "UPDATE boss_challenge_scores SET score=0, update_ts=? WHERE uid=?",
            (now, int(uid))
        )
        self.set_boss_challenge_mode(uid, 0)

    def save_boss_challenge_hero_team(self, uid, mode, boss_identifier, heroes_cfg):
        """保存首领预设出战阵容 (mode: 1=普通 group_id, 2=进阶 boss_id)。"""
        now = int(time.time())
        heroes_json = json.dumps(heroes_cfg or [])
        if int(mode) == 1:
            self.execute(
                "INSERT INTO boss_challenge_normal (uid, group_id, last_heroes_cfg, update_ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(uid, group_id) DO UPDATE SET last_heroes_cfg=excluded.last_heroes_cfg, update_ts=excluded.update_ts",
                (int(uid), int(boss_identifier), heroes_json, now)
            )
        else:
            self.execute(
                "INSERT INTO boss_challenge_advance (uid, boss_id, last_heroes_cfg, update_ts) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(uid, boss_id) DO UPDATE SET last_heroes_cfg=excluded.last_heroes_cfg, update_ts=excluded.update_ts",
                (int(uid), int(boss_identifier), heroes_json, now)
            )

    def claim_boss_challenge_reward(self, uid, reward_type, reward_id):
        """记录梦境再构已领奖励 (1=星级, 2=积分)。"""
        now = int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO boss_challenge_claimed_rewards (uid, reward_type, reward_id, update_ts) VALUES (?, ?, ?, ?)",
            (int(uid), int(reward_type), int(reward_id), now)
        )

    def claim_all_boss_challenge_point_rewards(self, uid, point_list):
        """批量记录梦境再构已领积分奖励。"""
        now = int(time.time())
        for pt in point_list:
            self.execute(
                "INSERT OR REPLACE INTO boss_challenge_claimed_rewards (uid, reward_type, reward_id, update_ts) VALUES (?, 2, ?, ?)",
                (int(uid), int(pt), now)
            )

    def set_mythic_difficulty(self, uid, difficulty):
        """切换黑区净化当前挑战难度。"""
        now = int(time.time())
        self.execute(
            "INSERT INTO mythic_public (uid, current_difficulty, update_ts) VALUES (?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET current_difficulty=excluded.current_difficulty, update_ts=excluded.update_ts",
            (int(uid), int(difficulty), now)
        )

    def claim_mythic_reward(self, uid, reward_id):
        """记录黑区净化已领星级奖励。"""
        now = int(time.time())
        self.execute(
            "INSERT OR IGNORE INTO mythic_progress_reward (uid, reward_id, update_ts) VALUES (?, ?, ?)",
            (int(uid), int(reward_id), now)
        )

    def record_mythic_partition_clear(self, uid, partition_id, star=3):
        """记录常规黑区通关分区与星数。"""
        now = int(time.time())
        self.execute(
            "INSERT INTO mythic_progress_partition (uid, partition_id, star, update_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uid, partition_id) DO UPDATE SET star=MAX(star, excluded.star), update_ts=excluded.update_ts",
            (int(uid), int(partition_id), int(star), now)
        )

    def get_mythic_final_progress(self, uid):
        """获取失序深区（终焉难度）玩家进度，支持半周惰性周期重置。"""
        now = int(time.time())
        try:
            import weekly_challenge_service
            cycle_start = weekly_challenge_service.get_next_semiweekly_5am(now) - 302400 # 3.5 days = 302400
        except Exception:
            cycle_start = now - 302400

        rows = self.query("SELECT * FROM mythic_final_progress WHERE uid=?", (int(uid),))
        if rows:
            r = rows[0]
            update_ts = r.get("update_ts") or 0
            
            # 若记录处于上一周期，惰性重置通关、领奖与战斗用时
            if update_ts < cycle_start:
                try:
                    can_choose = json.loads(r.get("can_choose_list") or "[]")
                except Exception:
                    can_choose = list(range(1, 31))
                if not can_choose:
                    can_choose = list(range(1, 31))
                now_diff = r.get("now_difficulty") or 30
                if now_diff <= 0:
                    now_diff = 30
                # 写回重置
                self.execute(
                    "UPDATE mythic_final_progress SET receive_reward_list='[]', clear_list='[]', challenge_info_json='[]', update_ts=? WHERE uid=?",
                    (now, int(uid))
                )
                return {
                    "now_difficulty": now_diff,
                    "can_choose_list": can_choose,
                    "receive_reward": [],
                    "clear_list": [],
                    "challenge_info": [],
                    "is_new_difficulty": False
                }
            
            try:
                can_choose = json.loads(r.get("can_choose_list") or "[]")
            except Exception:
                can_choose = list(range(1, 31))
            try:
                recv_rewards = json.loads(r.get("receive_reward_list") or "[]")
            except Exception:
                recv_rewards = []
            try:
                cleared = json.loads(r.get("clear_list") or "[]")
            except Exception:
                cleared = []
            try:
                challenge_info = json.loads(r.get("challenge_info_json") or "[]")
            except Exception:
                challenge_info = []
            if not can_choose:
                can_choose = list(range(1, 31))
            now_diff = r.get("now_difficulty") or 30
            if now_diff <= 0:
                now_diff = 30
            return {
                "now_difficulty": now_diff,
                "can_choose_list": can_choose,
                "receive_reward": recv_rewards,
                "clear_list": cleared,
                "challenge_info": challenge_info,
                "is_new_difficulty": bool(r.get("is_new_difficulty", 0))
            }
        # 默认初始数据：开放全部 1~30 档，选中 30 档，未挑战
        return {
            "now_difficulty": 30,
            "can_choose_list": list(range(1, 31)),
            "receive_reward": [],
            "clear_list": [],
            "challenge_info": [],
            "is_new_difficulty": False
        }

    def set_mythic_final_difficulty(self, uid, difficulty):
        """切换失序深区当前热度档位。"""
        now = int(time.time())
        prog = self.get_mythic_final_progress(uid)
        can_choose = prog["can_choose_list"]
        if int(difficulty) not in can_choose:
            can_choose.append(int(difficulty))
            can_choose.sort()
        self.execute(
            "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET now_difficulty=excluded.now_difficulty, can_choose_list=excluded.can_choose_list, update_ts=excluded.update_ts",
            (int(uid), int(difficulty), json.dumps(can_choose), json.dumps(prog["receive_reward"]), json.dumps(prog["clear_list"]), json.dumps(prog["challenge_info"]), now)
        )

    def claim_mythic_final_reward(self, uid, difficulty_id):
        """领取失序深区单档奖励。"""
        now = int(time.time())
        prog = self.get_mythic_final_progress(uid)
        recv = prog["receive_reward"]
        cleared = prog["clear_list"]
        max_cleared = max(cleared) if cleared else 0
        did = int(difficulty_id)
        if did <= max_cleared and did not in recv:
            recv.append(did)
            recv.sort()
            self.execute(
                "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET receive_reward_list=excluded.receive_reward_list, update_ts=excluded.update_ts",
                (int(uid), prog["now_difficulty"], json.dumps(prog["can_choose_list"]), json.dumps(recv), json.dumps(prog["clear_list"]), json.dumps(prog["challenge_info"]), now)
            )
            return True
        return False

    def claim_all_mythic_final_rewards(self, uid):
        """一键领取全部已通关未领取的失序深区奖励（支持直通高难度一键补齐所有低档奖励）。"""
        now = int(time.time())
        prog = self.get_mythic_final_progress(uid)
        recv = prog["receive_reward"]
        cleared = prog["clear_list"]
        max_cleared = max(cleared) if cleared else 0
        newly_claimed = []
        for did in range(1, max_cleared + 1):
            if did not in recv:
                recv.append(did)
                newly_claimed.append(did)
        if newly_claimed:
            recv.sort()
            self.execute(
                "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uid) DO UPDATE SET receive_reward_list=excluded.receive_reward_list, update_ts=excluded.update_ts",
                (int(uid), prog["now_difficulty"], json.dumps(prog["can_choose_list"]), json.dumps(recv), json.dumps(prog["clear_list"]), json.dumps(prog["challenge_info"]), now)
            )
        return newly_claimed

    def reset_mythic_final_team(self, uid):
        """重置失序深区队伍挑战状态与用时记录。"""
        now = int(time.time())
        prog = self.get_mythic_final_progress(uid)
        self.execute(
            "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET challenge_info_json='[]', update_ts=excluded.update_ts",
            (int(uid), prog["now_difficulty"], json.dumps(prog["can_choose_list"]), json.dumps(prog["receive_reward"]), json.dumps(prog["clear_list"]), '[]', now)
        )

    def reset_mythic_by_beacon(self, uid):
        """
        使用黑区信标（41101）刷新黑区与失序深区挑战：
        - 重置失序深区：通关记录 clear_list、领奖记录 receive_reward_list、出战用时 challenge_info_json 全部置空，必须重新挑战！
        - 重置常规黑区：清空 mythic_progress_reward（已领星级奖励）、清空 mythic_progress_partition（通关分区）。
        - 严格保留自然刷新周期（不影响时间戳刷新）。
        """
        now = int(time.time())
        # 1. 重置失序深区：通关记录、领奖记录、队伍用时全部清空，保留可选档位
        prog = self.get_mythic_final_progress(uid)
        self.execute(
            "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET receive_reward_list='[]', clear_list='[]', challenge_info_json='[]', update_ts=excluded.update_ts",
            (int(uid), prog["now_difficulty"], json.dumps(prog["can_choose_list"]), "[]", "[]", "[]", now)
        )
        
        # 3. 重置常规黑区
        self.execute("DELETE FROM mythic_progress_reward WHERE uid=?", (int(uid),))
        self.execute("DELETE FROM mythic_progress_partition WHERE uid=?", (int(uid),))

    def record_mythic_final_clear(self, uid, diff=30, team_id=1, use_time=45):
        """记录失序深区（终焉难度）队伍通关。支持 1~19 档单队与 20~30 档双队连续接力。"""
        now = int(time.time())
        diff = max(1, min(30, int(diff or 30)))
        team_id = max(1, min(2, int(team_id or 1)))
        use_time = max(1, int(use_time or 45))
        
        prog = self.get_mythic_final_progress(uid)
        cleared = prog["clear_list"]
        can_choose = prog["can_choose_list"]
        challenge_info = prog["challenge_info"]
        is_multi_team = (diff >= 20)
        
        if not is_multi_team:
            for d in range(1, diff + 1):
                if d not in cleared:
                    cleared.append(d)
            cleared.sort()
            if diff + 1 <= 30 and (diff + 1) not in can_choose:
                can_choose.append(diff + 1)
                can_choose.sort()
            challenge_info = [{"team_id": 1, "clear_state": 1, "use_time": use_time}]
        else:
            team_map = {item.get("team_id"): item for item in challenge_info}
            team_map[team_id] = {"team_id": team_id, "clear_state": 1, "use_time": use_time}
            challenge_info = [team_map[k] for k in sorted(team_map.keys())]
            
            if len(challenge_info) >= 2 and all(item.get("clear_state") == 1 for item in challenge_info):
                for d in range(1, diff + 1):
                    if d not in cleared:
                        cleared.append(d)
                cleared.sort()
                if diff + 1 <= 30 and (diff + 1) not in can_choose:
                    can_choose.append(diff + 1)
                    can_choose.sort()
                    
        self.execute(
            "INSERT INTO mythic_final_progress (uid, now_difficulty, can_choose_list, receive_reward_list, clear_list, challenge_info_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid) DO UPDATE SET can_choose_list=excluded.can_choose_list, clear_list=excluded.clear_list, challenge_info_json=excluded.challenge_info_json, update_ts=excluded.update_ts",
            (int(uid), prog["now_difficulty"], json.dumps(can_choose), json.dumps(prog["receive_reward"]), json.dumps(cleared), json.dumps(challenge_info), now)
        )
        return {
            "now_difficulty": prog["now_difficulty"],
            "can_choose_list": can_choose,
            "receive_reward": prog["receive_reward"],
            "clear_list": cleared,
            "challenge_info": challenge_info,
            "is_new_difficulty": False
        }

    def record_mythic_final_stage_clear(self, uid, stage_id, use_time=45):
        """记录失序深区关卡通关进度（兼容 stage_id 形式）。"""
        sid = int(stage_id)
        diff = 30
        team_id = 1
        if 3028001 <= sid <= 3028019:
            diff = sid - 3028000
            team_id = 1
        elif 3028020 <= sid <= 3028041:
            diff = 20 + (sid - 3028020) // 2
            team_id = 1 + (sid - 3028020) % 2
        return self.record_mythic_final_clear(uid, diff=diff, team_id=team_id, use_time=use_time)

    # ----------------- 迭代校验 (Core Verification) 数据持久化 -----------------

    def save_core_verification_affix(self, uid, mode_id, affix_list):
        """保存迭代校验模式自选词缀。"""
        now = int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO core_verification_affixes (uid, mode_id, affix_list, update_ts) VALUES (?, ?, ?, ?)",
            (int(uid), int(mode_id), json.dumps(affix_list), now)
        )

    def get_core_verification_affixes(self, uid):
        """获取玩家保存的所有自选词缀。"""
        rows = self.query("SELECT mode_id, affix_list FROM core_verification_affixes WHERE uid=?", (int(uid),))
        res = []
        for r in rows:
            try:
                res.append({
                    "id": int(r["mode_id"]),
                    "affix_list": json.loads(r["affix_list"] or "[]")
                })
            except Exception:
                pass
        return res

    def claim_core_verification_task(self, uid, task_id, cycle=0):
        """记录迭代校验常规已领任务奖励（cycle=0 代表首通永久，cycle>0 代表当期周常）。"""
        now = int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO core_verification_claimed_tasks (uid, task_id, cycle, update_ts) VALUES (?, ?, ?, ?)",
            (int(uid), int(task_id), int(cycle), now)
        )

    def is_core_verification_task_claimed(self, uid, task_id, cycle=0):
        """判断任务在指定周期（或永久首通 cycle=0）是否已领。"""
        rows = self.query(
            "SELECT 1 FROM core_verification_claimed_tasks WHERE uid=? AND task_id=? AND cycle=? LIMIT 1",
            (int(uid), int(task_id), int(cycle))
        )
        return len(rows) > 0

    def get_core_verification_claimed_tasks(self, uid, current_cycle=None):
        """获取迭代校验常规已领任务 ID 列表（包含永久首通 cycle=0 及当前周期 cycle 已领）。"""
        if current_cycle is not None and int(current_cycle) > 0:
            rows = self.query(
                "SELECT task_id FROM core_verification_claimed_tasks WHERE uid=? AND (cycle=0 OR cycle=?)",
                (int(uid), int(current_cycle))
            )
        else:
            rows = self.query("SELECT task_id FROM core_verification_claimed_tasks WHERE uid=?", (int(uid),))
        return [int(r["task_id"]) for r in rows]

    def clear_core_verification_data(self, uid):
        """清空指定玩家的全部迭代校验测试与战绩数据（恢复纯净全新状态）。"""
        u = int(uid)
        self.execute("DELETE FROM core_verification_stage_record WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_hero_lock WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_super_score WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_claimed_tasks WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_cl_progress WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_cl_task_claimed WHERE uid=?", (u,))
        self.execute("DELETE FROM core_verification_cl_badge_unlocked WHERE uid=?", (u,))

    def get_core_verification_stage_records(self, uid, cycle):
        """获取玩家指定周期的迭代校验关卡战绩。"""
        rows = self.query("SELECT * FROM core_verification_stage_record WHERE uid=? AND cycle=?", (int(uid), int(cycle)))
        records = {}
        for r in rows:
            records[int(r["info_id"])] = {
                "id": int(r["info_id"]),
                "boss_type": int(r["boss_type"]),
                "difficult": int(r["difficult"]),
                "sign": int(r["sign"]),
                "min_time": int(r["min_time"]),
                "score": int(r["score"])
            }
        return records

    def get_core_verification_hero_locks(self, uid, cycle):
        """获取玩家当前周期的锁定出战修正者列表 {1: [hero_ids], 2: [hero_ids]}。"""
        rows = self.query("SELECT boss_type, hero_id FROM core_verification_hero_lock WHERE uid=? AND cycle=?", (int(uid), int(cycle)))
        locks = {1: [], 2: []}
        for r in rows:
            bt = int(r["boss_type"])
            hid = int(r["hero_id"])
            if bt in locks and hid not in locks[bt]:
                locks[bt].append(hid)
        return locks

    def get_core_verification_super_scores(self, uid):
        """获取极值挑战单关最高积分映射。"""
        rows = self.query("SELECT stage_id, score FROM core_verification_super_score WHERE uid=?", (int(uid),))
        scores = {}
        for r in rows:
            scores[int(r["stage_id"])] = int(r["score"])
        return scores

    def record_core_verification_stage_clear(self, uid, cycle, info_id, boss_type, difficult, min_time_ms, score=0, heroes=None):
        """通关迭代校验关卡：标记 sign=1，更新 min_time 与 score，锁定修正者。"""
        now = int(time.time())
        info_id = int(info_id)
        cycle = int(cycle)
        boss_type = int(boss_type)
        difficult = int(difficult)
        min_time_ms = int(min_time_ms)
        score = int(score)
        heroes = heroes or []

        old_rows = self.query(
            "SELECT min_time, score FROM core_verification_stage_record WHERE uid=? AND cycle=? AND info_id=?",
            (int(uid), cycle, info_id)
        )
        best_time = min_time_ms
        best_score = score
        if old_rows:
            old_time = int(old_rows[0].get("min_time") or 0)
            old_score = int(old_rows[0].get("score") or 0)
            if old_time > 0 and (min_time_ms <= 0 or old_time < min_time_ms):
                best_time = old_time
            best_score = max(old_score, score)

        self.execute(
            "INSERT OR REPLACE INTO core_verification_stage_record (uid, cycle, info_id, boss_type, difficult, sign, min_time, score, update_ts) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (int(uid), cycle, info_id, boss_type, difficult, best_time, best_score, now)
        )

        # 向上向下兼容：通关高难度 D 时，自动补齐该 BOSS 低难度 (1..D-1) 的通关标记 (sign=1)
        for lower_d in range(1, difficult):
            lower_info_id = cycle * 1000 + boss_type * 100 + lower_d
            chk = self.query(
                "SELECT sign FROM core_verification_stage_record WHERE uid=? AND cycle=? AND info_id=?",
                (int(uid), cycle, lower_info_id)
            )
            if not chk or int(chk[0].get("sign") or 0) != 1:
                self.execute(
                    "INSERT OR REPLACE INTO core_verification_stage_record (uid, cycle, info_id, boss_type, difficult, sign, min_time, score, update_ts) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, 0, ?)",
                    (int(uid), cycle, lower_info_id, boss_type, lower_d, best_time, now)
                )

        for hid in heroes:
            hid = int(hid)
            if hid > 0:
                self.execute(
                    "INSERT OR IGNORE INTO core_verification_hero_lock (uid, cycle, boss_type, hero_id, update_ts) VALUES (?, ?, ?, ?, ?)",
                    (int(uid), cycle, boss_type, hid, now)
                )

        if difficult == 8 or score > 0:
            self.execute(
                "INSERT OR REPLACE INTO core_verification_super_score (uid, stage_id, score, update_ts) VALUES (?, ?, ?, ?)",
                (int(uid), info_id, best_score, now)
            )

    def reset_core_verification_challenge(self, uid, cycle, reset_type):
        """重置迭代校验挑战：type=0 重置全部关卡与出战锁定，type=1 仅重置出战锁定。"""
        cycle = int(cycle)
        reset_type = int(reset_type)
        if reset_type == 0:
            self.execute("DELETE FROM core_verification_stage_record WHERE uid=? AND cycle=?", (int(uid), cycle))
            self.execute("DELETE FROM core_verification_hero_lock WHERE uid=? AND cycle=?", (int(uid), cycle))
        else:
            self.execute("DELETE FROM core_verification_hero_lock WHERE uid=? AND cycle=?", (int(uid), cycle))

    # ----------------- 迭代校验·挑战模式 (Challenge Mode 1~4) 数据持久化 -----------------

    def get_core_verification_cl_progress(self, uid, activity_id):
        """获取玩家在指定挑战活动下的关卡进度与词条状态。"""
        rows = self.query("SELECT * FROM core_verification_cl_progress WHERE uid=? AND activity_id=?", (int(uid), int(activity_id)))
        prog = {}
        for r in rows:
            sid = int(r["stage_id"])
            prog[sid] = {
                "stage_id": sid,
                "is_cleared": int(r["is_cleared"]),
                "score": int(r["score"]),
                "min_time": int(r["min_time"]),
                "heroes": json.loads(r["heroes_json"] or "[]"),
                "select_buffs": json.loads(r["select_buffs"] or "[]")
            }
        return prog

    def record_core_verification_cl_stage_clear(self, uid, activity_id, mode, stage_id, min_time_ms=0, score=0, heroes=None, select_buffs=None):
        """记录挑战模式关卡通关。"""
        now = int(time.time())
        heroes = heroes or []
        select_buffs = select_buffs or []
        old = self.query(
            "SELECT min_time, score FROM core_verification_cl_progress WHERE uid=? AND activity_id=? AND stage_id=?",
            (int(uid), int(activity_id), int(stage_id))
        )
        best_time = int(min_time_ms)
        best_score = int(score)
        if old:
            ot = int(old[0].get("min_time") or 0)
            os_val = int(old[0].get("score") or 0)
            if ot > 0 and (best_time <= 0 or ot < best_time):
                best_time = ot
            best_score = max(os_val, best_score)

        self.execute(
            "INSERT OR REPLACE INTO core_verification_cl_progress (uid, activity_id, mode, stage_id, is_cleared, score, min_time, heroes_json, select_buffs, update_ts) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (int(uid), int(activity_id), int(mode), int(stage_id), best_score, best_time, json.dumps(heroes), json.dumps(select_buffs), now)
        )

    def save_core_verification_cl_buffs(self, uid, activity_id, buff_list):
        """保存挑战模式主关卡选中的战斗词条。"""
        now = int(time.time())
        rows = self.query("SELECT stage_id, mode, heroes_json FROM core_verification_cl_progress WHERE uid=? AND activity_id=?", (int(uid), int(activity_id)))
        if rows:
            for r in rows:
                self.execute(
                    "UPDATE core_verification_cl_progress SET select_buffs=?, update_ts=? WHERE uid=? AND activity_id=? AND stage_id=?",
                    (json.dumps(buff_list), now, int(uid), int(activity_id), r["stage_id"])
                )
        else:
            self.execute(
                "INSERT OR REPLACE INTO core_verification_cl_progress (uid, activity_id, mode, stage_id, is_cleared, score, min_time, heroes_json, select_buffs, update_ts) "
                "VALUES (?, ?, 1, 0, 0, 0, 0, '[]', ?, ?)",
                (int(uid), int(activity_id), json.dumps(buff_list), now)
            )

    def reset_core_verification_cl(self, uid, activity_id, stage_id=0):
        """重置挑战模式关卡进度。"""
        if stage_id and int(stage_id) > 0:
            self.execute("DELETE FROM core_verification_cl_progress WHERE uid=? AND activity_id=? AND stage_id=?", (int(uid), int(activity_id), int(stage_id)))
        else:
            self.execute("DELETE FROM core_verification_cl_progress WHERE uid=? AND activity_id=?", (int(uid), int(activity_id)))

    def claim_core_verification_cl_task(self, uid, activity_id, task_id):
        """记录挑战模式已领任务。"""
        now = int(time.time())
        self.execute(
            "INSERT OR IGNORE INTO core_verification_cl_task_claimed (uid, activity_id, task_id, update_ts) VALUES (?, ?, ?, ?)",
            (int(uid), int(activity_id), int(task_id), now)
        )

    def get_core_verification_cl_claimed_tasks(self, uid, activity_id):
        """获取挑战模式已领任务 ID 列表。"""
        rows = self.query("SELECT task_id FROM core_verification_cl_task_claimed WHERE uid=? AND activity_id=?", (int(uid), int(activity_id)))
        return [int(r["task_id"]) for r in rows]

    def unlock_core_verification_cl_badge(self, uid, badge_id):
        """解锁图鉴徽章。"""
        now = int(time.time())
        self.execute(
            "INSERT OR IGNORE INTO core_verification_cl_badge_unlocked (uid, badge_id, unlock_ts) VALUES (?, ?, ?)",
            (int(uid), int(badge_id), now)
        )

    def get_core_verification_cl_badges(self, uid):
        """获取玩家已解锁图鉴徽章列表。"""
        rows = self.query("SELECT badge_id, unlock_ts FROM core_verification_cl_badge_unlocked WHERE uid=?", (int(uid),))
        return [{"id": int(r["badge_id"]), "unlock_timestamp": int(r["unlock_ts"])} for r in rows]

    # ----------------- 迭代校验词条库 (Affix Catalog) 自定义管理 -----------------

    def sync_core_verification_affix_catalog_from_json(self, json_path=None):
        """将 core_verification_affix_custom.json 导入/同步至数据库词条库中。"""
        if json_path is None:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_verification_affix_custom.json")
        if not os.path.exists(json_path):
            return 0
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            affix_details = data.get("affix_details", {})
            now = int(time.time())
            count = 0
            for aid_str, item in affix_details.items():
                aid = int(item.get("id") or aid_str)
                name = str(item.get("name") or "")
                desc = str(item.get("desc") or "")
                cat = str(item.get("category") or "regular")
                point = int(item.get("point") or 0)
                cost = int(item.get("cost") or 0)
                affix_type = int(item.get("affix_type") or 0)
                level = int(item.get("level") or 1)
                is_custom = int(item.get("is_custom") or 0)
                self.execute(
                    "INSERT OR REPLACE INTO core_verification_affix_catalog "
                    "(id, name, desc, category, point, cost, affix_type, level, is_custom, extra_json, update_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, name, desc, cat, point, cost, affix_type, level, is_custom, json.dumps(item), now)
                )
                count += 1
            return count
        except Exception as e:
            print(f"[AccountDB] 同步词条库失败: {e}")
            return 0

    def get_core_verification_affix_catalog(self, category=None):
        """查询迭代校验词条库，可按分类 (regular/mode1_buff/mode1_debuff/mode2_buff/mode2_debuff/mode3/mode4) 过滤。"""
        if category:
            rows = self.query("SELECT * FROM core_verification_affix_catalog WHERE category=? ORDER BY id ASC", (category,))
        else:
            rows = self.query("SELECT * FROM core_verification_affix_catalog ORDER BY category, id ASC")
        result = []
        for r in rows:
            result.append({
                "id": int(r["id"]),
                "name": str(r["name"] or ""),
                "desc": str(r["desc"] or ""),
                "category": str(r["category"] or ""),
                "point": int(r["point"] or 0),
                "cost": int(r["cost"] or 0),
                "affix_type": int(r["affix_type"] or 0),
                "level": int(r["level"] or 1),
                "is_custom": int(r["is_custom"] or 0),
                "extra": json.loads(r["extra_json"] or "{}")
            })
        return result

    def upsert_core_verification_affix(self, affix_id, name, desc="", category="regular", point=0, cost=0, affix_type=0, level=1, is_custom=1, extra=None):
        """新增或修改自定义迭代校验词条。"""
        now = int(time.time())
        extra_json = json.dumps(extra or {})
        self.execute(
            "INSERT OR REPLACE INTO core_verification_affix_catalog "
            "(id, name, desc, category, point, cost, affix_type, level, is_custom, extra_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(affix_id), str(name), str(desc), str(category), int(point), int(cost), int(affix_type), int(level), int(is_custom), extra_json, now)
        )

    def delete_core_verification_affix(self, affix_id):
        """删除自定义词条。"""
        self.execute("DELETE FROM core_verification_affix_catalog WHERE id=?", (int(affix_id),))

    # ----------------- 介质攫取 (Equip Seizure) 数据持久化 -----------------

    def get_equip_seizure_progress(self, uid, now_ts=None):
        """获取玩家介质攫取进度，支持跨天跨周惰性刷新。"""
        if now_ts is None:
            now_ts = int(time.time())
        import weekly_challenge_service as _wcs
        next_weekly_rf = _wcs.get_next_thursday_5am(now_ts)
        next_affix_rf = _wcs.get_next_seizure_affix_refresh(now_ts)
        current_rate = _wcs.get_seizure_challenge_rate(now_ts)
        current_stage = _wcs.get_daily_seizure_stage_id(now_ts)
        current_affixes = _wcs.get_seizure_affixes(now_ts)
        today_tag = _wcs.get_daily_5am(now_ts)

        rows = self.query("SELECT * FROM equip_seizure_progress WHERE uid=?", (int(uid),))
        if not rows:
            prog = {
                "uid": int(uid),
                "stage_id": current_stage,
                "challenge_rate": current_rate,
                "affix_id_list": current_affixes,
                "affix_refresh_ts": next_affix_rf,
                "today_max_score": 0,
                "sum_score": 0,
                "refresh_ts": next_weekly_rf,
                "got_reward_id_list": [],
                "last_day_tag": today_tag,
                "team_heroes": []
            }
            self.execute(
                "INSERT INTO equip_seizure_progress (uid, stage_id, challenge_rate, affix_id_list, "
                "affix_refresh_ts, today_max_score, sum_score, refresh_ts, got_reward_id_list, "
                "last_day_tag, team_heroes, update_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(uid), current_stage, current_rate, json.dumps(current_affixes),
                 next_affix_rf, 0, 0, next_weekly_rf, json.dumps([]), today_tag, json.dumps([]), now_ts)
            )
            return prog

        r = rows[0]
        prog_stage = int(r.get("stage_id") or current_stage)
        prog_rate = float(r.get("challenge_rate") or current_rate)
        prog_affix_ts = int(r.get("affix_refresh_ts") or next_affix_rf)
        try:
            prog_affixes = json.loads(r.get("affix_id_list") or "[]")
        except Exception:
            prog_affixes = current_affixes
        today_score = int(r.get("today_max_score") or 0)
        sum_score = int(r.get("sum_score") or 0)
        refresh_ts = int(r.get("refresh_ts") or next_weekly_rf)
        try:
            got_rewards = json.loads(r.get("got_reward_id_list") or "[]")
        except Exception:
            got_rewards = []
        last_day = int(r.get("last_day_tag") or 0)
        try:
            team_heroes = json.loads(r.get("team_heroes") or "[]")
        except Exception:
            team_heroes = []

        need_save = False

        # 1. 跨周检测 (周四 05:00 刷新)
        if now_ts >= refresh_ts or refresh_ts != next_weekly_rf:
            if now_ts >= refresh_ts:
                today_score = 0
                sum_score = 0
                got_rewards = []
            refresh_ts = next_weekly_rf
            need_save = True

        # 2. 跨日检测 (每日 05:00 刷新)
        if last_day != today_tag:
            today_score = 0
            last_day = today_tag
            need_save = True

        # 3. 动态属性更新（每日关卡、倍率与词缀）
        if prog_stage != current_stage or prog_rate != current_rate:
            prog_stage = current_stage
            prog_rate = current_rate
            need_save = True

        if now_ts >= prog_affix_ts or not prog_affixes or prog_affixes != current_affixes or prog_affix_ts != next_affix_rf:
            prog_affixes = current_affixes
            prog_affix_ts = next_affix_rf
            need_save = True

        if need_save:
            self.execute(
                "UPDATE equip_seizure_progress SET stage_id=?, challenge_rate=?, affix_id_list=?, "
                "affix_refresh_ts=?, today_max_score=?, sum_score=?, refresh_ts=?, got_reward_id_list=?, "
                "last_day_tag=?, team_heroes=?, update_ts=? WHERE uid=?",
                (prog_stage, prog_rate, json.dumps(prog_affixes), prog_affix_ts, today_score,
                 sum_score, refresh_ts, json.dumps(got_rewards), last_day, json.dumps(team_heroes), now_ts, int(uid))
            )

        return {
            "uid": int(uid),
            "stage_id": prog_stage,
            "challenge_rate": prog_rate,
            "affix_id_list": prog_affixes,
            "affix_refresh_ts": prog_affix_ts,
            "today_max_score": today_score,
            "sum_score": sum_score,
            "refresh_ts": refresh_ts,
            "got_reward_id_list": got_rewards,
            "last_day_tag": last_day,
            "team_heroes": team_heroes
        }

    def save_equip_seizure_battle_score(self, uid, battle_score, team_heroes=None, now_ts=None):
        """保存介质攫取战斗得分与出战阵容。"""
        if now_ts is None:
            now_ts = int(time.time())
        prog = self.get_equip_seizure_progress(uid, now_ts)
        cur_today_max = int(prog.get("today_max_score") or 0)
        cur_sum = int(prog.get("sum_score") or 0)
        b_score = int(battle_score or 0)

        new_today_max = max(cur_today_max, b_score)
        delta = max(0, b_score - cur_today_max)
        new_sum = cur_sum + delta

        save_team = team_heroes if team_heroes is not None else prog.get("team_heroes", [])

        import weekly_challenge_service as _wcs
        today_tag = _wcs.get_daily_5am(now_ts)

        self.execute(
            "UPDATE equip_seizure_progress SET today_max_score=?, sum_score=?, team_heroes=?, last_day_tag=?, update_ts=? WHERE uid=?",
            (new_today_max, new_sum, json.dumps(save_team), today_tag, now_ts, int(uid))
        )
        return True, b_score, new_today_max, new_sum

    def claim_equip_seizure_rewards(self, uid, reward_ids):
        """记录已领取的介质攫取积分奖励（排重追加）。"""
        prog = self.get_equip_seizure_progress(uid)
        got_list = list(prog.get("got_reward_id_list") or [])
        added = []
        for rid in reward_ids:
            rid_int = int(rid)
            if rid_int not in got_list:
                got_list.append(rid_int)
                added.append(rid_int)
        if added:
            self.execute(
                "UPDATE equip_seizure_progress SET got_reward_id_list=?, update_ts=? WHERE uid=?",
                (json.dumps(got_list), int(time.time()), int(uid))
            )
        return got_list

    def save_equip_seizure_team(self, uid, team_heroes):
        """保存介质攫取专用编队阵容。"""
        self.execute(
            "UPDATE equip_seizure_progress SET team_heroes=?, update_ts=? WHERE uid=?",
            (json.dumps(team_heroes or []), int(time.time()), int(uid))
        )

    # ---------- 因果观测（WarChess）数据接口 ----------

    def init_warchess_chapters(self):
        """初始化因果观测（WarChess）章节种子数据。"""
        c = self._conn()
        seed_chapters = [
            (4040100, "工厂_5", 0, 0),
            (4040101, "工厂_1", 0, 0),
            (4040102, "工厂_2", 0, 0),
            (4040103, "工厂_3", 0, 0),
            (4040104, "工厂_4", 0, 0),
            (4040200, "测试地图", 0, 0),
            (4040201, "墓园·外", 0, 0),
            (4040202, "墓园·中", 0, 0),
            (4040203, "墓园·深处", 0, 0),
            (4040204, "墓园·迷失", 0, 0),
            (4040301, "原点·远端", 0, 0),
            (4040302, "原点·近端", 0, 0),
            (4040303, "原点·高处", 0, 0),
            (4040304, "原点·塔下", 0, 0),
            (4040305, "原点·狭长", 0, 0),
            (4040401, "异常信号", 0, 0),
            (4040402, "风暴眼", 0, 0),
            (4040403, "低语", 0, 0),
            (4040404, "深海·#1", 0, 0),
            (4040405, "深海·#2", 0, 0),
            (4040406, "极深海·#1", 0, 0),
            (4040407, "极深海·#2", 0, 0),
            (4040408, "集结", 0, 0),
            (4040501, "回溯", 0, 0),
            (4040502, "沉沦的骑士", 0, 0),
            (4040503, "陷位", 0, 0),
            (4040504, "叠影", 0, 0),
            (4040505, "深思", 0, 0),
            (4040601, "微录·入口", 0, 0),
            (4040602, "微录·断代", 0, 0),
            (4040603, "微录·千山", 0, 0),
            (4040604, "微录·归一", 0, 0),
            (4040605, "微录·晚霞", 0, 0),
            (104040201, "墓园·外", 0, 0),
            (104040202, "墓园·中", 0, 0),
            (104040203, "墓园·深处", 0, 0),
            (104040204, "墓园·迷失", 0, 0),
        ]
        for cid, name, ctype, ots in seed_chapters:
            c.execute(
                "INSERT OR IGNORE INTO warchess_chapter (chapter_id, name, chapter_type, open_ts) VALUES (?, ?, ?, ?)",
                (cid, name, ctype, ots)
            )
        try:
            c.execute("UPDATE login_push SET dynamic = 1 WHERE cmd IN (34007, 34021, 49001, 49023, 14501, 14503, 89201, 90051, 89125, 90007, 89207)")
        except Exception:
            pass
        if not self._in_txn():
            c.commit()

    def get_warchess_overview(self, uid):
        """获取因果观测全量进度概要（用于 sc_49001 组包）。
        返回: (current_chapter, chapter_info_list)
        """
        uid = int(uid)
        row = self.query("SELECT current_chapter FROM warchess_map WHERE uid = ? AND activity_id = 0", (uid,))
        current_chapter = int(row[0]["current_chapter"]) if row else 0

        chapters = self.query("SELECT chapter_id FROM warchess_chapter ORDER BY chapter_id")
        box_rows = self.query("SELECT chapter_id, box_id, num FROM warchess_box WHERE uid = ?", (uid,))
        box_map = {}
        for r in box_rows:
            cid = int(r["chapter_id"])
            if cid not in box_map:
                box_map[cid] = {}
            box_map[cid][int(r["box_id"])] = int(r["num"])

        chapter_info_list = []
        for ch in chapters:
            cid = int(ch["chapter_id"])
            c_boxes = box_map.get(cid, {})
            s_num = c_boxes.get(10203, 0)
            l_num = c_boxes.get(10204, 0)
            box_list = [
                {"id": 10203, "num": s_num},
                {"id": 10204, "num": l_num},
            ]
            if 106031 in c_boxes:
                box_list.append({"id": 106031, "num": c_boxes[106031]})

            chapter_info_list.append({
                "chapter_id": cid,
                "box": box_list
            })

        return current_chapter, chapter_info_list

    def get_warchess_open_list(self):
        """获取因果观测开放时间戳列表（用于 sc_49023 组包）。"""
        rows = self.query("SELECT chapter_id, open_ts FROM warchess_chapter ORDER BY chapter_id")
        return [
            {"chapter_id": int(r["chapter_id"]), "timestamp": int(r["open_ts"] or 0)}
            for r in rows
        ]

    WARCHESS_SPAWN_POINTS = {
        4040100: (7.0, 5.0),
        4040101: (17.0, 1.0),
        4040102: (3.0, 2.0),
        4040103: (3.0, 2.0),
        4040104: (9.0, 3.0),
        4040106: (10.0, 3.0),
        4040107: (10.0, 3.0),
        4040108: (4.0, 2.0),
        4040200: (7.0, 7.0),
        4040201: (3.0, 2.0),
        4040202: (5.0, 4.0),
        4040203: (7.0, 6.0),
        4040204: (3.0, 3.0),
        4040301: (2.0, 2.0),
        4040302: (14.0, 2.0),
        4040303: (16.0, 1.0),
        4040304: (15.0, 3.0),
        4040305: (15.0, 3.0),
        4040401: (5.0, 4.0),
        4040402: (7.0, 8.0),
        4040403: (10.0, 2.0),
        4040404: (10.0, 2.0),
        4040405: (4.0, 2.0),
        4040406: (4.0, 2.0),
        4040407: (2.0, 2.0),
        4040408: (1.0, 1.0),
        4040501: (9.0, 4.0),
        4040502: (8.0, 12.0),
        4040503: (14.0, 8.0),
        4040504: (10.0, 8.0),
        4040505: (13.0, 10.0),
        4040601: (3.0, 9.0),
        4040602: (6.0, 1.0),
        4040603: (4.0, 5.0),
        4040604: (2.0, 3.0),
        4040605: (5.0, 2.0),
        104040201: (3.0, 2.0),
        104040202: (5.0, 4.0),
        104040203: (7.0, 6.0),
        104040204: (3.0, 3.0),
    }

    CHAPTER_FOG_U32_COUNT = {
        4040100: 20, 4040101: 12, 4040102: 25, 4040103: 25, 4040104: 25, 4040106: 8,
        4040107: 16, 4040108: 12, 4040200: 31, 4040201: 31, 4040202: 14, 4040203: 18,
        4040204: 16, 4040301: 47, 4040302: 43, 4040303: 37, 4040304: 32, 4040305: 32,
        4040401: 20, 4040402: 29, 4040403: 32, 4040404: 18, 4040405: 17, 4040406: 15,
        4040407: 14, 4040408: 14, 4040501: 14, 4040502: 35, 4040503: 35, 4040504: 35,
        4040505: 35, 4040601: 29, 4040602: 39, 4040603: 39, 4040604: 39, 4040605: 79,
        104040201: 31, 104040202: 14, 104040203: 18, 104040204: 16,
    }

    def get_or_create_warchess_session(self, uid, chapter_id):
        """获取或创建因果观测指定章节的地图探索会话（用于 cs_49002 -> sc_49003）。"""
        uid = int(uid)
        chapter_id = int(chapter_id)
        self.execute(
            "INSERT INTO warchess_map (uid, activity_id, current_chapter) VALUES (?, 0, ?) "
            "ON CONFLICT(uid, activity_id) DO UPDATE SET current_chapter=excluded.current_chapter",
            (uid, chapter_id)
        )
        spawn = self.WARCHESS_SPAWN_POINTS.get(chapter_id, (0.0, 0.0))
        is_no_fog = chapter_id in (4040601, 4040602, 4040603, 4040604, 4040605)
        init_is_fog = not is_no_fog
        u32_cnt = self.CHAPTER_FOG_U32_COUNT.get(chapter_id, 32)
        init_fog = [10, 10] if is_no_fog else [0xFFFFFFFF] * u32_cnt

        row = self.query("SELECT * FROM warchess_session WHERE uid = ? AND chapter_id = ?", (uid, chapter_id))
        if row:
            r = row[0]
            try:
                fog_list = json.loads(r["fog_json"] or "[]")
            except Exception:
                fog_list = []
            try:
                map_list = json.loads(r["map_json"] or "[]")
            except Exception:
                map_list = []
            try:
                bag_dict = json.loads(r["bag_json"] or "{}")
            except Exception:
                bag_dict = {"item": [], "artifact": []}
            try:
                hp_list = json.loads(r["hp_json"] or "[]")
            except Exception:
                hp_list = []
            try:
                logs_list = json.loads(r["logs_json"] or "[]")
            except Exception:
                logs_list = []
            try:
                events_list = json.loads(r["events_json"] or "[]")
            except Exception:
                events_list = []

            if not fog_list:
                fog_list = init_fog
            elif init_is_fog and len(fog_list) != u32_cnt:
                fog_list = [0xFFFFFFFF] * u32_cnt

            pos_x = float(r["pos_x"])
            pos_z = float(r["pos_z"])
            if pos_x == 0.0 and pos_z == 0.0:
                pos_x, pos_z = spawn

            return {
                "pos": {"x": pos_x, "z": pos_z},
                "direction": int(r["direction"]),
                "is_fog": bool(r["is_fog"]),
                "fog": fog_list,
                "map": map_list,
                "bag": bag_dict,
                "hp_list": hp_list,
                "log": logs_list,
                "event_list": events_list,
                "is_viewed_log": False,
                "guide_pos": [],
                "event_info": self.get_warchess_event_info(uid, chapter_id),
            }

        # 预载入已开启宝箱的地块状态为已开启 (state=1)
        prev_boxes = self.query(
            "SELECT box_id, tag, pos_x, pos_z FROM warchess_box_record WHERE uid=? AND chapter_id=?",
            (uid, chapter_id)
        )
        init_map = []
        for pb in prev_boxes:
            init_map.append({
                "tag": int(pb["tag"]),
                "pos": {"x": float(pb["pos_x"]), "z": float(pb["pos_z"])},
                "state": 1,
                "attribute": [int(pb["box_id"])],
                "rotation": 0
            })

        init_session = {
            "pos": {"x": float(spawn[0]), "z": float(spawn[1])},
            "direction": 0,
            "is_fog": init_is_fog,
            "fog": init_fog,
            "map": init_map,
            "bag": {"item": [], "artifact": []},
            "hp_list": [],
            "log": [],
            "event_list": [],
            "is_viewed_log": False,
            "guide_pos": [],
            "event_info": self.get_warchess_event_info(uid, chapter_id),
        }
        self.execute(
            "INSERT INTO warchess_session (uid, chapter_id, pos_x, pos_z, direction, is_fog, fog_json, map_json, bag_json, hp_json, logs_json, events_json, update_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                chapter_id,
                float(spawn[0]),
                float(spawn[1]),
                0,
                1 if init_is_fog else 0,
                json.dumps(init_fog),
                json.dumps(init_map),
                json.dumps({"item": [], "artifact": []}),
                "[]",
                "[]",
                "[]",
                int(time.time()),
            )
        )
        return init_session

    def is_warchess_box_opened(self, uid: int, chapter_id: int, box_id: int, tag: int, x: float, z: float) -> bool:
        """检查指定章节指定坐标的宝箱是否已经开启过（防重复领取与重复出现）。"""
        rows = self.query(
            "SELECT 1 FROM warchess_box_record WHERE uid=? AND chapter_id=? AND box_id=? AND tag=? AND abs(pos_x - ?) < 1e-4 AND abs(pos_z - ?) < 1e-4",
            (int(uid), int(chapter_id), int(box_id), int(tag), float(x), float(z))
        )
        return len(rows) > 0

    def record_warchess_box_opened(self, uid: int, chapter_id: int, box_id: int, tag: int, x: float, z: float):
        """记录因果观测宝箱开启历史并累加计数。"""
        uid, chapter_id, box_id, tag = int(uid), int(chapter_id), int(box_id), int(tag)
        x, z = float(x), float(z)
        self.execute(
            "INSERT OR IGNORE INTO warchess_box_record (uid, chapter_id, box_id, tag, pos_x, pos_z, open_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, chapter_id, box_id, tag, x, z, int(time.time()))
        )
        self.open_warchess_box(uid, chapter_id, box_id, 1)

    def get_warchess_event_info(self, uid: int, chapter_id: int):
        """获取因果观测已执行的事件/已开启的宝箱列表（用于 sc_49003.map_info.event_info 组包）。"""
        rows = self.query(
            "SELECT box_id, tag, pos_x, pos_z FROM warchess_box_record WHERE uid=? AND chapter_id=?",
            (int(uid), int(chapter_id))
        )
        event_map = {}
        for r in rows:
            bid = int(r["box_id"])
            if bid not in event_map:
                event_map[bid] = []
            event_map[bid].append({
                "tag": int(r["tag"]),
                "pos": {"x": float(r["pos_x"]), "z": float(r["pos_z"])}
            })
        
        event_info_list = []
        for eid, plist in event_map.items():
            event_info_list.append({
                "event_id": eid,
                "pos_list": plist
            })
        return event_info_list

    def update_warchess_session_pos(self, uid, chapter_id, x, z):
        """更新玩家在战棋地图中的坐标位置。"""
        uid = int(uid)
        chapter_id = int(chapter_id)
        self.execute(
            "UPDATE warchess_session SET pos_x = ?, pos_z = ?, update_ts = ? WHERE uid = ? AND chapter_id = ?",
            (float(x), float(z), int(time.time()), uid, chapter_id)
        )

    def open_warchess_box(self, uid, chapter_id, box_id=10203, num=1):
        """记录因果观测宝箱开启并累加计数。"""
        uid = int(uid)
        chapter_id = int(chapter_id)
        box_id = int(box_id)
        num = int(num)
        self.execute(
            "INSERT INTO warchess_box (uid, chapter_id, box_id, num) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uid, chapter_id, box_id) DO UPDATE SET num = num + excluded.num",
            (uid, chapter_id, box_id, num)
        )

    def update_warchess_grid_state(self, uid, chapter_id, tag, x, z, state, attribute=None, rotation=0):
        """更新并持久化战棋地图中某个地块的交互状态（如开启后的宝箱状态）。"""
        uid = int(uid)
        chapter_id = int(chapter_id)
        row = self.query("SELECT map_json FROM warchess_session WHERE uid = ? AND chapter_id = ?", (uid, chapter_id))
        if not row:
            return
        try:
            map_list = json.loads(row[0]["map_json"] or "[]")
        except Exception:
            map_list = []

        found = False
        for item in map_list:
            pos = item.get("pos", {})
            if abs(pos.get("x", 0) - x) < 1e-4 and abs(pos.get("z", 0) - z) < 1e-4:
                item["tag"] = int(tag)
                item["state"] = int(state)
                item["attribute"] = attribute or []
                item["rotation"] = int(rotation)
                found = True
                break
        if not found:
            map_list.append({
                "tag": int(tag),
                "pos": {"x": float(x), "z": float(z)},
                "state": int(state),
                "attribute": attribute or [],
                "rotation": int(rotation)
            })

        self.execute(
            "UPDATE warchess_session SET map_json = ?, update_ts = ? WHERE uid = ? AND chapter_id = ?",
            (json.dumps(map_list, ensure_ascii=False), int(time.time()), uid, chapter_id)
        )

    def clear_warchess_session(self, uid, chapter_id=0):
        """退出/放弃当前进行的战棋章节。"""
        uid = int(uid)
        self.execute(
            "UPDATE warchess_map SET current_chapter = 0 WHERE uid = ? AND activity_id = 0",
            (uid,)
        )

    def add_warchess_artifact(self, uid: int, chapter_id: int, artifact_id: int):
        """记录因果观测获得的圣物/神格"""
        uid, chapter_id, artifact_id = int(uid), int(chapter_id), int(artifact_id)
        row = self.query("SELECT bag_json FROM warchess_session WHERE uid = ? AND chapter_id = ?", (uid, chapter_id))
        if not row:
            return
        try:
            bag = json.loads(row[0]["bag_json"] or '{"item":[],"artifact":[]}')
        except Exception:
            bag = {"item": [], "artifact": []}
        artifacts = bag.get("artifact", [])
        if artifact_id not in artifacts:
            artifacts.append(artifact_id)
        bag["artifact"] = artifacts
        self.execute("UPDATE warchess_session SET bag_json = ?, update_ts = ? WHERE uid = ? AND chapter_id = ?",
                     (json.dumps(bag), int(time.time()), uid, chapter_id))

    def get_timer_watermark(self, uid):
        """获取用户的时间与会话水位线。不存在则自动初始化一条默认记录。"""
        rows = self.query("SELECT * FROM user_timer_watermark WHERE uid=?", (uid,))
        if rows:
            return dict(rows[0])
        now_ts = int(time.time())
        # 优先继承 game_user / boss_challenge_progress 上的已有时间戳
        gu = self.get("game_user", uid) or {}
        init_daily = int(gu.get("last_daily_task_refresh_ts") or 0)
        init_weekly = int(gu.get("last_weekly_task_refresh_ts") or 0)
        bp = self.get("boss_challenge_progress", uid) or {}
        init_thu = int(bp.get("last_weekly_ts") or 0)

        # 若无历史时间戳，则以当前所处周期为起点，避免新记录误触发清零重置
        from lazy_timer import get_daily_5am_ts, get_weekly_mon_5am_ts, get_weekly_thu_5am_ts, get_monthly_5am_ts
        if init_daily <= 0:
            init_daily = get_daily_5am_ts(now_ts)
        if init_weekly <= 0:
            init_weekly = get_weekly_mon_5am_ts(now_ts)
        if init_thu <= 0:
            init_thu = get_weekly_thu_5am_ts(now_ts)
        init_monthly = get_monthly_5am_ts(now_ts)

        self.execute(
            """
            INSERT OR IGNORE INTO user_timer_watermark 
            (uid, last_pulse_ts, last_daily_5am_ts, last_weekly_mon_ts, last_weekly_thu_ts, 
             last_monthly_ts, last_heartbeat_ts, today_online_seconds, today_online_date, is_online, last_logout_ts, update_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', 1, 0, ?)
            """,
            (uid, now_ts, init_daily, init_weekly, init_thu, init_monthly, now_ts, now_ts)
        )
        rows = self.query("SELECT * FROM user_timer_watermark WHERE uid=?", (uid,))
        return dict(rows[0]) if rows else {}

    def update_timer_watermark(self, uid, **fields):
        """原子更新用户时间水位线字段（保证记录存在）。"""
        if not fields:
            return
        self.get_timer_watermark(uid)
        fields["update_ts"] = int(time.time())
        set_clauses = [f"{k}=?" for k in fields.keys()]
        values = list(fields.values()) + [uid]
        sql = f"UPDATE user_timer_watermark SET {', '.join(set_clauses)} WHERE uid=?"
        self.execute(sql, tuple(values))

    def user_exists(self, uid):
        """检查指定 UID 是否存在于 users 表中。"""
        try:
            cur = self.execute("SELECT 1 FROM users WHERE uid = ? LIMIT 1", (int(uid),))
            return bool(cur.fetchone())
        except Exception:
            return False

    def update_user_basic(self, uid, nick=None, level=None, exp=None):
        """【GM专用】修改玩家基础属性（昵称、等级、经验）。返回更新影响的行数。"""
        uid = int(uid)
        fields = {}
        if nick is not None:
            fields["nick"] = str(nick)
        if level is not None:
            fields["level"] = int(level)
        if exp is not None:
            fields["exp"] = int(exp)
        if not fields:
            return 0
        set_clauses = [f"{k}=?" for k in fields.keys()]
        values = list(fields.values()) + [uid]
        sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE uid=?"
        with self.transaction():
            cur = self.execute(sql, tuple(values))
            return cur.rowcount if cur else 0

    def update_user_peripheral(self, uid, sign=None, avatar=None, icon_frame=None):
        """【GM专用】修改玩家外围个性化装扮（个性签名、头像ID、头像框ID）。返回更新影响的行数。"""
        uid = int(uid)
        fields = {}
        if sign is not None:
            fields["sign"] = str(sign)
        if avatar is not None:
            fields["portrait"] = int(avatar)
        if icon_frame is not None:
            fields["icon_frame"] = int(icon_frame)
        if not fields:
            return 0
        set_clauses = [f"{k}=?" for k in fields.keys()]
        values = list(fields.values()) + [uid]
        sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE uid=?"
        with self.transaction():
            cur = self.execute(sql, tuple(values))
            return cur.rowcount if cur else 0


_db = None


def get_db(path=DEFAULT_DB):
    """全局单例（供服务器进程复用）。"""
    global _db
    if _db is None:
        _db = AccountDB(path)
    return _db


class _Transaction:
    """事务上下文：with db.transaction(): 内 execute 不自动 commit，退出统一 commit/rollback。
    嵌套支持：内层事务并入外层（同连接 in_txn 标记）。"""

    def __init__(self, db):
        self.db = db
        self._outer = False

    def __enter__(self):
        c = self.db._conn()
        if not self.db._in_txn():
            # 顺序要紧：先 BEGIN 成功再置 in_txn。
            # 反过来的话（先置标记再 BEGIN），一旦 BEGIN 抛异常（例如上一条 DML 的
            # commit 失败留下了未提交的隐式事务 → "cannot start a transaction within
            # a transaction"），__exit__ 不会被调用，in_txn 永远停在 True，
            # 该线程之后所有 execute 都不再 commit —— 静默丢数据且不可恢复。
            c.execute("BEGIN")
            self.db._local.in_txn = True
            self._outer = True
        return self

    def __exit__(self, exc_type, exc, tb):
        c = self.db._conn()
        if self._outer:
            try:
                if exc_type is None:
                    c.commit()
                else:
                    c.rollback()
            finally:
                self.db._local.in_txn = False
        return False  # 异常继续向上抛


if __name__ == "__main__":
    # 自检：建库 + 造一条种子用户
    import tempfile
    db = AccountDB(os.path.join(tempfile.gettempdir(), "account_selftest.db"))
    db.set_currency(2149778712, 1, 999999)   # 钻石
    db.set_currency(2149778712, 2, 821198)   # 金币
    db.set_material(2149778712, 41710, 4)
    db.set_sign(2149778712, 3, year=2026, month=8, day=6, sign_list=[5, 4])
    db.upsert("users", 2149778712, {"account": "7545472153", "nick": "椒烧墓捣",
                                    "level": 71, "exp": 123456})
    print("currency:", db.get_currency(2149778712))
    print("material:", db.get_material(2149778712))
    print("sign:", db.get_sign(2149778712))
    print("user:", db.get("users", 2149778712))
    print("自检 OK: 建库/写入/读取 正常")
