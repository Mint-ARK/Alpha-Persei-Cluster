-- =============================================================================
-- db_schema.sql —— 《深空之眼》离线单机服务端权威建库蓝图 (287 表)
-- 来源: 由 tools/export_db_schema.py 从权威生产库一致性提取
-- 特性: 全部采用 IF NOT EXISTS 语法，支持全新空库 0 毫秒构建与幂等自愈
-- =============================================================================

PRAGMA foreign_keys = OFF;

-- -----------------------------------------------------------------------------
-- 1. 业务数据表 (共 287 张)
-- -----------------------------------------------------------------------------

-- 表: abyss
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

-- 表: abyss_layer_stage
CREATE TABLE IF NOT EXISTS abyss_layer_stage (       -- sc_55001 深渊层关卡（行化）
    uid INTEGER NOT NULL,
    layer_id INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3060004
    is_completed INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, layer_id, stage_id)
);

-- 表: abyss_node_time
CREATE TABLE IF NOT EXISTS abyss_node_time (         -- sc_23665 深渊赛季节点→最后活动时间戳
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 101~401 节点 / 1001~1003 赛季条目（abysscfg）
    last_ts INTEGER DEFAULT 0,
    times INTEGER DEFAULT 0,
    flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id)
);

-- 表: abyss_progress
CREATE TABLE IF NOT EXISTS abyss_progress (          -- sc_23565 未知玩法多层进度（100000 系列运行时 ID，raw 全量）
    uid INTEGER PRIMARY KEY,
    raw_json TEXT DEFAULT '{}',                      -- 完整 protobuf 结构
    update_ts INTEGER DEFAULT 0
);

-- 表: abyss_reward
CREATE TABLE IF NOT EXISTS abyss_reward (            -- sc_55001 已领层奖励（行化）
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);

-- 表: accumulate_sign
CREATE TABLE IF NOT EXISTS accumulate_sign (         -- sc_17027 累计登录签到
    uid INTEGER PRIMARY KEY,
    version INTEGER DEFAULT 0,                       -- 2
    open_sign INTEGER DEFAULT 0,
    login_days INTEGER DEFAULT 0,                    -- 104
    update_ts INTEGER DEFAULT 0
);

-- 表: accumulate_sign_award
CREATE TABLE IF NOT EXISTS accumulate_sign_award (   -- sc_17027 已领累计奖励档位（行化）
    uid INTEGER NOT NULL,
    award_id INTEGER NOT NULL,                       -- 9/10（40/80 天档）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, award_id)
);

-- 表: accumulate_sign_bp
CREATE TABLE IF NOT EXISTS accumulate_sign_bp (      -- sc_17033 已购战令档位（行化）
    uid INTEGER NOT NULL,
    bp_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, bp_id)
);

-- 表: accumulate_sign_discount
CREATE TABLE IF NOT EXISTS accumulate_sign_discount (-- sc_17033 累计签到折扣购买
    uid INTEGER PRIMARY KEY,
    version INTEGER DEFAULT 0,
    cumulative_buy_card_num INTEGER DEFAULT 0,       -- 月卡购买数 0
    update_ts INTEGER DEFAULT 0
);

-- 表: achievement
CREATE TABLE IF NOT EXISTS "achievement" (
    uid INTEGER NOT NULL,
    achievement_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    condition INTEGER DEFAULT 0,
    desc TEXT DEFAULT '',
    need INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]',
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    achieve_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, achievement_id)
);

-- 表: achievement_snapshot
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

-- 表: active_draw_pool
CREATE TABLE IF NOT EXISTS active_draw_pool (
                    pool_id INTEGER PRIMARY KEY,
                    sort_order INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    update_ts INTEGER DEFAULT 0
                );

-- 表: activity
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

-- 表: activity_buff_rotation
CREATE TABLE IF NOT EXISTS activity_buff_rotation (  -- sc_22779 队伍肉鸽 buff 轮换（活动 1000）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 1000
    period INTEGER DEFAULT 0,                        -- 轮换期数 5
    rotation_ts INTEGER DEFAULT 0,                   -- 轮换时间 1786914000
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: activity_extra
CREATE TABLE IF NOT EXISTS activity_extra (          -- sc_9481 活动通用标志
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4335001 古远虚影(心魔挑战)
    flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: activity_play_cmd
CREATE TABLE IF NOT EXISTS "activity_play_cmd" (
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    data_json TEXT DEFAULT '{}',
    PRIMARY KEY (uid, activity_id)
);

-- 表: activity_point
CREATE TABLE IF NOT EXISTS activity_point (          -- sc_60201 活动累计积分
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323801 奥西里斯 / 3639701 灰烬
    point INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: activity_point_reward
CREATE TABLE IF NOT EXISTS activity_point_reward (   -- sc_60201 已领积分奖励档位（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,                      -- 32380101~18 / 363970101~110
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, reward_id)
);

-- 表: activity_pt
CREATE TABLE IF NOT EXISTS activity_pt (
    uid INTEGER NOT NULL,
    activity_pt_id INTEGER NOT NULL,
    active_point INTEGER DEFAULT 0,
    get_id_list TEXT DEFAULT '[]',
    PRIMARY KEY (uid, activity_pt_id)
);

-- 表: activity_refresh_time
CREATE TABLE IF NOT EXISTS activity_refresh_time (   -- sc_64031 活动刷新时间
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 9 / 4335001
    refresh_time INTEGER NOT NULL,                   -- 下次刷新时间戳
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, refresh_time)
);

-- 表: activity_reward
CREATE TABLE IF NOT EXISTS activity_reward (         -- sc_60107 常驻积分已领奖励
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);

-- 表: activity_score
CREATE TABLE IF NOT EXISTS activity_score (          -- sc_60105 常驻积分
    uid INTEGER NOT NULL,
    score_key INTEGER NOT NULL,                      -- 常驻积分 key
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, score_key)
);

-- 表: admin_cat_explore_data
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

-- 表: admin_cat_explore_queue
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

-- 表: admin_cat_list
CREATE TABLE IF NOT EXISTS admin_cat_list (
    uid                      INTEGER,
    mimir_id                 INTEGER,
    skill_level              INTEGER DEFAULT 1,    -- 技能等级 (1~10)
    unlock_ts                INTEGER DEFAULT 0,    -- 解锁时间戳
    PRIMARY KEY (uid, mimir_id)
);

-- 表: affix_cfg
CREATE TABLE IF NOT EXISTS affix_cfg (
    affix_id INTEGER PRIMARY KEY,
    name TEXT DEFAULT '',
    max_level INTEGER DEFAULT 1
);

-- 表: ai_characters
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

-- 表: ai_chat_memory
CREATE TABLE IF NOT EXISTS ai_chat_memory (          -- AI 角色私聊对话记忆与上下文
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             INTEGER NOT NULL,                -- 玩家 UID
    char_id         INTEGER NOT NULL,                -- 角色 UID
    role            TEXT NOT NULL,                   -- 'user' / 'assistant'
    content         TEXT NOT NULL,                   -- 对话内容
    timestamp       INTEGER DEFAULT 0,               -- 时间戳
    is_read         INTEGER DEFAULT 1                -- 是否已读
);

-- 表: announcement
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

-- 表: ash_shoot_stage
CREATE TABLE IF NOT EXISTS ash_shoot_stage (         -- sc_2645 灰烬射击关卡最高分（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 404101~404304
    point INTEGER DEFAULT 0,                         -- 历史最高分
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: autochess_card
CREATE TABLE IF NOT EXISTS autochess_card (          -- sc_24515 自走棋卡牌持有（行化）
    uid INTEGER NOT NULL,
    card_id INTEGER NOT NULL,                        -- 10021~14153（135 张）
    num INTEGER DEFAULT 0,                           -- 持有数量
    update_ts INTEGER DEFAULT 0, seq INTEGER DEFAULT 0,
    PRIMARY KEY (uid, card_id)
);

-- 表: autochess_medal
CREATE TABLE IF NOT EXISTS autochess_medal (
    uid INTEGER NOT NULL,
    medal_id INTEGER NOT NULL,
    level INTEGER DEFAULT 1,
    unlock_time INTEGER DEFAULT 0,
    upgrade_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, medal_id)
);

-- 表: autochess_meta
CREATE TABLE IF NOT EXISTS autochess_meta (          -- sc_24515 自走棋卡牌总览
    uid INTEGER PRIMARY KEY,
    rank_score INTEGER DEFAULT 0,                    -- 4800
    energy INTEGER DEFAULT 0,                        -- 28
    sunglasses INTEGER DEFAULT 0,                    -- 1
    update_ts INTEGER DEFAULT 0
);

-- 表: autochess_session
CREATE TABLE IF NOT EXISTS autochess_session (
    uid INTEGER PRIMARY KEY,
    game_type INTEGER DEFAULT 0,
    state INTEGER DEFAULT 0,
    stage_id INTEGER DEFAULT 0,
    round INTEGER DEFAULT 1,
    gold INTEGER DEFAULT 10,
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

-- 表: autochess_stage
CREATE TABLE IF NOT EXISTS autochess_stage (
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    clear_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: backhome
CREATE TABLE IF NOT EXISTS backhome (
    uid INTEGER PRIMARY KEY,
    data_json TEXT DEFAULT '{}',
    update_ts INTEGER DEFAULT 0
);

-- 表: backhome_canteen_career
CREATE TABLE IF NOT EXISTS backhome_canteen_career ( -- sc_58001 食堂工作安排（行化）
    uid INTEGER NOT NULL,
    ctype INTEGER NOT NULL,                          -- 1~3
    hero_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, ctype)
);

-- 表: backhome_canteen_dish
CREATE TABLE IF NOT EXISTS backhome_canteen_dish (   -- sc_58001 食堂招牌菜（行化）
    uid INTEGER NOT NULL,
    food_id INTEGER NOT NULL,                        -- 106/121
    sell_num INTEGER DEFAULT 0,
    sold_num INTEGER DEFAULT 0,
    sell_earnings INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, food_id)
);

-- 表: backhome_canteen_entrust
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

-- 表: backhome_canteen_furniture
CREATE TABLE IF NOT EXISTS backhome_canteen_furniture (  -- 食堂家具实例（58116 升级）
    uid INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,                      -- 实例 uid（BackHomeCanteenFurnitureIDCfg.entity_id）
    type_id INTEGER DEFAULT 0,                       -- 941001 炒锅等（idcfg.type_id）
    level INTEGER DEFAULT 0,                         -- 升级等级（0/1/2，cost_material 分级）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, entity_id)
);

-- 表: backhome_canteen_meta
CREATE TABLE IF NOT EXISTS backhome_canteen_meta (   -- sc_58001 食堂总览
    uid INTEGER NOT NULL,
    canteen_id INTEGER NOT NULL,                     -- 4
    accruing_earnings INTEGER DEFAULT 0,             -- 1847982
    update_ts INTEGER DEFAULT 0, "mode" INTEGER DEFAULT 0, "pending_earnings" INTEGER DEFAULT 0, "last_receive_earnings_time" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, canteen_id)
);

-- 表: backhome_canteen_task
CREATE TABLE IF NOT EXISTS backhome_canteen_task (
    task_id INTEGER PRIMARY KEY,
    name TEXT DEFAULT '',
    task_level INTEGER DEFAULT 1,
    cost INTEGER DEFAULT 0,
    need_min INTEGER DEFAULT 1,
    need_max INTEGER DEFAULT 3,
    base_success INTEGER DEFAULT 0,
    tag_success INTEGER DEFAULT 5,
    tag_list TEXT DEFAULT '[]',
    tag_max TEXT DEFAULT '[]',
    reward_list TEXT DEFAULT '[]',
    time TEXT DEFAULT '{}'
);

-- 表: backhome_dorm
CREATE TABLE IF NOT EXISTS backhome_dorm (           -- sc_58001 宿舍（行化）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,                        -- 5~15
    pos_id INTEGER DEFAULT 0,
    exp INTEGER DEFAULT 0,
    liked_num INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "be_visited_num" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id)
);

-- 表: backhome_dorm_gift
CREATE TABLE IF NOT EXISTS backhome_dorm_gift (
                uid INTEGER NOT NULL,
                dorm_id INTEGER NOT NULL,
                hero_id INTEGER NOT NULL,
                furniture_id INTEGER NOT NULL,
                num INTEGER DEFAULT 1,
                update_ts INTEGER DEFAULT 0,
                PRIMARY KEY (uid, dorm_id, furniture_id)
            );

-- 表: backhome_dorm_hero
CREATE TABLE IF NOT EXISTS backhome_dorm_hero (      -- sc_58001 宿舍驻守英雄（行化）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id, hero_id)
);

-- 表: backhome_dorm_layout
CREATE TABLE IF NOT EXISTS backhome_dorm_layout (   -- sc_58003 宿舍家具布局（58010 保存）
    uid INTEGER NOT NULL,
    dorm_id INTEGER NOT NULL,
    layout_json TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, dorm_id)
);

-- 表: backhome_dorm_template
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

-- 表: backhome_furniture
CREATE TABLE IF NOT EXISTS backhome_furniture (      -- sc_58001 家具持有（行化）
    uid INTEGER NOT NULL,
    furniture_id INTEGER NOT NULL,                   -- 95xxx/96xxx
    num INTEGER DEFAULT 0,
    give_num INTEGER DEFAULT 0,                      -- 赠送数
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, furniture_id)
);

-- 表: backhome_hero
CREATE TABLE IF NOT EXISTS backhome_hero (           -- sc_58001 回家英雄（行化）
    uid INTEGER NOT NULL,
    archives_id INTEGER NOT NULL,                    -- 1015 等
    hero_id INTEGER NOT NULL,
    fatigue INTEGER DEFAULT 0,
    feed_times INTEGER DEFAULT 0,
    total_feed_times INTEGER DEFAULT 0,
    is_lock INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "skin_id" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, archives_id)
);

-- 表: backhome_ingredient
CREATE TABLE IF NOT EXISTS backhome_ingredient (     -- sc_58001 食材库存（行化）
    uid INTEGER NOT NULL,
    item_id INTEGER NOT NULL,                        -- 51xxx 食材族
    num INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, item_id)
);

-- 表: backhome_meta
CREATE TABLE IF NOT EXISTS backhome_meta (           -- sc_58001 家园总览
    uid INTEGER PRIMARY KEY,
    last_fatigue_update_time INTEGER DEFAULT 0,      -- 1786411476
    exhibition_id INTEGER DEFAULT 0,                 -- 5
    share_is_open INTEGER DEFAULT 0,
    received_be_visited_gift_num INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: backhome_suit
CREATE TABLE IF NOT EXISTS backhome_suit (           -- sc_58001 已解锁套件（行化）
    uid INTEGER NOT NULL,
    suit_id INTEGER NOT NULL,                        -- 3011001 等 21 套
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, suit_id)
);

-- 表: battle_equip
CREATE TABLE IF NOT EXISTS battle_equip (
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    drop_lib_id INTEGER DEFAULT 0,
    insure_times INTEGER DEFAULT 0,
    next_refresh_time INTEGER DEFAULT 0, "suit_id" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: battlepass
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

-- 表: big_month_card
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

-- 表: boss_challenge_advance
CREATE TABLE IF NOT EXISTS boss_challenge_advance (  -- 进阶梦境首领得分与阵容
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,                -- 进阶首领 Pool ID (如 20001)
    score           INTEGER DEFAULT 0,               -- 最高得分
    used_heroes     TEXT DEFAULT '[]',               -- 锁定出战角色 [id, id, id]
    last_heroes_cfg TEXT DEFAULT '[]',               -- 预设出战阵容
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);

-- 表: boss_challenge_affixes
CREATE TABLE IF NOT EXISTS boss_challenge_affixes (  -- 梦境再构自选词缀配置
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,
    affix_index_list TEXT DEFAULT '[]',
    time_index_list TEXT DEFAULT '[]',
    diffculty_index INTEGER DEFAULT 1,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);

-- 表: boss_challenge_claimed_rewards
CREATE TABLE IF NOT EXISTS boss_challenge_claimed_rewards ( -- 梦境再构已领奖励
    uid             INTEGER NOT NULL,
    reward_type     INTEGER NOT NULL,                -- 1=星级奖励, 2=积分奖励
    reward_id       INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_type, reward_id)
);

-- 表: boss_challenge_exchange
CREATE TABLE IF NOT EXISTS boss_challenge_exchange ( -- 梦境再构兑换次数，与周四重置使用同一表
    uid             INTEGER NOT NULL,
    goods_id        INTEGER NOT NULL,
    exchange_times  INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, goods_id)
);

-- 表: boss_challenge_normal
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

-- 表: boss_challenge_progress
CREATE TABLE IF NOT EXISTS boss_challenge_progress ( -- sc_45201 Boss 挑战入口
    uid INTEGER PRIMARY KEY,
    mode INTEGER DEFAULT 0,                          -- 0=未选择
    next_refresh_time INTEGER DEFAULT 0,
    difficulty_list TEXT DEFAULT '[]',               -- [3,4,101,102] 开放难度
    update_ts INTEGER DEFAULT 0
, "cycle_id" INTEGER DEFAULT 0, "area_id" INTEGER DEFAULT 4, "use_times" INTEGER DEFAULT 0, "last_daily_ts" INTEGER DEFAULT 0, "last_weekly_ts" INTEGER DEFAULT 0, "advance_id" INTEGER DEFAULT 101);

-- 表: boss_challenge_scores
CREATE TABLE IF NOT EXISTS boss_challenge_scores (   -- 梦境再构首领挑战得分
    uid             INTEGER NOT NULL,
    boss_id         INTEGER NOT NULL,
    score           INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, boss_id)
);

-- 表: chapter
CREATE TABLE IF NOT EXISTS chapter (
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    star_list TEXT DEFAULT '[]',
    clear_times INTEGER DEFAULT 0,
    type INTEGER DEFAULT 1,
    chapter_id INTEGER, name TEXT DEFAULT '', category TEXT DEFAULT '', category_id INTEGER DEFAULT 1, difficulty INTEGER DEFAULT 1, max_star INTEGER DEFAULT 3, stamina_need INTEGER DEFAULT 0, first_reward TEXT DEFAULT '[]', reward TEXT DEFAULT '[]', team_list TEXT DEFAULT '[]', first_clear_ts INTEGER, update_ts INTEGER, chapter_name TEXT DEFAULT '',
    PRIMARY KEY (uid, id)
);

-- 表: chapter19_state
CREATE TABLE IF NOT EXISTS chapter19_state (         -- sc_24029 第19章扫描/线索/档案/日期操作
    uid INTEGER PRIMARY KEY,
    state_json TEXT NOT NULL,
    update_ts INTEGER NOT NULL
);

-- 表: chapter_reward
CREATE TABLE IF NOT EXISTS chapter_reward (
    uid INTEGER NOT NULL, chapter_id INTEGER NOT NULL, name TEXT DEFAULT '',
    difficulty INTEGER DEFAULT 1, star_need TEXT DEFAULT '[]',
    reward_first TEXT DEFAULT '[]', reward_second TEXT DEFAULT '[]', reward_third TEXT DEFAULT '[]',
    done1 INTEGER DEFAULT 0, done2 INTEGER DEFAULT 0, done3 INTEGER DEFAULT 0,
    update_ts INTEGER, PRIMARY KEY (uid, chapter_id));

-- 表: chapter_star_reward
CREATE TABLE IF NOT EXISTS chapter_star_reward (
    uid INT NOT NULL,
    chapter_id INT NOT NULL,
    reward_order INT NOT NULL,
    receive_time INT DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, reward_order)
);

-- 表: chapter_state
CREATE TABLE IF NOT EXISTS chapter_state (           -- sc_23475 主线章节状态
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,                     -- 10100~10800
    state INTEGER DEFAULT 0,                         -- 2=已完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id)
);

-- 表: chapter_v2_backtrack
CREATE TABLE IF NOT EXISTS chapter_v2_backtrack (    -- 当前回溯游标与临时位置；永久进度单独保留
    uid INTEGER PRIMARY KEY,
    map_id INTEGER NOT NULL,
    start_event INTEGER NOT NULL,
    now_location INTEGER NOT NULL,
    token TEXT NOT NULL,
    update_ts INTEGER NOT NULL DEFAULT 0
);

-- 表: chapter_v2_map_node
CREATE TABLE IF NOT EXISTS chapter_v2_map_node (     -- sc_24065 章节V2地图事件节点（行化）
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 帧小 ID（101~313）
    ev_type INTEGER DEFAULT 0,                       -- 1主线40801xx/2支线40802xx/3补充40803xx/0运行时
    list_type TEXT DEFAULT '',                       -- 来源组 f1f4/f1f7/f3/f4/f5
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id, list_type)
);

-- 表: chapter_v2_map_state
CREATE TABLE IF NOT EXISTS chapter_v2_map_state (    -- sc_24061 当前地图位置与补充资料阅读
    uid INTEGER PRIMARY KEY,
    now_location INTEGER NOT NULL DEFAULT 0,
    read_events_json TEXT NOT NULL DEFAULT '[]',
    update_ts INTEGER NOT NULL DEFAULT 0
);

-- 表: chat_sticker
CREATE TABLE IF NOT EXISTS chat_sticker (
    uid INTEGER NOT NULL,
    emoji_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    desc TEXT DEFAULT '',
    PRIMARY KEY (uid, emoji_id)
);

-- 表: chip
CREATE TABLE IF NOT EXISTS chip (
    uid INTEGER NOT NULL,
    cat TEXT NOT NULL,
    id INTEGER NOT NULL,
    unlocked INTEGER DEFAULT 0,
    name TEXT DEFAULT '',
    desc TEXT DEFAULT '',
    type_id INTEGER DEFAULT 0,
    role_type_id INTEGER DEFAULT 0,
    spec_char INTEGER DEFAULT 0,
    picture_id TEXT DEFAULT '',
    portrait TEXT DEFAULT '',
    story TEXT DEFAULT '',
    profile TEXT DEFAULT '',
    new_condition INTEGER DEFAULT 0,
    cost_condition TEXT DEFAULT '',
    module_condition TEXT DEFAULT '',
    cooldown_first INTEGER DEFAULT 0,
    cooldown_com INTEGER DEFAULT 0,
    sort INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, remark TEXT DEFAULT '',
    PRIMARY KEY (uid, id)
);

-- 表: chip_proposal
CREATE TABLE IF NOT EXISTS chip_proposal (           -- sc_50001 管理喵芯片方案
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    secondary TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);

-- 表: claim_ledger
CREATE TABLE IF NOT EXISTS claim_ledger (            -- 通用领取台账（幂等/防重复发奖）
    uid       INTEGER NOT NULL,
    kind      TEXT NOT NULL,        -- 领取类别：'relation_story' / 'activity_tier' / ...
    key_id    INTEGER NOT NULL,     -- 该类别下的条目 id（故事 id / 档位 id ...）
    claim_ts  INTEGER DEFAULT 0,
    PRIMARY KEY (uid, kind, key_id)
);

-- 表: club
CREATE TABLE IF NOT EXISTS club (
    uid INTEGER NOT NULL,
    club_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    icon INTEGER DEFAULT 0,
    level INTEGER DEFAULT 0,
    notice TEXT DEFAULT '',
    leader_player_id INTEGER DEFAULT 0,
    leader_nick TEXT DEFAULT '',
    leader_icon INTEGER DEFAULT 0,
    leader_icon_frame INTEGER DEFAULT 0,
    leader_level INTEGER DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    member_limit INTEGER DEFAULT 0,
    club_exp INTEGER DEFAULT 0,
    impeach_ts INTEGER DEFAULT 0,
    last_week_total_vitality INTEGER DEFAULT 0,
    weekly_gain_exp INTEGER DEFAULT 0,
    weekly_gain_coin INTEGER DEFAULT 0,
    post INTEGER DEFAULT 0,
    is_candidate INTEGER DEFAULT 0,
    examine_switch INTEGER DEFAULT 0,
    join_request_limit INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "leader_online_ts" INTEGER DEFAULT 0, "leader_vitality" INTEGER DEFAULT 0, "leader_total_vitality" INTEGER DEFAULT 0, "assist_hero_ids" TEXT DEFAULT '', "last_share_ts" INTEGER DEFAULT 0, "welfare_state" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, club_id)
);

-- 表: combo_skill_counter
CREATE TABLE IF NOT EXISTS combo_skill_counter (    -- 连携技能出场计量表（cooperation_skill_server 管理）
    uid                  INTEGER NOT NULL,
    combo_id             INTEGER NOT NULL,          -- ComboSkillCfg.id (1~85)
    total_times          INTEGER NOT NULL DEFAULT 0,-- 总触发次数（战斗/扫荡胜利）
    normal_times         INTEGER NOT NULL DEFAULT 0,-- 普通关卡触发次数
    hard_times           INTEGER NOT NULL DEFAULT 0,-- 特定高难关卡触发次数（梦境魇渊Boss/黑区高难）
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, combo_id)
);

-- 表: combo_skill_state
CREATE TABLE IF NOT EXISTS combo_skill_state (      -- 连携技能（协作技能网 p73）等级与进度状态
    uid                  INTEGER NOT NULL,
    skill_id             INTEGER NOT NULL,          -- ComboSkillCfg.id (1~85)，cs_73018 上报的 cooperate_unique_skill_id
    level                INTEGER NOT NULL DEFAULT 1, -- 当前等级 1~3（maxLevel）
    progress_json        TEXT NOT NULL DEFAULT '{}', -- {condition_id: times} 升级进度计数器
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, skill_id)
);

-- 表: coop_room
CREATE TABLE IF NOT EXISTS coop_room (
    uid INTEGER PRIMARY KEY,
    room_info_json TEXT DEFAULT '{}',
    update_ts INTEGER DEFAULT 0
);

-- 表: core_verification_affix
CREATE TABLE IF NOT EXISTS core_verification_affix ( -- sc_23477 词缀池状态（8 行）
    uid INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,                       -- 740010 技能伤害强化 lv10 等
    level INTEGER DEFAULT 0,                         -- 0=未选
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, affix_id)
);

-- 表: core_verification_affix_catalog
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

-- 表: core_verification_affixes
CREATE TABLE IF NOT EXISTS core_verification_affixes ( -- 迭代校验自选词缀
    uid             INTEGER NOT NULL,
    mode_id         INTEGER NOT NULL,
    affix_list      TEXT DEFAULT '[]',
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, mode_id)
);

-- 表: core_verification_badge
CREATE TABLE IF NOT EXISTS core_verification_badge ( -- sc_23485 核心验证挑战图鉴徽章（行化）
    uid INTEGER NOT NULL,
    badge_id INTEGER NOT NULL,                       -- 40605/40705/408xx/409xx/410xx/501xx/502xx
    unlock_ts INTEGER DEFAULT 0,                     -- 解锁时间戳
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, badge_id)
);

-- 表: core_verification_cl_badge_unlocked
CREATE TABLE IF NOT EXISTS core_verification_cl_badge_unlocked ( -- 挑战模式图鉴徽章
    uid             INTEGER NOT NULL,
    badge_id        INTEGER NOT NULL,
    unlock_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, badge_id)
);

-- 表: core_verification_cl_progress
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

-- 表: core_verification_cl_task_claimed
CREATE TABLE IF NOT EXISTS core_verification_cl_task_claimed ( -- 挑战模式已领任务
    uid             INTEGER NOT NULL,
    activity_id     INTEGER NOT NULL,
    task_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, task_id)
);

-- 表: core_verification_claimed_tasks
CREATE TABLE IF NOT EXISTS core_verification_claimed_tasks ( -- 迭代校验已领任务奖励
    uid             INTEGER NOT NULL,
    task_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0, "cycle" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);

-- 表: core_verification_hero_lock
CREATE TABLE IF NOT EXISTS core_verification_hero_lock ( -- 迭代校验出战锁定
    uid             INTEGER NOT NULL,
    cycle           INTEGER NOT NULL,
    boss_type       INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, cycle, boss_type, hero_id)
);

-- 表: core_verification_stage
CREATE TABLE IF NOT EXISTS core_verification_stage ( -- sc_23477 核心验证挑战关卡成绩
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3093101 主 / 3092101~2 子
    kind TEXT DEFAULT 'main',                        -- main/sub
    max_score INTEGER DEFAULT 0,                     -- 最高分
    last_score INTEGER DEFAULT 0,                    -- 最近分
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: core_verification_stage_affix
CREATE TABLE IF NOT EXISTS core_verification_stage_affix ( -- sc_23477 主关已选增益词缀（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, affix_id)
);

-- 表: core_verification_stage_record
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

-- 表: core_verification_stage_score
CREATE TABLE IF NOT EXISTS core_verification_stage_score ( -- sc_23477 关卡阶段小计分（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    score_idx INTEGER NOT NULL,                      -- 1~3
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, score_idx)
);

-- 表: core_verification_super_score
CREATE TABLE IF NOT EXISTS core_verification_super_score ( -- 极值挑战单关积分
    uid             INTEGER NOT NULL,
    stage_id        INTEGER NOT NULL,
    score           INTEGER DEFAULT 0,
    update_ts       INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: core_verification_task
CREATE TABLE IF NOT EXISTS core_verification_task (  -- sc_23477 挑战任务/徽章（行化）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 50201~50205
    state INTEGER DEFAULT 0,                         -- 2=完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);

-- 表: currency
CREATE TABLE IF NOT EXISTS currency (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    num         INTEGER DEFAULT 0, currency_name TEXT DEFAULT '', valid_time INTEGER DEFAULT 0, valid_start INTEGER, valid_time_D TEXT DEFAULT '', "last_fatigue_recover_time" INTEGER DEFAULT 0, source_type TEXT DEFAULT '', "update_ts" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);

-- 表: cvm4_meta
CREATE TABLE IF NOT EXISTS cvm4_meta (               -- sc_24265 Mode4 总览
    uid INTEGER PRIMARY KEY,
    first_enter INTEGER DEFAULT 0,
    max_point INTEGER DEFAULT 0,                     -- 19570
    update_ts INTEGER DEFAULT 0
);

-- 表: cvm4_stage
CREATE TABLE IF NOT EXISTS cvm4_stage (              -- sc_24265 核心验证 Mode4 关卡（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 3092091~3092093/3093091
    stage_index INTEGER DEFAULT 0,
    cur_value INTEGER DEFAULT 0,                     -- 队伍得分
    seconds INTEGER DEFAULT 0,                       -- 通关耗时
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: cvm4_stage_hero
CREATE TABLE IF NOT EXISTS cvm4_stage_hero (         -- sc_24265 Mode4 上阵队伍（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    hero_idx INTEGER NOT NULL,                       -- 1~3
    hero_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id, hero_idx)
);

-- 表: cvm4_task
CREATE TABLE IF NOT EXISTS cvm4_task (               -- sc_24265 Mode4 徽章任务（行化）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 50110~50114
    state INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);

-- 表: daily_fatigue
CREATE TABLE IF NOT EXISTS daily_fatigue (          -- sc_12045 每日体力甜点（午晚领蛋）
    uid INTEGER NOT NULL,
    type INTEGER NOT NULL,                           -- 11=午间, 18=晚间
    date_str TEXT NOT NULL,                          -- YYYY-MM-DD
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, type, date_str)
);

-- 表: dance_diy
CREATE TABLE IF NOT EXISTS dance_diy (               -- sc_58195 练舞房 DIY 舞蹈序列
    uid INTEGER NOT NULL,
    pos INTEGER NOT NULL,                            -- 槽位 1..N
    scene_id INTEGER DEFAULT 0,
    music_id INTEGER DEFAULT 0,
    action_list TEXT DEFAULT '',                     -- 逗号分隔动作 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pos)
);

-- 表: dorm_information
CREATE TABLE IF NOT EXISTS dorm_information (        -- sc_58063 宿舍资讯（行化）
    uid INTEGER NOT NULL,
    info_id INTEGER NOT NULL,                        -- 1001/2001
    param_idx INTEGER NOT NULL,                      -- 1~7
    param INTEGER DEFAULT 0,                         -- -1 占位
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, info_id, param_idx)
);

-- 表: draw_group_state
CREATE TABLE IF NOT EXISTS draw_group_state (
    uid INTEGER NOT NULL,
    pool_group TEXT NOT NULL,
    since_ssr INTEGER DEFAULT 0,
    total_draws INTEGER DEFAULT 0,
    up_id INTEGER DEFAULT 0,
    is_up_guaranteed INTEGER DEFAULT 0,
    last_pool_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, since_sr INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pool_group)
);

-- 表: draw_hero_pool
CREATE TABLE IF NOT EXISTS draw_hero_pool (
    hero_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    rare TEXT NOT NULL,
    rare_val INTEGER NOT NULL,
    unlock_star INTEGER NOT NULL,
    is_active INTEGER DEFAULT 1
);

-- 表: draw_item
CREATE TABLE IF NOT EXISTS draw_item (group_id INTEGER, item_id INTEGER, PRIMARY KEY(group_id, item_id));

-- 表: draw_pool
CREATE TABLE IF NOT EXISTS draw_pool (
    pool_id INTEGER PRIMARY KEY,   -- 卡池配置（静态，无 uid）
    name TEXT DEFAULT '',
    pool_type INTEGER DEFAULT 0,   -- 1常驻角色/2武器/3角色UP/5新手/6武器UP/8新手自选/9回归
    ssr_rate REAL DEFAULT 2.0,
    guarantee TEXT DEFAULT '',
    detail_json TEXT DEFAULT '{}'
);

-- 表: draw_record
CREATE TABLE IF NOT EXISTS draw_record (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid INTEGER NOT NULL,
                    pool_id INTEGER NOT NULL,
                    pool_group TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_num INTEGER NOT NULL,
                    draw_ts INTEGER NOT NULL
                );

-- 表: draw_state
CREATE TABLE IF NOT EXISTS draw_state (
    uid INTEGER NOT NULL,
    pool_id INTEGER NOT NULL,
    since_ssr INTEGER DEFAULT 0,   -- 距上次S级抽数（保底计数）
    total_draws INTEGER DEFAULT 0, up_id INTEGER DEFAULT 0, is_up_guaranteed INTEGER DEFAULT 0, pool_group TEXT,
    PRIMARY KEY (uid, pool_id)
);

-- 表: enchant
CREATE TABLE IF NOT EXISTS enchant (
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    difficulty INTEGER DEFAULT 0,
    group_id INTEGER DEFAULT 0,
    monster_level INTEGER DEFAULT 0,
    cost INTEGER DEFAULT 0,
    drop_lib_id INTEGER DEFAULT 0,
    remain_free_refresh INTEGER DEFAULT 2,   -- 剩余免费刷新次数(合入条目)
    daily_refresh_times INTEGER DEFAULT 2,   -- 每日免费刷新上限
    unlock_level INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: equip
CREATE TABLE IF NOT EXISTS equip (
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    prefab_id INTEGER,
    exp INTEGER,
    hero_id INTEGER,
    is_lock INTEGER,
    now_break_level INTEGER,
    enchant_slots TEXT,
    race INTEGER,
    race_preview INTEGER,
    race_hero INTEGER,
    race_faction INTEGER,
    update_ts INTEGER, "is_init" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);

-- 表: equip_cfg
CREATE TABLE IF NOT EXISTS equip_cfg (
    prefab_id INTEGER PRIMARY KEY, starlevel INTEGER DEFAULT 0,
    exp_variant TEXT DEFAULT '[]', max_level TEXT DEFAULT '[]',
    break_cost TEXT DEFAULT '[]', break_times_max INTEGER DEFAULT 0
);

-- 表: equip_exp_curve
CREATE TABLE IF NOT EXISTS equip_exp_curve (
    level INTEGER PRIMARY KEY,
    exp1 INTEGER, exp2 INTEGER, exp3 INTEGER, exp4 INTEGER,
    exp5 INTEGER, exp6 INTEGER, exp7 INTEGER
);

-- 表: equip_material_cfg
CREATE TABLE IF NOT EXISTS equip_material_cfg (
    mat_id INTEGER PRIMARY KEY, item_list TEXT DEFAULT '[]'
);

-- 表: equip_seizure_progress
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
    update_ts INTEGER DEFAULT 0
, last_day_tag INTEGER DEFAULT 0, team_heroes TEXT DEFAULT '[]');

-- 表: equip_skill_cfg
CREATE TABLE IF NOT EXISTS equip_skill_cfg (   -- 装备技能池（equipskillcfg.lua，静态无 uid）
        skill_id INTEGER PRIMARY KEY,
        name TEXT DEFAULT '',
        lvmax INTEGER DEFAULT 0,
        percent INTEGER DEFAULT 0,
        type INTEGER DEFAULT 0,
        attribute INTEGER DEFAULT 0,
        skill_type INTEGER DEFAULT 0,
        desc_spec TEXT DEFAULT ''
    );

-- 表: formation_team
CREATE TABLE IF NOT EXISTS formation_team (         -- cs_63010 自定义编队方案原始消息体
    uid         INTEGER NOT NULL,
    team_type   INTEGER NOT NULL,
    cont_id     INTEGER NOT NULL,
    cont_team   BLOB NOT NULL,
    data_rec    BLOB NOT NULL,
    update_ts   INTEGER DEFAULT 0,
    PRIMARY KEY (uid, team_type, cont_id)
);

-- 表: friend_blacklist
CREATE TABLE IF NOT EXISTS friend_blacklist (        -- sc_19029 黑名单（行化）
    uid INTEGER NOT NULL,
    black_uid INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, black_uid)
);

-- 表: friend_chat
CREATE TABLE IF NOT EXISTS friend_chat (             -- sc_19039 好友聊天离线消息
    uid INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    sender_uid INTEGER DEFAULT 0,
    content TEXT DEFAULT '',
    timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "peer_uid" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, msg_id)
);

-- 表: friend_request
CREATE TABLE IF NOT EXISTS friend_request (          -- sc_19029 好友申请（行化）
    uid INTEGER NOT NULL,
    request_uid INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, request_uid)
);

-- 表: friends
CREATE TABLE IF NOT EXISTS friends (                 -- sc_19029 好友（行化）
    uid INTEGER NOT NULL,
    friend_uid INTEGER NOT NULL,                     -- 2149618442
    timestamp INTEGER DEFAULT 0,                     -- 加好友时间
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, friend_uid)
);

-- 表: fukubukuro
CREATE TABLE IF NOT EXISTS fukubukuro (              -- sc_17021 福袋实例
    uid INTEGER NOT NULL,
    instance_id INTEGER NOT NULL,                    -- 福袋实例 id
    item_id INTEGER DEFAULT 0,                       -- 福袋道具 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, instance_id)
);

-- 表: fukubukuro_cfg
CREATE TABLE IF NOT EXISTS fukubukuro_cfg (          -- 福袋配置（itemcfg sub_type=513 FUKUBUKURO）
    item_id INTEGER PRIMARY KEY,                     -- 30041/30043/30060
    name TEXT DEFAULT '',
    desc TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    total_weight INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: fukubukuro_cfg_select
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

-- 表: fukubukuro_select
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

-- 表: game_level_setting
CREATE TABLE IF NOT EXISTS game_level_setting (
    level INTEGER PRIMARY KEY,
    hero_exp_need INTEGER DEFAULT 0,
    hero_exp_sum INTEGER DEFAULT 0,
    fatigue_upgrade_reward INTEGER DEFAULT 0,
    battlepass_level_exp INTEGER DEFAULT 0
);

-- 表: game_user
CREATE TABLE IF NOT EXISTS game_user (
    uid INTEGER PRIMARY KEY,
    birth_month INTEGER DEFAULT 0,
    birth_day INTEGER DEFAULT 0,
    is_first_draw INTEGER DEFAULT 0,
    is_first_draw_limited INTEGER DEFAULT 0,
    is_first_draw_lucky INTEGER DEFAULT 0,
    total_buy_fatigue_times INTEGER DEFAULT 0,
    bgm_id INTEGER DEFAULT 0,
    dessert_11_got INTEGER DEFAULT 0,
    dessert_18_got INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
, "cur_portrait" INTEGER DEFAULT 0, "cur_icon_frame" INTEGER DEFAULT 0, "cur_home_bg" INTEGER DEFAULT 0, "cur_card_bg" INTEGER DEFAULT 0, "cur_bubble" INTEGER DEFAULT 0, "cur_sticker_bg" INTEGER DEFAULT 0, "show_hero" TEXT DEFAULT '[]', "board_hero" INTEGER DEFAULT 0, "cur_scene" INTEGER DEFAULT 0, "cur_momotalk_frame" INTEGER DEFAULT 1, "last_daily_task_refresh_ts" INTEGER DEFAULT 0, "last_weekly_task_refresh_ts" INTEGER DEFAULT 0, "last_login_task_cycle_ts" INTEGER DEFAULT 0);

-- 表: growth_task_state
CREATE TABLE IF NOT EXISTS growth_task_state (       -- sc_23475 成长阶段任务状态（主任务 4 位 + 子任务 8 位统一）
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,                        -- 40301~40314 主任务 / 4030101~4030308 子任务
    state INTEGER DEFAULT 0,                         -- 2=已完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, task_id)
);

-- 表: guide
CREATE TABLE IF NOT EXISTS guide (
    uid INTEGER NOT NULL,
    guide_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    kind TEXT DEFAULT 'main',   -- main=强引导(12011) / weak=弱引导(12111)
    PRIMARY KEY (uid, guide_id)
);

-- 表: guild_boss
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

-- 表: guild_boss_affix
CREATE TABLE IF NOT EXISTS guild_boss_affix (        -- sc_33001 本期 Buff 词缀（行化）
    club_id INTEGER NOT NULL,
    affix_id INTEGER NOT NULL,                       -- 116/117/195/181/183
    level INTEGER DEFAULT 0,
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (club_id, affix_id)
);

-- 表: guild_boss_prebuff
CREATE TABLE IF NOT EXISTS guild_boss_prebuff (      -- sc_33001 预置 Buff 关卡成绩（行化）
    club_id INTEGER NOT NULL,
    level_id INTEGER NOT NULL,                       -- 1~5
    stage_id INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (club_id, level_id)
);

-- 表: hero
CREATE TABLE IF NOT EXISTS hero (
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    level INTEGER,
    star INTEGER,
    exp INTEGER,
    skill_list TEXT,
    weapon_exp INTEGER,
    weapon_break INTEGER,
    weapon_servant_uid INTEGER,
    servant INTEGER,
    using_astrolabe TEXT,
    unlock_astrolabe TEXT,
    using_skin INTEGER,
    break_level INTEGER,
    exclusive_skill_list TEXT,
    weapon_module_level INTEGER,
    skill_intensify TEXT,
    battle_using_skin INTEGER,
    clear_times INTEGER,
    unlock INTEGER,
    equip_slot TEXT,
    module_level INTEGER,
    module_assignment INTEGER,
    chip_state TEXT,
    update_ts INTEGER, name TEXT, "is_favorite" INTEGER DEFAULT 0, "trust_level" INTEGER DEFAULT 0, "trust_exp" INTEGER DEFAULT 0, "trust_mood" INTEGER DEFAULT 1, "relation_net" TEXT DEFAULT '[]', battle_win_times INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);

-- 表: hero_archive
CREATE TABLE IF NOT EXISTS hero_archive (
    uid         INTEGER NOT NULL,
    archive_id  INTEGER NOT NULL,
    exp         INTEGER DEFAULT 0,
    text_list   TEXT DEFAULT '[]',
    video_list  TEXT DEFAULT '[]',
    gift_list   TEXT DEFAULT '[]',
    selected_picture TEXT DEFAULT '{}',
    super_heart_link_list TEXT DEFAULT '[]',
    hero_story_list TEXT DEFAULT '[]', name TEXT DEFAULT '',
    PRIMARY KEY (uid, archive_id)
);

-- 表: hero_break_cfg
CREATE TABLE IF NOT EXISTS hero_break_cfg (
        id INTEGER PRIMARY KEY,
        race INTEGER DEFAULT 0,
        break_times INTEGER DEFAULT 0,
        user_level INTEGER DEFAULT 0,
        max_level INTEGER DEFAULT 0,
        cost_gold INTEGER DEFAULT 0,
        cost TEXT DEFAULT '[]'
    );

-- 表: hero_cfg
CREATE TABLE IF NOT EXISTS hero_cfg (hero_id INTEGER PRIMARY KEY, unlock_star INTEGER DEFAULT 0, name TEXT DEFAULT "", race INTEGER DEFAULT 0);

-- 表: hero_oath
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

-- 表: hero_oath_plot
CREATE TABLE IF NOT EXISTS hero_oath_plot (          -- 誓约剧情已读（oath_plot.text_list 行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    plot_idx INTEGER NOT NULL,
    text_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, plot_idx)
);

-- 表: hero_oath_time
CREATE TABLE IF NOT EXISTS hero_oath_time (          -- 誓约时间列表（time_list 行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    time_idx INTEGER NOT NULL,
    oath_time INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, time_idx)
);

-- 表: hero_piece
CREATE TABLE IF NOT EXISTS hero_piece (
    uid     INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,
    num     INTEGER DEFAULT 0, name TEXT DEFAULT '',
    PRIMARY KEY (uid, hero_id)
);

-- 表: hero_race_collect
CREATE TABLE IF NOT EXISTS hero_race_collect (       -- sc_52025 英雄种族收集已领取档位
    uid INTEGER NOT NULL,
    race_type INTEGER NOT NULL,                      -- 1奥山/2尼罗/3真樱/4圣树/5众星/9天垣
    received_cnt_list TEXT NOT NULL DEFAULT '[]',    -- JSON 数组，已领档位的收集数量，如 [6,3]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, race_type)
);

-- 表: hero_skill_element_cfg
CREATE TABLE IF NOT EXISTS hero_skill_element_cfg (
        id INTEGER PRIMARY KEY,
        hero_id INTEGER NOT NULL,
        level INTEGER DEFAULT 0,
        cost TEXT DEFAULT '{}',
        attr TEXT DEFAULT '{}'
    );

-- 表: hero_trial
CREATE TABLE IF NOT EXISTS hero_trial (              -- sc_11035 英雄试炼/角色试用活动
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4310701
    activity_info_json TEXT DEFAULT '[]',            -- [{id, challenge_state}]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: history_cmd
CREATE TABLE IF NOT EXISTS history_cmd (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- history/recharge/first_charge
    key_id TEXT NOT NULL,
    data TEXT DEFAULT '',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, kind, key_id)
);

-- 表: hodur_progress
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

-- 表: idol_dance_collection
CREATE TABLE IF NOT EXISTS idol_dance_collection (   -- sc_58203 舞蹈收集观看记录
    uid INTEGER NOT NULL,
    collect_type INTEGER NOT NULL,                   -- 1/2/3
    viewed_id_list TEXT DEFAULT '',                  -- 逗号分隔已观看 id
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, collect_type)
);

-- 表: idol_pvp_stage
CREATE TABLE IF NOT EXISTS idol_pvp_stage (          -- sc_58173 偶像练习生 PVP 舞台
    uid INTEGER PRIMARY KEY,
    stage_id INTEGER DEFAULT 0,                      -- 10013
    refresh_timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: idol_trainee
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

-- 表: idol_trainee_hero
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

-- 表: idol_trainee_pos
CREATE TABLE IF NOT EXISTS idol_trainee_pos (        -- sc_58169 舞台站位（行化）
    uid INTEGER NOT NULL,
    pos INTEGER NOT NULL,                            -- 1~5
    hero_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pos)
);

-- 表: idol_trainee_pve
CREATE TABLE IF NOT EXISTS idol_trainee_pve (        -- sc_58189 练舞 PVE 章节进度（行化）
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,                     -- 1 挑战
    stage_id INTEGER NOT NULL,                       -- 101~103
    score INTEGER DEFAULT 0,
    is_clear INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, stage_id)
);

-- 表: idol_trainee_rank
CREATE TABLE IF NOT EXISTS idol_trainee_rank (       -- sc_58191 练舞排行
    uid INTEGER PRIMARY KEY,
    weekly_point INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: idol_trainee_rank_got
CREATE TABLE IF NOT EXISTS idol_trainee_rank_got (   -- sc_58191 已领排行档位（行化）
    uid INTEGER NOT NULL,
    rank_id INTEGER NOT NULL,                        -- 1~4
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, rank_id)
);

-- 表: illustrated
CREATE TABLE IF NOT EXISTS illustrated (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    times INTEGER DEFAULT 0,       -- enemy 击杀次数
    is_view INTEGER DEFAULT 0,     -- 已查看
    is_receive INTEGER DEFAULT 0,  -- 插画已领
    progress INTEGER DEFAULT 0,    -- intelligence 进度
    complete_flag INTEGER DEFAULT 0,
    pos_list_json TEXT DEFAULT '[]',  -- equip 已收集部位
    PRIMARY KEY (uid, kind, item_id)
);

-- 表: illustrated_cfg
CREATE TABLE IF NOT EXISTS illustrated_cfg (
    kind TEXT NOT NULL,            -- enemy/servant/equip/plot/inbetweening/affix
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    PRIMARY KEY (kind, item_id)
);

-- 表: item_catalog
CREATE TABLE IF NOT EXISTS item_catalog (
    id         INTEGER PRIMARY KEY,
    name       TEXT DEFAULT '',
    type       INTEGER,
    rare       INTEGER,
    category   TEXT DEFAULT 'other'   -- currency/material/keyong/cosmetic/other
);

-- 表: kadas_race_progress
CREATE TABLE IF NOT EXISTS kadas_race_progress (     -- sc_18795 卡达斯假日赛状态
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4343601
    node INTEGER DEFAULT 0,                          -- 当前节点 312
    node2 INTEGER DEFAULT 0,                         -- 配套节点 312
    count INTEGER DEFAULT 0,                         -- 计数 106
    status INTEGER DEFAULT 0,
    ts INTEGER DEFAULT 0,                            -- 时间戳（-1 未记录）
    update_ts INTEGER DEFAULT 0, mission_list TEXT DEFAULT '[]', tank_info TEXT DEFAULT '[]',
    PRIMARY KEY (uid, activity_id)
);

-- 表: letter_special
CREATE TABLE IF NOT EXISTS letter_special (
    uid INTEGER NOT NULL,
    letter_id INTEGER NOT NULL,
    hero_id INTEGER DEFAULT 0,
    is_viewed INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, letter_id)
);

-- 表: loading_set
CREATE TABLE IF NOT EXISTS loading_set (             -- sc_52021 加载图集 id 列表（玩家自定义加载画面）
    uid INTEGER NOT NULL,
    id_list TEXT NOT NULL DEFAULT '[]',              -- JSON 数组，如 [2109401,2109403,2109402]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);

-- 表: login_push
CREATE TABLE IF NOT EXISTS login_push (
    seq INTEGER PRIMARY KEY,
    cmd INTEGER NOT NULL,
    header BLOB NOT NULL,              -- 原素材 11B 帧头整体（size/flag域/cmd/idx/srv 原样）
    payload BLOB NOT NULL,
    dynamic INTEGER DEFAULT 0,
    note TEXT DEFAULT ''
, payload_json TEXT);

-- 表: mail
CREATE TABLE IF NOT EXISTS mail (
    uid INTEGER NOT NULL,
    mail_id INTEGER NOT NULL,
    mail_type INTEGER DEFAULT 0,
    template_id INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    sender TEXT DEFAULT '',
    content_json TEXT DEFAULT '',
    send_time INTEGER DEFAULT 0,
    expire_time INTEGER DEFAULT 0,
    read_flag INTEGER DEFAULT 0,
    attach_flag INTEGER DEFAULT 0,
    star_state INTEGER DEFAULT 0,
    attachment_json TEXT DEFAULT '',
    collect_time INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, mail_id)
);

-- 表: material
CREATE TABLE IF NOT EXISTS material (
    uid         INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    num         INTEGER DEFAULT 0, name TEXT DEFAULT '', "update_ts" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, id)
);

-- 表: matrix_hero_attr
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

-- 表: matrix_hero_standard
CREATE TABLE IF NOT EXISTS matrix_hero_standard (
    standard_id INTEGER PRIMARY KEY,  -- 3031xxx 可解锁标准角色（每周四解锁池）
    hero_id INTEGER DEFAULT 0,
    hero_name TEXT DEFAULT '',
    template_id INTEGER DEFAULT 1
);

-- 表: matrix_point_reward
CREATE TABLE IF NOT EXISTS matrix_point_reward (
    rank INTEGER PRIMARY KEY,
    point_need INTEGER DEFAULT 0,
    need_level INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]'
);

-- 表: matrix_system
CREATE TABLE IF NOT EXISTS matrix_system (
    uid INTEGER NOT NULL,
    ready_hero_ids TEXT DEFAULT '[]',    -- 本期 5 个 standard_id
    difficulty_json TEXT DEFAULT '[]',   -- 3 档难度 {difficulty, regular_affix, ai_chip, custom_affix, price}
    next_refresh_ts INTEGER DEFAULT 0, ready_hero_skins TEXT DEFAULT '[]', ready_hero_json TEXT DEFAULT '[]',
    PRIMARY KEY (uid)
);

-- 表: matrix_user
CREATE TABLE IF NOT EXISTS matrix_user (
    uid INTEGER PRIMARY KEY,
    game_state INTEGER DEFAULT 1,
    terminal_json TEXT DEFAULT '{}',
    beacon_json TEXT DEFAULT '[]',
    point_reward_got TEXT DEFAULT '[]',  -- 已领档位 rank 列表
    update_ts INTEGER DEFAULT 0
, current_progress_json TEXT DEFAULT '{}');

-- 表: momotalk
CREATE TABLE IF NOT EXISTS momotalk (
    uid INTEGER NOT NULL,
    momotalk_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    read_flag INTEGER DEFAULT 1,
    activity_id INTEGER DEFAULT 3639701,
    PRIMARY KEY (uid, momotalk_id)
);

-- 表: momotalk_content
CREATE TABLE IF NOT EXISTS momotalk_content (        -- ChatContentCfg 对话文本（静态全量）
    content_id INTEGER PRIMARY KEY,                  -- 4105 条
    text TEXT DEFAULT ''
);

-- 表: momotalk_hero
CREATE TABLE IF NOT EXISTS momotalk_hero (           -- ChatHeroCfg 聊天对象（静态全量）
    hero_id INTEGER PRIMARY KEY,                     -- 1011 奥努里斯 等 74 个
    name TEXT DEFAULT '',
    icon TEXT DEFAULT ''
);

-- 表: momotalk_message
CREATE TABLE IF NOT EXISTS momotalk_message (        -- ChatMessageCfg 会话消息（静态全量）
    message_id INTEGER PRIMARY KEY,                  -- 101101 等 266 条
    mtype INTEGER DEFAULT 0,                         -- type
    sender INTEGER DEFAULT 0,                        -- 发送对象
    content_id INTEGER DEFAULT 0,                    -- 引用 chatcontentcfg
    trigger_type INTEGER DEFAULT 0,
    trigger_condition INTEGER DEFAULT 0
);

-- 表: momotalk_session
CREATE TABLE IF NOT EXISTS momotalk_session (        -- sc_25465 MomoTalk 聊天对象会话摘要（行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,                        -- 1015 塞赫麦特/1020 梵天/1022 塞勒涅/9002 第九部门
    session_id INTEGER NOT NULL,                     -- 父ID×100+序号（900201 等）
    send_time INTEGER DEFAULT 0,                     -- 最近消息时间
    is_view INTEGER DEFAULT 0,
    content_id INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "save_list" TEXT DEFAULT '[]', "current_content_id" INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id, session_id)
);

-- 表: monster_cosplay_cfg
CREATE TABLE IF NOT EXISTS monster_cosplay_cfg (     -- sc_17865 怪物扮演静态配置（词缀映射+关卡列表，无 uid 全服一份）
    activity_id INTEGER NOT NULL,                    -- 282991
    chapter_id INTEGER NOT NULL,                     -- 6108/6109
    affix_map_json TEXT DEFAULT '[]',                -- [{affix_id, stage_id}] 词缀→技能教学关
    stage_id_list TEXT DEFAULT '[]',                 -- 全部关卡 id
    PRIMARY KEY (activity_id, chapter_id)
);

-- 表: monster_cosplay_progress
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

-- 表: month_card
CREATE TABLE IF NOT EXISTS month_card (
    uid INTEGER PRIMARY KEY,
    monthly_card_num INTEGER DEFAULT 0,
    monthly_card_timestamp INTEGER DEFAULT 0,  -- 到期时间戳
    is_sign INTEGER DEFAULT 0,                 -- 今日已签到
    update_ts INTEGER DEFAULT 0
);

-- 表: music_challenge
CREATE TABLE IF NOT EXISTS music_challenge (         -- sc_61047 音乐挑战谱面成绩
    uid INTEGER NOT NULL,
    challenge_id INTEGER NOT NULL,                   -- 92~166
    score INTEGER DEFAULT 0,                         -- 最高分
    sign INTEGER DEFAULT 0,                          -- 1完成/0未完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, challenge_id)
);

-- 表: mythic_affix
CREATE TABLE IF NOT EXISTS mythic_affix (            -- sc_44007 当期词缀（9 行）
    uid INTEGER NOT NULL,
    affix_kind TEXT NOT NULL,                        -- advantage/inferiority/ultimate
    affix_id INTEGER NOT NULL,                       -- 417 光增伤等
    level INTEGER DEFAULT 0,
    aff_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, affix_kind, affix_id)
);

-- 表: mythic_difficulty
CREATE TABLE IF NOT EXISTS mythic_difficulty (       -- sc_44019 已开放难度（行化）
    uid INTEGER NOT NULL,
    difficulty_id INTEGER NOT NULL,                  -- 1~13 / 1001=难度Ω
    is_open INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty_id)
);

-- 表: mythic_final_cfg
CREATE TABLE IF NOT EXISTS mythic_final_cfg (        -- sc_44021 终焉神话难度→关卡映射（静态配置，无 uid）
    difficulty_id INTEGER PRIMARY KEY,               -- 1~30
    stage_id_list TEXT NOT NULL DEFAULT '[]'         -- JSON 数组（41 关卡 3028001~3028041）
);

-- 表: mythic_final_progress
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

-- 表: mythic_partition
CREATE TABLE IF NOT EXISTS mythic_partition (        -- sc_44007 难度分区（52 行）
    uid INTEGER NOT NULL,
    difficulty INTEGER NOT NULL,                     -- 1~13
    partition_id INTEGER NOT NULL,                   -- 主分区=难度 / 子分区=难度×100+1..3
    stage_id INTEGER NOT NULL,                       -- 3026001~3026013 / 3027001~3027039
    kind TEXT DEFAULT 'main',                        -- main/sub
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty, partition_id)
);

-- 表: mythic_progress_partition
CREATE TABLE IF NOT EXISTS mythic_progress_partition ( -- sc_44009 玩家通关分区/星数（行化）
    uid INTEGER NOT NULL,
    partition_id INTEGER NOT NULL,
    star INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, partition_id)
);

-- 表: mythic_progress_reward
CREATE TABLE IF NOT EXISTS mythic_progress_reward (  -- sc_44009 已领星级奖励档位（行化）
    uid INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, reward_id)
);

-- 表: mythic_public
CREATE TABLE IF NOT EXISTS mythic_public (           -- sc_44019 MyTHIC 幻境玩家公共（普通神话）
    uid INTEGER PRIMARY KEY,
    current_difficulty INTEGER DEFAULT 0,            -- 当前选择难度
    is_new_difficulty INTEGER DEFAULT 0,
    next_refresh_ts INTEGER DEFAULT 0,               -- sc_44007 f5
    recommend_team_1 INTEGER DEFAULT 0,              -- sc_44007 f6
    recommend_team_2 INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: newbie_activity
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

-- 表: newbie_level_reward
CREATE TABLE IF NOT EXISTS newbie_level_reward (     -- sc_59011 升级奖励已领档位（行化）
    uid INTEGER NOT NULL,
    level INTEGER NOT NULL,                          -- 20/30/40/50/60
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, level)
);

-- 表: newbie_pt_reward
CREATE TABLE IF NOT EXISTS newbie_pt_reward (        -- sc_59011 累计任务奖励档位（行化）
    uid INTEGER NOT NULL,
    pt_id INTEGER NOT NULL,                          -- 1~7
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pt_id)
);

-- 表: oath_assignment
CREATE TABLE IF NOT EXISTS oath_assignment (         -- sc_14503 誓约派遣任务（行化）
    uid INTEGER NOT NULL,
    assignment_id INTEGER NOT NULL,
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, assignment_id)
);

-- 表: oath_assignment_meta
CREATE TABLE IF NOT EXISTS oath_assignment_meta (    -- sc_14503 誓约派遣总览
    uid INTEGER PRIMARY KEY,
    send_type INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
);

-- 表: ops_common
CREATE TABLE IF NOT EXISTS ops_common (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- survey/operate/platform/announcement
    item_id TEXT NOT NULL,       -- 问卷id/运营id/平台id/公告id
    name TEXT DEFAULT '',
    value_json TEXT DEFAULT '{}',-- 内容(公告含HTML/图片/外链)
    PRIMARY KEY (uid, kind, item_id)
);

-- 表: osiris_chapter_progress
CREATE TABLE IF NOT EXISTS osiris_chapter_progress ( -- sc_2635 奥西里斯 6 章进度标记
    uid INTEGER NOT NULL,
    chapter_no INTEGER NOT NULL,                     -- 1~6
    value INTEGER DEFAULT 0,                         -- 6/5/4/3/2/1
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_no)
);

-- 表: osiris_stage_progress
CREATE TABLE IF NOT EXISTS osiris_stage_progress (   -- sc_2635 奥西里斯玩法关卡进度（行化）
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 5260111~5260163（6章×3）
    score INTEGER DEFAULT 0,                         -- 分数
    settle INTEGER DEFAULT 0,                        -- 结算值
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: pinball_progress
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

-- 表: player_card
CREATE TABLE IF NOT EXISTS player_card (
    uid INTEGER NOT NULL,
    kind TEXT NOT NULL,           -- portrait/icon_frame/title/card_bg/home_bg/bubble/game_icon/sticker/sticker_bg
    item_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    obtained INTEGER DEFAULT 0,   -- 1=账号拥有
    PRIMARY KEY (uid, kind, item_id)
);

-- 表: player_skin
CREATE TABLE IF NOT EXISTS player_skin (             -- sc_14201 管理员玩家皮肤总览
    uid INTEGER PRIMARY KEY,
    player_id INTEGER DEFAULT 0,                     -- 1001
    using_skin INTEGER DEFAULT 0,                    -- 使用中皮肤 0
    update_ts INTEGER DEFAULT 0
);

-- 表: player_skin_unlocked
CREATE TABLE IF NOT EXISTS player_skin_unlocked (    -- sc_14201 已解锁皮肤（行化）
    uid INTEGER NOT NULL,
    skin_id INTEGER NOT NULL,                        -- 100102
    unlock_ts INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, skin_id)
);

-- 表: polyhedron_artifact
CREATE TABLE IF NOT EXISTS polyhedron_artifact (     -- sc_18001 神器图鉴样本（行化）
    uid INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,                    -- 70501~70867 等
    state INTEGER DEFAULT 0,                         -- 1待查看/2已查看
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, artifact_id)
);

-- 表: polyhedron_beacon
CREATE TABLE IF NOT EXISTS polyhedron_beacon (       -- sc_18001 信标（行化）
    uid INTEGER NOT NULL,
    beacon_id INTEGER NOT NULL,                      -- 1~14
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, beacon_id)
);

-- 表: polyhedron_difficulty
CREATE TABLE IF NOT EXISTS polyhedron_difficulty (   -- sc_18001 已通关难度（行化）
    uid INTEGER NOT NULL,
    difficulty_id INTEGER NOT NULL,                  -- 1/6/11/16~19
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, difficulty_id)
);

-- 表: polyhedron_hero
CREATE TABLE IF NOT EXISTS polyhedron_hero (         -- sc_18001 玩法英雄+星盘（行化）
    uid INTEGER NOT NULL,
    hero_id INTEGER NOT NULL,                        -- 1035~1284
    astrolabe_1 INTEGER DEFAULT 0,
    astrolabe_2 INTEGER DEFAULT 0,
    astrolabe_3 INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, hero_id)
);

-- 表: polyhedron_meta
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

-- 表: polyhedron_policy_claimed
CREATE TABLE IF NOT EXISTS polyhedron_policy_claimed (
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, level)
);

-- 表: polyhedron_run
CREATE TABLE IF NOT EXISTS polyhedron_run (
    uid INTEGER NOT NULL PRIMARY KEY,
    state INTEGER DEFAULT 1,
    tier_id INTEGER DEFAULT 1001,
    run_data TEXT,
    update_ts INTEGER DEFAULT 0
);

-- 表: polyhedron_terminal
CREATE TABLE IF NOT EXISTS polyhedron_terminal (     -- sc_18001 终端升级（行化）
    uid INTEGER NOT NULL,
    terminal_id INTEGER NOT NULL,                    -- 62~63 个
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, terminal_id)
);

-- 表: pt_roulette_progress
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

-- 表: quanzhou_level_progress
CREATE TABLE IF NOT EXISTS quanzhou_level_progress ( -- sc_20501 泉州箱庭航行见闻等级+档位奖励
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323841
    level INTEGER DEFAULT 0,                         -- 见闻等级（10 满级）
    reward_id_list TEXT DEFAULT '[]',                -- 已领档位奖励（32364101~32364110）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: quanzhou_sail_progress
CREATE TABLE IF NOT EXISTS quanzhou_sail_progress (  -- sc_20499 泉州箱庭航行解锁进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 323841
    skill_id_list TEXT DEFAULT '[]',                 -- 已解锁航行技能（1001~2003）
    stage_id_list TEXT DEFAULT '[]',                 -- 已解锁航行关卡（400001~400106）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: qworld_quest
CREATE TABLE IF NOT EXISTS qworld_quest (            -- sc_28801 泉州箱庭主线组
    uid INTEGER NOT NULL,
    main_quest_id INTEGER NOT NULL,                  -- 11101~11350/101/201/202
    status INTEGER DEFAULT 0,                        -- 9完成/3进行中
    expired_ts INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, main_quest_id)
);

-- 表: qworld_quest_task
CREATE TABLE IF NOT EXISTS qworld_quest_task (       -- sc_28801 泉州箱庭子任务（行化）
    uid INTEGER NOT NULL,
    main_quest_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    status INTEGER DEFAULT 0,                        -- 1完成/0进行
    progress INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, main_quest_id, task_id)
);

-- 表: recommend_equip
CREATE TABLE IF NOT EXISTS recommend_equip (
    engrave_id INTEGER NOT NULL,  -- 英雄id（静态配置，无uid）
    astro_json TEXT DEFAULT '[]',
    servant_json TEXT DEFAULT '[]',
    omega_json TEXT DEFAULT '[]',
    PRIMARY KEY (engrave_id)
);

-- 表: redpoint
CREATE TABLE IF NOT EXISTS redpoint (
    uid         INTEGER NOT NULL,
    red_dot_id  INTEGER NOT NULL,
    state       INTEGER DEFAULT 0,    -- 0=已处理(finished) 1=待展示(red_dot 白名单)
    finish_ts   INTEGER DEFAULT 0,
    PRIMARY KEY (uid, red_dot_id)
);

-- 表: regression
CREATE TABLE IF NOT EXISTS regression (              -- sc_62019 回归活动抽奖
    uid INTEGER PRIMARY KEY,
    other_sign INTEGER DEFAULT 0,
    draw_over_sign INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0
, "received_sign_list" TEXT DEFAULT '[]', "find_time" INTEGER DEFAULT 0);

-- 表: reserve_team
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

-- 表: reserve_team_hero
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

-- 表: resident_music_record
CREATE TABLE IF NOT EXISTS resident_music_record (
    uid INTEGER NOT NULL,
    music_id INTEGER NOT NULL,
    score INTEGER DEFAULT 0,
    sign INTEGER DEFAULT 0,
    is_rewarded INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, music_id)
);

-- 表: resource_stage
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

-- 表: rhythm_game_progress
CREATE TABLE IF NOT EXISTS rhythm_game_progress (    -- sc_17588 节奏游戏关卡进度
    uid INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,                       -- 5190300~5190317
    star_id_list TEXT DEFAULT '[]',                  -- [101,102,103]
    use_seconds INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: rhythm_story_record
CREATE TABLE IF NOT EXISTS rhythm_story_record (
    uid INTEGER NOT NULL,
    story_id INTEGER NOT NULL,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, story_id)
);

-- 表: rogue_card_game_record
CREATE TABLE IF NOT EXISTS rogue_card_game_record (
    uid INTEGER NOT NULL,
    thread_id INTEGER DEFAULT 0,
    thread_state INTEGER DEFAULT 0,
    battle_id INTEGER DEFAULT 0,
    finish_thread_list TEXT DEFAULT '[]',
    like_thread_list TEXT DEFAULT '[]',
    post_thread_list TEXT DEFAULT '[]',
    view_thread_list TEXT DEFAULT '[]',
    challenge_deck INTEGER DEFAULT 0,
    challenge_diff INTEGER DEFAULT 0,
    challenge_state INTEGER DEFAULT 0,
    challenge_battle_id INTEGER DEFAULT 0,
    challenge_status_list TEXT DEFAULT '[]',
    gather_card_list TEXT DEFAULT '[]',
    gather_enhance_list TEXT DEFAULT '[]',
    gather_weal_woe_list TEXT DEFAULT '[]',
    save_data TEXT DEFAULT '{}',
    save_rollback TEXT DEFAULT '{}',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);

-- 表: rogueteam_history
CREATE TABLE IF NOT EXISTS rogueteam_history (        -- sc_88305 / sc_88307 通关历史统计
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    diff_clear_json TEXT DEFAULT '[]',                -- [{key: diff_id, value: count}]
    ending_pass_json TEXT DEFAULT '[]',               -- [{key: ending_id, value: count}]
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id)
);

-- 表: rogueteam_illustrated
CREATE TABLE IF NOT EXISTS rogueteam_illustrated (    -- sc_88305 / sc_88313 图鉴与收集品
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    item_id INTEGER NOT NULL,
    item_type INTEGER DEFAULT 1,
    is_viewed INTEGER DEFAULT 0,
    unlock_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id, item_id)
);

-- 表: rogueteam_item
CREATE TABLE IF NOT EXISTS rogueteam_item (          -- sc_22769 RogueTeam 道具明细（f3 各组展开）
    uid INTEGER NOT NULL,
    category_id INTEGER NOT NULL,                    -- 组1~5（1=141/142/143系, 2=10000~60050系, 3=131/132/133系, 4=1200001系, 5=初始标记[6,1,2,3]）
    item_id INTEGER NOT NULL,                        -- 道具 id（rogueteamitemcfg）
    name TEXT DEFAULT '',
    in_f6 INTEGER DEFAULT 0,                         -- 是否在 f6/f7 列表（131/132/133 系）
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, category_id, item_id)
);

-- 表: rogueteam_map
CREATE TABLE IF NOT EXISTS rogueteam_map (           -- sc_24165 RogueTeam 地图节点进度
    uid INTEGER NOT NULL,
    map_id INTEGER NOT NULL,                         -- 1001~3005（rogueteammapcfg 节点）
    status INTEGER DEFAULT 1,                        -- 1=已达成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, map_id)
);

-- 表: rogueteam_progress
CREATE TABLE IF NOT EXISTS rogueteam_progress (      -- sc_22769 RogueTeam 玩家总览
    uid INTEGER PRIMARY KEY,
    mode_flag INTEGER DEFAULT 0,                     -- 100001 玩法模式标识
    f2_flag INTEGER DEFAULT 0,                       -- 帧 f2 状态位
    f4_count_json TEXT DEFAULT '[]',                 -- [{group,count}] 组计数
    f5_mark_json TEXT DEFAULT '[]',                  -- [{group,count}] 标记
    top_tech_node INTEGER DEFAULT 0,                 -- 最高科技树节点 110160
    update_ts INTEGER DEFAULT 0
, "status" INTEGER DEFAULT 0, "progress_ref_json" TEXT DEFAULT '[]');

-- 表: rogueteam_session
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
    in_game INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0, "room_passed_count" INTEGER DEFAULT 0, "battle_passed_count" INTEGER DEFAULT 0, "score_rate" INTEGER DEFAULT 100,
    PRIMARY KEY (uid, template_id)
);

-- 表: rogueteam_tech
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

-- 表: rogueteam_tree
CREATE TABLE IF NOT EXISTS rogueteam_tree (           -- sc_88305 / sc_88301 科技树节点
    uid INTEGER NOT NULL,
    template_id INTEGER NOT NULL DEFAULT 100001,
    node_id INTEGER NOT NULL,
    unlock_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, template_id, node_id)
);

-- 表: rogueteam_user
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

-- 表: sandplay_progress
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

-- 表: scene
CREATE TABLE IF NOT EXISTS scene (
    uid INTEGER NOT NULL,
    scene_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    scene_type INTEGER DEFAULT 0,  -- 1=常驻展示 / 0=活动特殊
    is_current INTEGER DEFAULT 0,
    obtain_time INTEGER DEFAULT 0,
    PRIMARY KEY (uid, scene_id)
);

-- 表: scene_pool_unopen
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

-- 表: sdk
CREATE TABLE IF NOT EXISTS sdk (
    uid         INTEGER PRIMARY KEY,
    token       TEXT,
    gstoken     TEXT,
    uid_sign    TEXT,
    register_ts INTEGER DEFAULT 0,
    extra       TEXT DEFAULT '{}'
);

-- 表: servant
CREATE TABLE IF NOT EXISTS servant (
    uid INTEGER NOT NULL,
    id INTEGER NOT NULL,
    prefab_id INTEGER,
    name TEXT,
    race INTEGER,
    starlevel INTEGER,
    stage INTEGER,
    owned INTEGER,
    is_locked INTEGER,
    update_ts INTEGER,
    PRIMARY KEY (uid, id)
);

-- 表: servant_history
CREATE TABLE IF NOT EXISTS servant_history (
        uid INTEGER NOT NULL,
        id INTEGER NOT NULL,
        prefab_id INTEGER NOT NULL,
        PRIMARY KEY (uid, id)
    );

-- 表: server_global_config
CREATE TABLE IF NOT EXISTS server_global_config (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

-- 表: seven_day_skin_new
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

-- 表: shop
CREATE TABLE IF NOT EXISTS shop (
    shop_id INTEGER PRIMARY KEY,
    name TEXT DEFAULT '',
    group_name TEXT DEFAULT '',
    system INTEGER DEFAULT 1,
    refresh_num_limit INTEGER DEFAULT 0,
    activity_id INTEGER DEFAULT 0,
    is_permanent INTEGER DEFAULT 1
);

-- 表: shop_goods
CREATE TABLE IF NOT EXISTS shop_goods (
    goods_id INTEGER PRIMARY KEY, -- 商品id（静态配置，无uid）
    item_id INTEGER DEFAULT 0,    -- 对应物品id(description)
    name TEXT DEFAULT '',
    cost_type INTEGER DEFAULT 0,
    cost_id INTEGER DEFAULT 0,
    cost INTEGER DEFAULT 0,
    cheap_cost INTEGER DEFAULT 0,
    discount INTEGER DEFAULT 0,
    limit_num INTEGER DEFAULT 0,
    refresh_cycle INTEGER DEFAULT 0,
    open_time TEXT DEFAULT '',
    close_time TEXT DEFAULT '',
    tag INTEGER DEFAULT 0,
    detail_json TEXT DEFAULT '{}'
, shop_id INTEGER DEFAULT 0);

-- 表: sign
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

-- 表: sign_reward_cfg
CREATE TABLE IF NOT EXISTS sign_reward_cfg (   -- 每日签到奖励（signcfg.lua 静态）
    month INTEGER NOT NULL,
    day INTEGER NOT NULL,
    reward_id INTEGER DEFAULT 0,
    reward_num INTEGER DEFAULT 0,
    PRIMARY KEY (month, day)
);

-- 表: skin_draw_guarantee
CREATE TABLE IF NOT EXISTS skin_draw_guarantee (     -- sc_2649 誓约卡池保底记录（行化，语义待确认）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    drop_id INTEGER NOT NULL,                        -- 保底核心格
    idx INTEGER NOT NULL,                            -- 1~10
    value INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, drop_id, idx)
);

-- 表: skin_draw_pool
CREATE TABLE IF NOT EXISTS skin_draw_pool (          -- sc_2615/2649 T0 皮肤抽取卡池剩余份数（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4322201/4322202 诗蔻蒂 / 4342401/4342402 托特
    drop_id INTEGER NOT NULL,                        -- 奖励格 id（2301~2320/2401~2408/2501~2519/2601~2619）
    remain INTEGER DEFAULT 0,                        -- 剩余份数（=total 未抽）
    update_ts INTEGER DEFAULT 0, "pool_name" TEXT DEFAULT '', "reward_item_id" INTEGER DEFAULT 0, "reward_name" TEXT DEFAULT '',
    PRIMARY KEY (uid, activity_id, drop_id)
);

-- 表: skin_draw_pool_cfg
CREATE TABLE IF NOT EXISTS skin_draw_pool_cfg (   -- 皮肤抽池配置（活动limited draw pool，静态无 uid）
    pool_id INTEGER PRIMARY KEY,
    draw_name TEXT DEFAULT '',
    token INTEGER DEFAULT 0,               -- 抽卡凭证 item_id
    cost_once TEXT DEFAULT '[]',           -- [凭证id, 单抽数]
    cost_ten TEXT DEFAULT '[]',            -- [凭证id, 十连数]
    flawless_exchange TEXT DEFAULT '{}',   -- 移转之辉直兑 {cost_id,unit,qty,give}
    ascending TEXT DEFAULT '[]'            -- 移转之花递增档 [{unit,discount,qty,give}, ...]
);

-- 表: skin_draw_story
CREATE TABLE IF NOT EXISTS skin_draw_story (         -- sc_2625 皮肤抽取剧情进度
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 4322301
    story_stage INTEGER DEFAULT 0,                   -- 当前阶段 1
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: skin_draw_story_finished
CREATE TABLE IF NOT EXISTS skin_draw_story_finished (-- sc_2625 已完成剧情（行化）
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    story_id INTEGER NOT NULL,                       -- 928011001~928041001
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, story_id)
);

-- 表: skin_pool_unopen
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

-- 表: skuld_progress
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

-- 表: sp_hero_boss_progress
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

-- 表: sp_hero_challenge_schedule
CREATE TABLE IF NOT EXISTS sp_hero_challenge_schedule ( -- sc_17480 薇儿丹蒂挑战日程记录
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242841
    schedule_type INTEGER DEFAULT 0,                 -- 1~5
    schedule_id INTEGER NOT NULL,                    -- 2428401~2428406
    ts INTEGER DEFAULT 0,                            -- 1786337562
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, schedule_id, schedule_type)
);

-- 表: sp_hero_challenge_stage
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

-- 表: sp_hero_puzzle_progress
CREATE TABLE IF NOT EXISTS sp_hero_puzzle_progress ( -- sc_17485 SP 英雄挑战·解密玩法(242871)
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,                    -- 242871
    cleared_stage_list TEXT DEFAULT '[]',            -- [2428701~2428720] 通关关卡
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: stage_archive_collect
CREATE TABLE IF NOT EXISTS stage_archive_collect (
    uid INTEGER NOT NULL,
    archive_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    view_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, archive_id)
);

-- 表: stage_clear_team
CREATE TABLE IF NOT EXISTS stage_clear_team (       -- 关卡通关阵容增量表（team_server 管理，关卡ID唯一主键）
    uid                  INTEGER NOT NULL,
    stage_id             INTEGER NOT NULL,          -- 通关关卡 ID（唯一区分主键）
    hero_json            TEXT NOT NULL DEFAULT '[]',-- 出战修正者 id 有序数组（上报次序不可乱，[0]=队长位）
    hero_count           INTEGER NOT NULL DEFAULT 0,
    cooperate_skill      INTEGER NOT NULL DEFAULT 0,-- 本场使用连携 ID
    mimir_id             INTEGER NOT NULL DEFAULT 0,-- 助战弥弥尔（管理器钥从）id
    mimir_chips          TEXT NOT NULL DEFAULT '',   -- 弥弥尔携带芯片（逗号分隔）
    hero_chips           TEXT NOT NULL DEFAULT '',   -- 助战英雄芯片（逗号分隔，出战帧暂无数据源留空）
    clear_times          INTEGER NOT NULL DEFAULT 0, -- 该关卡累计通关次数
    last_clear_ts        INTEGER DEFAULT 0,
    update_ts            INTEGER DEFAULT 0,
    PRIMARY KEY (uid, stage_id)
);

-- 表: stage_sub
CREATE TABLE IF NOT EXISTS stage_sub (
    uid INTEGER NOT NULL, id INTEGER NOT NULL, chapter_id INTEGER,
    name TEXT DEFAULT '', category TEXT DEFAULT '支线', category_id INTEGER DEFAULT 3,
    difficulty INTEGER DEFAULT 0, max_star INTEGER DEFAULT 3,
    star_list TEXT DEFAULT '[0,0,0]', clear_times INTEGER DEFAULT 0,
    stamina_need INTEGER DEFAULT 0, first_reward TEXT DEFAULT '[]',
    reward TEXT DEFAULT '[]', team_list TEXT DEFAULT '[]',
    first_clear_ts INTEGER, update_ts INTEGER, type INTEGER DEFAULT 0, chapter_name TEXT DEFAULT '',
    PRIMARY KEY (uid, id));

-- 表: stage_team
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

-- 表: store_purchase
CREATE TABLE IF NOT EXISTS store_purchase (
    uid INTEGER NOT NULL,
    shop_id INTEGER NOT NULL,
    goods_id INTEGER NOT NULL,
    buy_times INTEGER DEFAULT 0,
    next_refresh_timestamp INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, shop_id, goods_id)
);

-- 表: story_unlock
CREATE TABLE IF NOT EXISTS story_unlock (
    uid INTEGER NOT NULL,
    story_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    unlock_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, story_id)
);

-- 表: summer_chess_node
CREATE TABLE IF NOT EXISTS summer_chess_node (       -- sc_19569 夏日棋盘节点条目（行化）
    uid INTEGER NOT NULL,
    node_id INTEGER NOT NULL,                        -- 1/4/5/12~16/31/32
    item_id INTEGER NOT NULL,                        -- 222001~222015 等
    state INTEGER DEFAULT 0,                         -- 1=完成
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, node_id, item_id)
);

-- 表: summer_chess_point
CREATE TABLE IF NOT EXISTS summer_chess_point (      -- sc_19565 夏日棋盘积分奖励档位（行化）
    uid INTEGER NOT NULL,
    point_id INTEGER NOT NULL,                       -- 24190001~24190043
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, point_id)
);

-- 表: summer_pub_record
CREATE TABLE IF NOT EXISTS summer_pub_record (
    uid INTEGER NOT NULL,
    stage_list TEXT DEFAULT '[]',
    cook_stages TEXT DEFAULT '{}',
    battle_stages TEXT DEFAULT '[]',
    illustrated TEXT DEFAULT '{}',
    time_state INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid)
);

-- 表: system_closed
CREATE TABLE IF NOT EXISTS system_closed (            -- [Fix by Gemini 3.7-flash] 关闭系统备份表
    uid INTEGER NOT NULL,
    system_id INTEGER NOT NULL,
    system_name TEXT DEFAULT '',
    is_open INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    remark TEXT DEFAULT '',
    PRIMARY KEY (uid, system_id)
);

-- 表: system_config
CREATE TABLE IF NOT EXISTS system_config (
    uid INTEGER NOT NULL,
    sys_id INTEGER NOT NULL,
    open INTEGER DEFAULT 1,      -- 1=开放(10600) 0=未开放
    suspend INTEGER DEFAULT 0,   -- 1=停用(40035)
    name TEXT DEFAULT '',
    PRIMARY KEY (uid, sys_id)
);

-- 表: task
CREATE TABLE IF NOT EXISTS task (
    uid INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    task_type INTEGER DEFAULT 0,
    progress INTEGER DEFAULT 0,
    complete_flag INTEGER DEFAULT 0,
    expired_ts INTEGER DEFAULT 0,
    claimed_ts INTEGER DEFAULT 0,   -- 领取时间(防重复入账)
    PRIMARY KEY (uid, task_id)
);

-- 表: task_cfg
CREATE TABLE IF NOT EXISTS task_cfg (
    task_id INTEGER PRIMARY KEY,     -- 全量任务配置（静态，无 uid）
    name TEXT DEFAULT '',
    task_type INTEGER DEFAULT 0,
    condition INTEGER DEFAULT 0,
    desc TEXT DEFAULT '',
    need INTEGER DEFAULT 0,
    activity_id INTEGER DEFAULT 0,
    pre_id INTEGER DEFAULT 0,
    reward_json TEXT DEFAULT '[]'
, "additional_parameter" TEXT DEFAULT '[]');

-- 表: tower
CREATE TABLE IF NOT EXISTS tower (
    uid INTEGER NOT NULL,
    area INTEGER NOT NULL,
    stage INTEGER DEFAULT 0,
    name TEXT DEFAULT '',
    PRIMARY KEY (uid, area)
);

-- 表: trust_displace_counter
CREATE TABLE IF NOT EXISTS trust_displace_counter ( -- 好感度礼物置换每日限次计数（sc_73005 limit_list 数据源）
    uid                  INTEGER NOT NULL,
    item_id              INTEGER NOT NULL,          -- 目标礼物 id（30012~30015）
    day_key              TEXT NOT NULL,             -- 每日 5 点跨天日键（如 20260906_5）
    use                  INTEGER NOT NULL DEFAULT 0,-- 当日已置换数量
    PRIMARY KEY (uid, item_id, day_key)
);

-- 表: tutorial
CREATE TABLE IF NOT EXISTS tutorial (
    uid INTEGER NOT NULL,
    tutorial_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    kind TEXT DEFAULT 'base',   -- base=基础教学 / hero=英雄教学
    pass_flag INTEGER DEFAULT 0,
    PRIMARY KEY (uid, tutorial_id)
);

-- 表: unclaimed
CREATE TABLE IF NOT EXISTS unclaimed (               -- sc_12023 未领取挑战补偿
    uid INTEGER NOT NULL,
    unclaimed_id INTEGER NOT NULL,                   -- 1矩阵/2BOSS/3神话/4装备侵夺/5深渊
    stage INTEGER DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, unclaimed_id)
);

-- 表: user_behavior_stat
CREATE TABLE IF NOT EXISTS user_behavior_stat (
    uid INTEGER,
    stat_key TEXT,
    hero_id INTEGER,
    value INTEGER DEFAULT 0,
    update_ts INTEGER,
    PRIMARY KEY (uid, stat_key, hero_id)
);

-- 表: user_daily_shop
CREATE TABLE IF NOT EXISTS user_daily_shop (uid INTEGER PRIMARY KEY, refresh_times INTEGER DEFAULT 0, goods_list_json TEXT DEFAULT '[]', last_refresh_ts INTEGER DEFAULT 0, update_ts INTEGER DEFAULT 0);

-- 表: user_periodic_gift
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

-- 表: user_recharge
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

-- 表: user_scene
CREATE TABLE IF NOT EXISTS user_scene (
        uid INTEGER NOT NULL,
        scene_id INTEGER NOT NULL,
        lasted_time INTEGER DEFAULT 0,
        obtain_time INTEGER DEFAULT 0,
        update_ts INTEGER DEFAULT 0,
        PRIMARY KEY (uid, scene_id)
    );

-- 表: user_setting
CREATE TABLE IF NOT EXISTS user_setting (            -- sc_21001 提醒设置
    uid INTEGER PRIMARY KEY,
    sign_in_propel_switch INTEGER DEFAULT 1,
    month_card_propel_switch INTEGER DEFAULT 1,
    update_ts INTEGER DEFAULT 0
);

-- 表: user_skin_draw_pool
CREATE TABLE IF NOT EXISTS user_skin_draw_pool ( -- 换装/场景卡池抽卡实时库存
    uid INTEGER NOT NULL,
    pool_id INTEGER NOT NULL,
    drop_id INTEGER NOT NULL,
    remain_num INTEGER NOT NULL DEFAULT 0,
    drawn_num INTEGER NOT NULL DEFAULT 0,
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pool_id, drop_id)
);

-- 表: user_skin_draw_pop
CREATE TABLE IF NOT EXISTS user_skin_draw_pop (
    uid INTEGER NOT NULL,
    skin_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, skin_id)
);

-- 表: user_skin_draw_progress
CREATE TABLE IF NOT EXISTS user_skin_draw_progress (
    uid INTEGER NOT NULL,
    pool_id INTEGER NOT NULL,
    misses INTEGER NOT NULL DEFAULT 0,
    last_drop INTEGER NOT NULL DEFAULT 0,
    already_drop TEXT NOT NULL DEFAULT '[0,0,0,0,0,0,0,0,0,0]',
    update_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, pool_id)
);

-- 表: user_skin_story
CREATE TABLE IF NOT EXISTS user_skin_story (     -- 换装活动剧情完成状态
    uid INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    story_id INTEGER NOT NULL,
    finish_ts INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id, story_id)
);

-- 表: user_timer_listener_retry
CREATE TABLE IF NOT EXISTS user_timer_listener_retry (
    uid                  INTEGER NOT NULL,
    event_name           TEXT NOT NULL,
    listener_key         TEXT NOT NULL,
    cycle_start_ts       INTEGER NOT NULL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    next_retry_ts        INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (uid, event_name, listener_key)
);

-- 表: user_timer_listener_watermark
CREATE TABLE IF NOT EXISTS user_timer_listener_watermark (
    uid                  INTEGER NOT NULL,
    event_name           TEXT NOT NULL,
    listener_key         TEXT NOT NULL,
    cycle_start_ts       INTEGER NOT NULL,
    PRIMARY KEY (uid, event_name, listener_key)
);

-- 表: user_timer_watermark
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

-- 表: users
CREATE TABLE IF NOT EXISTS users (
    uid         INTEGER PRIMARY KEY,
    account     TEXT,
    server_id   INTEGER DEFAULT 2,
    nick        TEXT DEFAULT '',
    level       INTEGER DEFAULT 1,
    exp         INTEGER DEFAULT 0,
    extra       TEXT DEFAULT '{}'
, hero_num INTEGER DEFAULT 0, plot_progress INTEGER DEFAULT 0, is_changed_nick INTEGER DEFAULT 0, change_nick_times INTEGER DEFAULT 0, sign TEXT DEFAULT "", portrait INTEGER DEFAULT 0, icon_frame INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, ip_location TEXT DEFAULT "", register_ts INTEGER DEFAULT 0, club_id INTEGER DEFAULT 0, club_post INTEGER DEFAULT 0);

-- 表: validation
CREATE TABLE IF NOT EXISTS validation (
    uid         INTEGER NOT NULL,
    kind        TEXT NOT NULL,        -- 'assethash' / 'voice_zh' / 'voice_ja' / 'voice_package'
    version     TEXT DEFAULT '',
    detail      TEXT DEFAULT '{}',
    PRIMARY KEY (uid, kind)
);

-- 表: vehicle_ball_progress
CREATE TABLE IF NOT EXISTS vehicle_ball_progress (
    uid INTEGER PRIMARY KEY,
    using_vehicle INTEGER DEFAULT 50111,
    stages_json TEXT DEFAULT '[]',
    unlock_buffs_json TEXT DEFAULT '[]',
    update_ts INTEGER DEFAULT 0
);

-- 表: warchess_box
CREATE TABLE IF NOT EXISTS warchess_box (
    uid INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    box_id INTEGER NOT NULL,
    num INTEGER DEFAULT 0,
    PRIMARY KEY (uid, chapter_id, box_id)
);

-- 表: warchess_box_record
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

-- 表: warchess_chapter
CREATE TABLE IF NOT EXISTS warchess_chapter (
    chapter_id INTEGER PRIMARY KEY,   -- 4040xxx 战棋章节
    name TEXT DEFAULT '',
    chapter_type INTEGER DEFAULT 0,   -- 0=常驻
    open_ts INTEGER DEFAULT 0
);

-- 表: warchess_map
CREATE TABLE IF NOT EXISTS warchess_map (
    uid INTEGER NOT NULL,
    activity_id INTEGER DEFAULT 0,
    current_chapter INTEGER DEFAULT 0,
    PRIMARY KEY (uid, activity_id)
);

-- 表: warchess_session
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

-- -----------------------------------------------------------------------------
-- -----------------------------------------------------------------------------

-- 索引: idx_draw_record_uid_group
CREATE INDEX IF NOT EXISTS idx_draw_record_uid_group ON draw_record(uid, pool_group, id DESC);

-- 索引: idx_draw_record_uid_pool
CREATE INDEX IF NOT EXISTS idx_draw_record_uid_pool ON draw_record(uid, pool_id, id DESC);

-- 索引: idx_periodic_gift_uid_status
CREATE INDEX IF NOT EXISTS idx_periodic_gift_uid_status ON user_periodic_gift(uid, status);
