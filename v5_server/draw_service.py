# -*- coding: utf-8 -*-
"""
draw_service.py — V5 抽卡与卡池领域服务（DrawService，2026-09-05）

职责：
1. 卡池元数据与分类纳管（全服 199 卡池）：
   - 划分 4 大保底系列（hero_precision_90, hero_precision_70, hero_standard_70, weapon_servant_70）；
   - 卡池资源安全评级（Grade A 现役验证 / Grade B 通用多选一 / Grade C 远古单UP待验证）；
   - 识别并隔离消耗“限时精确探测凭证”的特殊卡池（统计入库但不开放，防资产异常）。
2. 保底与状态跨池共享继承体系（draw_group_state 表）：
   - 系列内部继承 since_ssr / is_up_guaranteed / up_id；
   - 系列之间保底计数与状态严格物理隔离。
3. 抽卡核心业务引擎（RNG、验资扣券、防歪大保底、产物严格物理隔离）：
   - 角色池严禁产出 4/5 星钥从，仅出 S/A 角色与 3 星钥从副产物；
   - 钥从池仅出 6 大阵营沉睡之子与 4 星钥从，严禁产出角色与专属五星钥从；
   - 重复修正者自动转碎片（S级30片 / A级18片 / B级10片），发放共鸣辉芒；
   - 资产扣减支持 0 值清零白名单下发，彻底杜绝 UI 数量假残留。
4. 动态卡池下发与热更协议（sc_16015）：
   - 替代登录洪流写死的 6 个静态历史卡池，由当前激活的卡池列表动态生成；
   - 支持在线热更推送，客户端无需重启即可无缝刷新在架卡池。
5. 卡池详情与角色展示列表协议（cs_16018 → sc_16019）：
   - 动态装配 pool_details_net_rec（S级UP率、本期UP列表、非UP歪卡列表、A级UP与常驻列表）；
   - 精准 70 歪卡集合严格排除 33 位常驻自选修正者与当期 UP 角色！
6. 对外开放控制台接口与卡池预设模板（Presets）：
   - 供 Web 控制台、GM 接口灵活查询、上架、下架卡池、切换经典预设组合及调控保底。
"""

import json
import logging
import os
import random
import time

from middleware import DownFrame

logger = logging.getLogger("DrawService")

# ==================== 官方基准概率与软保底参数 ====================
# 权威来源：decompiled_v2/x64/game/config/tipscfg.lua (359001 ~ 359034)
# 角色池官方告示：S 级 1.60%（含保底 2.40%）、A 级 7.80%（含保底 14.00%）、B 级 6.00%、3 星钥从 84.60%
BASE_RATE_SSR = 0.016            # S 级修正者 / 5 星钥从基础出率 (1.60%)
BASE_RATE_SR_HERO = 0.078        # A 级修正者基础出率 (7.80%)
BASE_RATE_SR_WEAPON = 0.1108     # 4 星钥从基础出率 (11.08%)
BASE_RATE_R_HERO = 0.060         # B 级修正者基础出率 (6.00%)
BASE_RATE_R_WEAPON = 0.846       # 3 星钥从基础出率 (84.60%)

# 兼容旧引用
_DRAW_SSR_RATE = BASE_RATE_SSR

# 6 大阵营 5 星沉睡之子（钥从池唯一 SSR 产物）
SLEEPING_CHILDREN = [2510000, 2520000, 2530000, 2540000, 2550000, 2590000]

# 33 位普通/常驻卡池初始 S 角色（官方 4080101 自选精准卡池 optional_lists 权威名单）
STANDARD_S_HEROES = [
    1066, 1093, 1058, 1139, 1013, 1032, 1119, 1067, 1024, 1127,
    1071, 1072, 1070, 1199, 1111, 1094, 1138, 1081, 1042, 1041,
    1052, 1028, 1132, 1075, 1076, 1074, 1055, 1049, 1158, 1060,
    1061, 1150, 1015
]

# 26 位限定 S 角色（全服 59 位 S 除去上述 33 位常驻角色，即精准 70 会歪的限定池范围）
LIMITED_S_HEROES = [
    1012, 1020, 1021, 1022, 1034, 1043, 1044, 1047, 1053, 1054,
    1073, 1077, 1083, 1085, 1089, 1095, 1133, 1137, 1156, 1166,
    1170, 1194, 1197, 1211, 1248, 1284
]

# 21 位 A 级修正者全量名单
ALL_A_HEROES = [
    1011, 1016, 1017, 1019, 1026, 1027, 1033, 1035, 1037, 1038,
    1039, 1048, 1050, 1056, 1059, 1068, 1080, 1096, 1097, 1099, 1184
]

# 6 大神系 3 星通用钥从名录 (真樱/圣树/奥林匹斯/尼罗/众星/天垣)
SERVANT_3_LIST = [2310001, 2320001, 2330001, 2340001, 2350001, 2390001]

# 2 位官方抽卡副产物 B 级修正者（朝约·薇儿丹蒂 1084, 追炎·前鬼坊天狗 1148）
HERO_B_LIST = [1084, 1148]

# 12 位 4 星通用钥从（六大神系各 2 位）
SERVANT_4_LIST = [
    2410001, 2410002, 2420001, 2420002,
    2430001, 2430002, 2440001, 2440002,
    2450001, 2450002, 2490001, 2490002
]

BASE_B_ITEMS = SERVANT_3_LIST + HERO_B_LIST

# ==================== DrawItemCfg 官方客户端卡池配置 ID 映射表 ====================
# 权威来源：decompiled_v2/x64/game/config/drawitemcfg.lua
# 客户端 drawinfopopview.lua / drawinfocommonitem.lua 必须且仅支持 DrawItemCfg 的条目 ID (id)！

DRAW_ITEM_STD_S = {
    1066: 1001, 1093: 1002, 1058: 1003, 1139: 1004, 1013: 1005, 1032: 1006, 1119: 1007, 1067: 1008, 1024: 1009, 1127: 1010,
    1071: 1011, 1072: 1012, 1070: 1013, 1199: 1014, 1111: 1015, 1094: 1016, 1138: 1017, 1081: 1018, 1042: 1019, 1041: 1020,
    1052: 1021, 1028: 1022, 1132: 1023, 1075: 1024, 1076: 1025, 1074: 1026, 1055: 1027, 1049: 1028, 1158: 1029, 1060: 1030,
    1061: 1031, 1150: 1032, 1015: 1033
}

DRAW_ITEM_LTD_S = {
    1095: 2034, 1284: 2035, 1197: 2036, 1022: 2037, 1021: 2038, 1020: 2039, 1156: 2040, 1089: 2041, 1248: 2042, 1133: 2043,
    1034: 2044, 1012: 2045, 1085: 2046, 1073: 2047, 1211: 2048, 1047: 2049, 1083: 2050, 1044: 2051, 1043: 2052, 1194: 2053,
    1137: 2054, 1053: 2055, 1054: 2056, 1170: 2057, 1077: 2058, 1166: 2059
}

DRAW_ITEM_A = {
    1048: 3001, 1038: 3002, 1099: 3003, 1019: 3004, 1080: 3005, 1027: 3006, 1050: 3007, 1037: 3008, 1011: 3009, 1184: 3010,
    1059: 3011, 1096: 3012, 1026: 3013, 1097: 3014, 1056: 3015, 1035: 3016, 1033: 3017, 1068: 3018, 1017: 3019, 1016: 3020
}

DRAW_ITEM_B = {1084: 4001, 1148: 4002}

DRAW_ITEM_SERVANT_5 = {
    2510000: 5001, 2520000: 5002, 2530000: 5003, 2540000: 5004,
    2550000: 5005, 2590000: 5006, 2550996: 5025, 2550997: 5026
}

DRAW_ITEM_SERVANT_4 = {
    2410001: 5007, 2410002: 5008, 2420001: 5009, 2420002: 5010,
    2430001: 5011, 2430002: 5012, 2440001: 5013, 2440002: 5014,
    2450001: 5015, 2450002: 5016, 2490001: 5017, 2490002: 5018
}

DRAW_ITEM_SERVANT_3 = {
    2310001: 5019, 2320001: 5020, 2330001: 5021,
    2340001: 5022, 2350001: 5023, 2390001: 5024
}

# 基础副产物 DrawItemCfg 条目 ID（2 位 B 级修正者 + 6 位 3 星通用钥从）
BASE_B_DRAW_ITEMS = [4001, 4002, 5019, 5020, 5021, 5022, 5023, 5024]

# 钥从池副产物 DrawItemCfg 条目 ID
SERVANT_B_DRAW_ITEMS = [5019, 5020, 5021, 5022, 5023, 5024]
SERVANT_4_DRAW_ITEMS = [5007, 5008, 5009, 5010, 5011, 5012, 5013, 5014, 5015, 5016, 5017, 5018]
ALL_A_DRAW_ITEMS = list(range(3001, 3021))

# 卡池名与 UP 角色对照映射表（覆盖 70+ 种活动/常驻探测）
POOL_UP_HERO_MAP = {
    "奇迹缔造法则": 1083,       # 澄心·陵光
    "大器免成": 1054,           # 双司镇命·孟章
    "行侠己道": 1071,           # 青君·孟章
    "莫道奈何": 1061,           # 玄机·执明
    "天通明晦": 1076,           # 太一·庚辰
    "夜影的疏语": 1043,         # 隐夜·伊里伽尔
    "彼岸流沙": 1034,           # 潜蛇·阿佩普
    "灵火的宿语": 1053,         # 怀阳·陵光
    "与破晓凯旋": 1021,         # 曦光·巴德尔
    "旷野的呼唤": 1015,         # 狂狮·塞赫麦特
    "洞穿谎言之刃": 1047,       # 裁暗之锋·托尔
    "血色回响": 1044,           # 不灭王权·欧西里斯
    "神机咆哮": 1073,           # 巧构·赫菲斯托斯
    "圣翼的微光": 1137,         # 谧光之刻·海姆达尔
    "瑞气呈祥": 1072,           # 百解·陆吾
    "幻梦回环之理": 1197,       # 梦影·欧西里斯
    "九州巡诫": 1012,           # 天诫·庚辰
    "撕裂迷沼之牙": 1035,       # 狂鳄·索贝克
    "虹式协议": 1150,           # 绯染·丰前坊天狗
    "强袭计划": 1028,           # 轰雷·托尔
    "曼陀罗轻语": 1085,         # 绮望·赫拉
    "时母的战舞": 1156,         # 伐灭·迦梨
    "梵我一如": 1020,           # 三相·梵天
    "奏鸣的剑芒": 1166,         # 星仪·雅典娜
    "幽盈夜歌": 1022,           # 幽月·塞勒涅
    "甜点与童话": 1127,         # 逆潮·利维坦
    "致现在的你": 1284,         # 黯耀·薇儿丹蒂
    "流萤岚雾": 1119,           # 流萤岚雾·休
    "丝与刃之歌": 1042,         # 操偶师·哈迪斯
    "冰渊": 1139,               # 冰渊·波塞冬
    "灵识宝鉴、迷蝶来归": 1077, # 锻玉·欧里罗
    "罪魂裁断": 1211,           # 魂羽·奥西里斯
    "历世狂歌": 1089,           # 焚轮·阿波罗
    "逆潮": 1127,               # 逆潮·利维坦
    "闪雷轰鸣": 1248,           # 神威·托尔
    "樱华尽起": 1049,           # 镜花黄泉·伊邪那美
    "障月": 1013,               # 障月·阿修罗
    "念良游": 1060,             # 九司·陆吾
    "浮生忘闲": 1075,           # 澄心·赫拉
    "剑怅弦鸣": 1074,           # 巡天·英灵
    "破阵乐": 1054,             # 双司镇命·孟章
    "心火烈燃": 1132,           # 烙焰·提尔
    "早樱": 1066,               # 早樱·大国主
    "熯天": 1032,               # 熯天·提尔
    "镜华落羽": 1049,           # 镜花黄泉·伊邪那美
    "觅影": 1093,               # 觅影·国常立
    "龙切": 1058,               # 龙切·迦具土
    "启奏晨辉": 1138,           # 黎幻·赫拉
    "霜月侠影": 1055,           # 霜影·国常立
    "雪浪的回漩": 1081,         # 斩浪·丝卡蒂
    "月华的剑语": 1133,         # 天卫·提尔
    "百解": 1072,               # 百解·陆吾
    "乾坤开吾": 1076,           # 太一·庚辰
    "青君": 1071,               # 青君·孟章
    "玉龙吟风": 1054,           # 双司镇命·孟章
    "十曜": 1070,               # 十曜·金乌
    "丹焰还真": 1170,           # 昭阳·金乌
    "暗焰重燃": 1058,           # 龙切·迦具土
    "刺耳的安魂曲": 1042,       # 操偶师·哈迪斯
    "极昼的闪电宫": 1028,       # 轰雷·托尔
    "陨铁星火": 1026,           # 银臂·努阿达
    "雅典娜": 1041,             # 铃兰之弦·雅典娜
    "旧时流萤": 1119,           # 流萤岚雾·休
    "缪斯茶会": 1042,           # 操偶师·哈迪斯
    "鲸歌的鸣奏": 1127,         # 逆潮·利维坦
    "波涛的奔行": 1067,         # 流转之洋·恩利尔
    "赫拉池子": 1138,           # 黎幻·赫拉
    "海拉池子": 1094,           # 暗星·海拉
    "绝处逢生魂": 1111,         # 生魂·奥西里斯
    "雷光的剑息": 1199,         # 震离·月读
    "震离、欧申纳斯": 1199,     # 震离·月读
    "幽魂倩影、启奏晨辉": 1138, # 黎幻·赫拉
}

# 官方默认推荐活跃卡池（Grade A 现役完备池 + 311全自选池）
DEFAULT_ACTIVE_POOLS = [10001, 10002, 5030601, 5030301, 5020301, 5020302, 5020303, 5020601, 4080101, 10000]

# 卡池经典预设方案
POOL_PRESETS = {
    "default": {
        "name": "官方标准活跃组（澄心·陵光 + 自选扩充）",
        "desc": "常态角色 + 自选钥从 + 陵光70精准 + 雅典娜70 + 赫拉70 + 陵光90精准 + 自选扩充70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 5020301, 5020302, 5020303, 5020601, 5000303, 4080101, 10000]
    },
    "theme44_pioneer": {
        "name": "Build 311 预设方案（全自选锚定90 + 全自选扩充70）",
        "desc": "常态角色 + 自选钥从 + 311自选锚定90 + 311自选扩充70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 5030601, 5030301, 4080101, 10000]
    },
    "all_versions_combo": {
        "name": "双版本通用方案（311自选双池 + 229现役限定）",
        "desc": "常态 + 钥从 + 311自选锚定90 + 311自选扩充70 + 星仪90/70 + 托特70 + 诗蔻蒂70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 5030601, 5030301, 5020601, 5020301, 5020302, 5020303, 4080101, 10000]
    },
    "classic_safe": {
        "name": "经典安全双轨组（澄心·陵光 + 5000303全自选）",
        "desc": "常态角色 + 自选钥从 + 陵光70精准 + 雅典娜70 + 赫拉70 + 陵光90精准 + 自选扩充70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 5020301, 5020302, 5020303, 5020601, 5000303, 4080101, 10000]
    },
    "all_expansion": {
        "name": "全能自选与现役双轨组（自选扩充 + 精选单UP角色专属池）",
        "desc": "常态 + 钥从 + 自选扩充70 + 陵光70 + 陵光90 + 雅典娜70 + 赫拉70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 5000303, 5020301, 5020601, 5020302, 5020303, 4080101, 10000]
    },
    "classic_1_0": {
        "name": "1.0 怀旧开荒组（震离·月读）",
        "desc": "常态角色 + 自选钥从 + 震离·月读精准70 + 自选精准 + 新手池",
        "pool_ids": [10001, 10002, 2040302, 4080101, 10000]
    },
    "hades_show": {
        "name": "奥林匹斯专场（操偶师·哈迪斯）",
        "desc": "常态角色 + 自选钥从 + 哈迪斯丝与刃之歌精准70/90 + 自选精准",
        "pool_ids": [10001, 10002, 3000303, 3000602, 4080101, 10000]
    },
    "tianyuan_sisters": {
        "name": "虚恒天垣组（十曜·金乌 & 太一·庚辰）",
        "desc": "常态角色 + 自选钥从 + 金乌精准70 + 庚辰精准70 + 自选精准",
        "pool_ids": [10001, 10002, 5000302, 2090302, 4080101, 10000]
    },
    "all_precision": {
        "name": "热门限定全开组（多位高人气修正者同台）",
        "desc": "常态 + 钥从 + 陵光70 + 陵光90 + 雅典娜70 + 赫拉70 + 自选精准",
        "pool_ids": [10001, 10002, 5020301, 5020601, 5020302, 5020303, 4080101]
    },
    "standard_clean": {
        "name": "纯净常驻组（开荒专用无限定）",
        "desc": "常态角色探测 + 自选钥从探测 + 自选精准探测 + 新人入职限定探测",
        "pool_ids": [10001, 10002, 4080101, 10000]
    }
}


class DrawService:
    """抽卡与卡池领域服务单例类"""

    _instance = None

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            if cls._instance.db is not db:
                cls._instance.db = db
                cls._instance._init_db_schema()
        return cls._instance

    def __init__(self, db=None):
        self.db = db
        self._pool_up_cfg = None
        self._init_db_schema()
        try:
            import res_version_manager
            res_version_manager.register_version_change_listener(self._on_version_changed)
        except Exception:
            pass

    def _on_version_changed(self, old_ver, new_ver):
        """客户端资源版本变更事件响应"""
        if str(new_ver) == "229":
            self.sanitize_pools_for_version("229")

    def _init_db_schema(self):
        """确保持久化表存在"""
        if self.db is None:
            return
        try:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS active_draw_pool (
                    pool_id INTEGER PRIMARY KEY,
                    sort_order INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    update_ts INTEGER DEFAULT 0
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS draw_group_state (
                    uid INTEGER NOT NULL,
                    pool_group TEXT NOT NULL,
                    since_ssr INTEGER DEFAULT 0,
                    since_sr INTEGER DEFAULT 0,
                    total_draws INTEGER DEFAULT 0,
                    up_id INTEGER DEFAULT 0,
                    is_up_guaranteed INTEGER DEFAULT 0,
                    last_pool_id INTEGER DEFAULT 0,
                    update_ts INTEGER DEFAULT 0,
                    PRIMARY KEY (uid, pool_group)
                )
            """)
            try:
                cols = [c["name"] for c in self.db.query("PRAGMA table_info(draw_group_state)")]
                if "since_sr" not in cols:
                    self.db.execute("ALTER TABLE draw_group_state ADD COLUMN since_sr INTEGER DEFAULT 0")
            except Exception as _e:
                logger.debug(f"[DrawService] 检查/补齐 draw_group_state.since_sr 列: {_e}")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS draw_record (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid INTEGER NOT NULL,
                    pool_id INTEGER NOT NULL,
                    pool_group TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_num INTEGER NOT NULL,
                    draw_ts INTEGER NOT NULL
                )
            """)
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_draw_record_uid_group ON draw_record(uid, pool_group, id DESC)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_draw_record_uid_pool ON draw_record(uid, pool_id, id DESC)")
            cnt = self.db.query("SELECT COUNT(*) AS c FROM active_draw_pool")
            if not cnt or cnt[0]["c"] == 0:
                now = int(time.time())
                for idx, pid in enumerate(DEFAULT_ACTIVE_POOLS):
                    self.db.execute(
                        "INSERT OR IGNORE INTO active_draw_pool (pool_id, sort_order, is_active, update_ts) VALUES (?, ?, 1, ?)",
                        (pid, idx + 1, now)
                    )
            # 启动自愈：将活动表 (activity) 与当前在架卡池 (active_draw_pool) 强一致对齐，清除历史脏数据
            self.sync_active_pool_activities()
        except Exception as e:
            logger.warning(f"[DrawService] 初始化 active_draw_pool / draw_record 数据表异常: {e}")

    # ==================== 卡池元数据与分类判定 ====================

    def load_pool_up_cfg(self):
        """加载 pool_up_config.json 静态配置"""
        if self._pool_up_cfg is None:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool_up_config.json")
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    self._pool_up_cfg = json.load(f)
            except Exception as e:
                logger.warning(f"[DrawService] 加载 pool_up_config.json 失败: {e}")
                self._pool_up_cfg = {}
        return self._pool_up_cfg

    def get_pool_group(self, pool_id, detail_json=""):
        """确定卡池所属的 4 大保底系列（系列内部共享继承保底计数与状态，系列之间相互独立）：
        1. hero_precision_90: 90 抽精准角色池（90 抽大保底必出 UP，绝不歪）
        2. hero_precision_70: 70 抽精准角色池（70 抽小保底 50% UP，歪了下次必出 UP）
        3. hero_standard_70: 70 抽常规/标准角色池（常驻角色、新手池、自选角色池）
        4. weapon_servant_70: 70 抽钥从池（沉睡之子、自选钥从池）
        """
        pid = int(pool_id)
        dj = {}
        if isinstance(detail_json, dict):
            dj = detail_json
        elif isinstance(detail_json, str) and detail_json.strip():
            try:
                dj = json.loads(detail_json)
            except Exception:
                dj = {}

        if not dj and self.db:
            prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pid,))
            if prow:
                try:
                    dj = json.loads(prow[0].get("detail_json") or "{}")
                except Exception:
                    dj = {}

        ptype = int(dj.get("pool_type") or 0)
        name = str(dj.get("name", ""))
        notes = str(dj.get("notes", ""))
        cost = dj.get("cost_ten_times") or dj.get("cost_once") or []
        cost_id = int(cost[0]) if cost else 0

        # 1. 钥从池（10002 等真正的钥从池）
        if ptype == 2 or cost_id == 19 or pid == 10002 or "钥从" in name or "沉睡之子" in name:
            return "weapon_servant_70"

        # 2. 90 抽精准角色池（陵光、星仪、311自选锚定等 90 精准/必中池）
        if (ptype == 6 or "pool_type=6" in notes or "(90)" in notes or "90" in name 
                or str(pid).startswith("50206") or str(pid).startswith("50306") or pid == 5030601):
            return "hero_precision_90"

        # 3. 70 抽精准角色池（活动限定角色 UP 池 / 311自选扩充池）
        if (ptype == 3 or cost_id == 38 or "UP" in name or "活动限定" in notes 
                or str(pid).startswith("50203") or str(pid).startswith("5020")
                or str(pid).startswith("50303") or str(pid).startswith("5030") or pid == 5030301):
            return "hero_precision_70"

        # 4. 70 抽常规/标准角色池（常态探测、新手、常驻自选角色池）
        return "hero_standard_70"

    def get_servant_sleeping_child(self, up_id):
        """根据客户端上报的神系ID或沉睡之子ID解析对应的 5 星沉睡之子 ID：
        - 2510000: 奥山 (race 1)
        - 2520000: 尼罗 (race 2)
        - 2530000: 真樱 (race 3)
        - 2540000: 圣树 (race 4)
        - 2550000: 众星 (race 5)
        - 2590000: 天垣 (race 9)
        """
        if not up_id:
            return 0
        u = int(up_id)
        if u in SLEEPING_CHILDREN:
            return u
        if 2500000 <= u <= 2600000:
            prefix = (u // 10000) * 10000
            if prefix in SLEEPING_CHILDREN:
                return prefix
        race_to_child = {
            1: 2510000, 2: 2520000, 3: 2530000,
            4: 2540000, 5: 2550000, 9: 2590000,
        }
        return race_to_child.get(u, 0)

    def check_pool_safety(self, pool_id, detail_json=""):
        """评估卡池的安全评级：
        Grade A: 现役已验证安全卡池（有完整 Prefab、立绘与动作资源，绝对安全不崩溃）
        Grade B: 通用多选一容器卡池（支持通用自选 UI 兜底展示）
        Grade C: 远古单UP限定卡池（可能因客户端版本缺失专属 Prefab 而引发黑屏，建议先验证）
        """
        pid = int(pool_id)
        dj = {}
        if isinstance(detail_json, dict):
            dj = detail_json
        elif isinstance(detail_json, str) and detail_json.strip():
            try:
                dj = json.loads(detail_json)
            except Exception:
                dj = {}

        if not dj and self.db:
            prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pid,))
            if prow:
                try:
                    dj = json.loads(prow[0].get("detail_json") or "{}")
                except Exception:
                    dj = {}

        # 检测是否消耗“限时精确探测凭证”
        cost_once = dj.get("cost_once") or []
        cost_act = dj.get("cost_once_activity_material") or []
        cost_id = int(cost_once[0]) if cost_once else 0
        is_limited_voucher = False
        if cost_act or (cost_id > 0 and cost_id not in (5, 19, 38)):
            is_limited_voucher = True

        # 安全评级判断
        verified_a_pools = {10000, 10001, 10002, 10003, 5020301, 5020302, 5020303, 5020601, 5030601, 5030301}
        if pid in verified_a_pools or str(pid).startswith("5020") or str(pid).startswith("5030"):
            grade = "A"
            desc = "现役已验证安全卡池（全套资源完备）"
        elif dj.get("pool_selected_type") in (2, 8, 9, 10) or dj.get("pool_type") in (1, 8) or pid in (4080101, 5000303, 4000303, 3000304, 5030601, 5030301):
            grade = "B"
            desc = "通用多选一容器卡池（支持通用 UI 兜底）"
        else:
            grade = "C"
            desc = "远古单UP限定卡池（可能缺失专属 Prefab，建议先验证）"

        return grade, is_limited_voucher, desc

    def is_selectable_pool(self, pool_id):
        """判定卡池是否为用户可自选 UP 目标的卡池（非单 UP 固定池）"""
        pid = int(pool_id)
        if pid in (10002, 4080101, 5000303, 4000303, 3000304, 5030601, 5030301):
            return True
        if self.db is not None:
            prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pid,))
            if prow:
                try:
                    dj = json.loads(prow[0].get("detail_json") or "{}")
                    if dj.get("pool_selected_type") in (2, 8, 9, 10):
                        return True
                    opt = dj.get("optional_detail")
                    if opt and isinstance(opt, list) and len(opt) > 1:
                        return True
                except Exception:
                    pass
        return False

    def get_pool_user_up(self, uid, pool_id):
        """获取特定用户在特定自选卡池中选定的 UP 目标 ID（按 pool_id 物理隔离）"""
        if self.db is None or not uid:
            return 0
        pool_id = int(pool_id)
        try:
            row = self.db.query("SELECT up_id FROM draw_state WHERE uid=? AND pool_id=?", (uid, pool_id))
            if row and row[0].get("up_id"):
                return int(row[0]["up_id"])
        except Exception:
            pass
        return 0

    # ==================== 概率与软保底动态计算引擎 ====================

    @staticmethod
    def calc_ssr_prob(pool_group, since_ssr):
        """计算当前第 since_ssr 抽的 SSR (S级修正者/5星钥从) 动态命中概率（含软保底平滑爬升）
        官方公示基础概率: 1.60%, 含保底综合出率: 2.40%
        1. 70 抽系列 (hero_precision_70, hero_standard_70, weapon_servant_70):
           - 1 ~ 65 抽: 1.60% 基础出率
           - 66 ~ 69 抽: 软保底线性阶梯爬升 (递增约 19.68% / 抽)
           - 70 抽: 100.0% 硬保底必出
           -> 模拟大样本收敛值严格稳定于 2.40% ~ 2.44%
        2. 90 抽系列 (hero_precision_90):
           - 1 ~ 80 抽: 1.60% 基础出率
           - 81 ~ 89 抽: 软保底线性阶梯爬升 (递增约 9.84% / 抽)
           - 90 抽: 100.0% 硬保底必出
        """
        if pool_group == "hero_precision_90":
            if since_ssr >= 90:
                return 1.0
            elif since_ssr <= 80:
                return BASE_RATE_SSR
            else:
                return BASE_RATE_SSR + (since_ssr - 80) * ((1.0 - BASE_RATE_SSR) / (90 - 80))
        else:
            if since_ssr >= 70:
                return 1.0
            elif since_ssr <= 65:
                return BASE_RATE_SSR
            else:
                return BASE_RATE_SSR + (since_ssr - 65) * ((1.0 - BASE_RATE_SSR) / (70 - 65))

    @staticmethod
    def calc_sr_prob(pool_group, since_sr):
        """计算当前第 since_sr 抽的 SR (A级修正者/4星钥从) 命中概率
        - 角色池基础出率: 7.80% (综合 14.00%)
        - 钥从池基础出率: 11.08% (综合 16.00%)
        - since_sr >= 10: 100.0% 保底必出
        """
        if since_sr >= 10:
            return 1.0
        if pool_group == "weapon_servant_70":
            return BASE_RATE_SR_WEAPON
        return BASE_RATE_SR_HERO

    # ==================== 保底与卡池状态存取 ====================

    def get_draw_state(self, uid, pool_group):
        """读取用户在特定卡池系列的保底与状态数据"""
        if self.db is None:
            return {"since_ssr": 0, "since_sr": 0, "total_draws": 0, "up_id": 0, "is_up_guaranteed": 0, "last_pool_id": 0}
        row = self.db.query("SELECT * FROM draw_group_state WHERE uid=? AND pool_group=?", (uid, pool_group))
        if row:
            r = row[0]
            return {
                "since_ssr": int(r.get("since_ssr") or 0),
                "since_sr": int(r.get("since_sr") or 0),
                "total_draws": int(r.get("total_draws") or 0),
                "up_id": int(r.get("up_id") or 0),
                "is_up_guaranteed": int(r.get("is_up_guaranteed") or 0),
                "last_pool_id": int(r.get("last_pool_id") or 0)
            }
        return {"since_ssr": 0, "since_sr": 0, "total_draws": 0, "up_id": 0, "is_up_guaranteed": 0, "last_pool_id": 0}

    def save_draw_state(self, uid, pool_group, state, pool_id=0):
        """保存用户的保底与卡池状态"""
        if self.db is None:
            return
        now = int(time.time())
        self.db.execute(
            "INSERT INTO draw_group_state (uid, pool_group, since_ssr, since_sr, total_draws, up_id, is_up_guaranteed, last_pool_id, update_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid, pool_group) DO UPDATE SET "
            "since_ssr=excluded.since_ssr, since_sr=excluded.since_sr, total_draws=excluded.total_draws, "
            "up_id=excluded.up_id, is_up_guaranteed=excluded.is_up_guaranteed, "
            "last_pool_id=excluded.last_pool_id, update_ts=excluded.update_ts",
            (uid, pool_group, int(state["since_ssr"]), int(state.get("since_sr", 0)), int(state["total_draws"]),
             int(state["up_id"]), int(state["is_up_guaranteed"]), int(pool_id or state.get("last_pool_id", 0)), now)
        )
        if pool_id:
            try:
                self.db.execute(
                    "INSERT INTO draw_state (uid, pool_id, since_ssr, since_sr, total_draws, up_id, is_up_guaranteed, pool_group) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid, pool_id) DO UPDATE SET "
                    "since_ssr=excluded.since_ssr, since_sr=excluded.since_sr, total_draws=excluded.total_draws, "
                    "up_id=excluded.up_id, is_up_guaranteed=excluded.is_up_guaranteed, pool_group=excluded.pool_group",
                    (uid, int(pool_id), int(state["since_ssr"]), int(state.get("since_sr", 0)), int(state["total_draws"]),
                     int(state["up_id"]), int(state["is_up_guaranteed"]), pool_group)
                )
            except Exception:
                pass

    def set_pool_up(self, uid, pool_id, up_id):
        """设置自选 UP 目标（支持角色自选与钥从神系映射，按 pool_id 物理隔离，杜绝串池）"""
        pool_id = int(pool_id)
        up_id = int(up_id)
        prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pool_id,))
        dj = prow[0]["detail_json"] if prow else ""
        pool_group = self.get_pool_group(pool_id, dj)

        if pool_group == "weapon_servant_70":
            child_id = self.get_servant_sleeping_child(up_id)
            if child_id:
                up_id = child_id

        # 1. 独立更新 draw_state 表（按 uid, pool_id 物理隔离，保障每个自选池独立保存目标）
        now = int(time.time())
        try:
            self.db.execute(
                "INSERT INTO draw_state (uid, pool_id, since_ssr, total_draws, up_id, is_up_guaranteed, pool_group) "
                "VALUES (?, ?, 0, 0, ?, 0, ?) "
                "ON CONFLICT(uid, pool_id) DO UPDATE SET up_id=excluded.up_id",
                (uid, pool_id, up_id, pool_group)
            )
        except Exception as e:
            logger.warning(f"[DrawService] 写入 draw_state 异常: {e}")

        # 2. 保留系列保底进度流转
        state = self.get_draw_state(uid, pool_group)
        state["last_pool_id"] = pool_id
        state["up_id"] = up_id
        self.save_draw_state(uid, pool_group, state, pool_id)
        return {"pool_id": pool_id, "pool_group": pool_group, "up_id": up_id}

    def calc_draw_rebate(self, item_id):
        """根据产出物品（角色/钥从）基础稀有度计算返还的共鸣辉芒(item_id=36)数量
        官方配置来源: GameSetting.currency_for_draw.value (gamesetting.lua:76)
        稀有度 5 (S角色 / 5星钥从) -> 100
        稀有度 4 (A角色 / 4星钥从) -> 20
        稀有度 3 (B角色 / 3星钥从) -> 10
        """
        item_id = int(item_id)
        # 1. 钥从判定 (25xxxxx: 5星, 24xxxxx: 4星, 其余: 3星)
        if item_id >= 2000000:
            if item_id >= 2500000:
                return 5, 100
            elif item_id >= 2400000:
                return 4, 20
            else:
                return 3, 10
        # 2. 角色判定
        if item_id in (STANDARD_S_HEROES + LIMITED_S_HEROES):
            return 5, 100
        elif item_id in ALL_A_HEROES:
            return 4, 20
        else:
            return 3, 10

    def query_info(self, ctx, uid, pool_id):
        """查询卡池保底信息与抽卡历史记录（cs_16012）"""
        pool_id = int(pool_id)
        prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pool_id,))
        dj = prow[0]["detail_json"] if prow else ""
        pool_group = self.get_pool_group(pool_id, dj)
        state = self.get_draw_state(uid, pool_group)

        # 查询同系列最近抽卡历史（最多 100 条，最新在前）
        draw_records = []
        if self.db is not None:
            try:
                # 客户端接收后会执行反转遍历 (#list - i + 1)，因此服务端必须下发正序 (id ASC)
                # 这样客户端反转后，最新的记录才会排在第 1 页第 1 行
                rows = self.db.query(
                    """SELECT item_id, item_num, draw_ts FROM (
                        SELECT id, item_id, item_num, draw_ts FROM draw_record
                        WHERE uid=? AND pool_group=?
                        ORDER BY id DESC LIMIT 100
                    ) ORDER BY id ASC""",
                    (uid, pool_group)
                )
                for r in rows:
                    draw_records.append({
                        "item": {"id": int(r["item_id"]), "num": int(r["item_num"])},
                        "draw_timestamp": int(r["draw_ts"])
                    })
            except Exception as e:
                logger.warning(f"[DrawService] 查询抽卡记录异常 uid={uid} group={pool_group}: {e}")

        return {
            "pool_id": pool_id,
            "pool_group": pool_group,
            "since_ssr": state["since_ssr"],
            "draw_record_list": draw_records
        }

    def validate_draw(self, uid, pool_id, dtype=10):
        """抽卡前置验资与合法性校验"""
        pool_id = int(pool_id)
        dtype = int(dtype)
        pool = self.db.query("SELECT * FROM draw_pool WHERE pool_id=?", (pool_id,))
        if not pool:
            return False, f"抽卡池 {pool_id} 不存在"
        try:
            dj = json.loads(pool[0].get("detail_json") or "{}")
        except Exception:
            dj = {}
        pair = dj.get("cost_ten_times") if dtype == 10 else dj.get("cost_once")
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            cost_id, cost_num = int(pair[0]), int(pair[1])
        else:
            cost_id = int(dj.get("cost_id", 38))
            cost_num = int(dj.get("cost_ten", 10) if dtype == 10 else dj.get("cost_once", 1))
        cnt = self._item_balance(uid, cost_id)
        if cnt < cost_num:
            return False, f"抽卡道具不足（需 {cost_num}，持有 {cnt}）"
        return True, ""

    # ==================== cs_16018 → sc_16019 卡池详情组装 ====================

    def get_pool_details(self, pool_id, uid=None):
        """构建 cs_16018 请求对应的卡池详情（sc_16019 pool_details_net_rec）
        规则（老大定稿算法 & 客户端官方配置对齐）：
        1. 必须下发 DrawItemCfg 的条目 ID（draw_item_id），绝不能下发原生物品 ID！
           客户端 drawinfopopview.lua / drawinfocommonitem.lua 依赖 DrawItemCfg[id]，
           下发原生物品 ID 会导致 DrawItemCfg[id] nil 报错、%s 未替换以及 loading 遮罩死锁卡转圈。
        2. 70精准角色池：
           - 歪卡列表 s_other_item 严格排除 33 位常驻老 S 角色！
           - 歪卡列表 = 26 位限定 S 角色 剔除 本期 UP S 角色！
           - S UP 率 50 (客户端模板直接拼接 '%' 字符展示为 '50%')
        3. 90精准角色池：
           - S UP 率 100 (展示为 '100%' 必出 UP，绝不歪)，s_other_item = []
        4. 常态/标准角色池 (10001 / 4080101)：
           - s_other_item = 33 位常驻标准 S 角色（剔除玩家自选目标）
           - pool 4080101 针对 DRAW_POOL_DESC_TEMP_11，s_up_item 默认指向 1066 大国主（DrawItemCfg 1001）
        5. 钥从池 (10002)：
           - s_up_item = [已选沉睡之子 DrawItemCfg ID]，s_other_item = [其余沉睡之子 DrawItemCfg ID]
           - a_other_item = [全量4星钥从 DrawItemCfg ID: 5007~5018]
           - b_item = [全量3星钥从 DrawItemCfg ID: 5019~5024]
        6. b_item（角色池）：
           - [4001, 4002, 5019, 5020, 5021, 5022, 5023, 5024]
           - 4001/4002 (pool_id 301) 归入 B 级修正者；5019~5024 (pool_id 1003) 归入 3 星钥从。
        """
        pool_id = int(pool_id)
        prow = self.db.query("SELECT * FROM draw_pool WHERE pool_id=?", (pool_id,))
        dj = {}
        pool_name = ""
        if prow:
            pool_name = prow[0].get("name") or ""
            try:
                dj = json.loads(prow[0].get("detail_json") or "{}")
            except Exception:
                dj = {}

        pool_group = self.get_pool_group(pool_id, dj)
        pool_cfg_all = self.load_pool_up_cfg()
        pcfg = pool_cfg_all.get(str(pool_id)) or pool_cfg_all.get(int(pool_id)) or {}

        official_up_s = int(pcfg.get("up_s") or 0)
        official_up_a = [int(x) for x in pcfg.get("up_a", []) if int(x) in ALL_A_HEROES]

        # 确定本期 UP 角色
        if not pool_name:
            pool_name = str(pcfg.get("pool_name") or dj.get("name", ""))
        default_up_s = official_up_s or int(dj.get("up_hero") or POOL_UP_HERO_MAP.get(pool_name) or 0)

        # 核心隔离：仅自选类卡池（5000303、4080101、10002 等）才展示玩家自选的 UP 角色；
        # 单 UP 固定池强制展示该卡池固有的 default_up_s，绝不被其他自选池串扰！
        if self.is_selectable_pool(pool_id):
            user_up_id = self.get_pool_user_up(uid, pool_id)
            target_up_s = user_up_id if user_up_id else default_up_s
        else:
            target_up_s = default_up_s

        # 随行 A 级 UP（客户端 DescModule 仅当恰好有 3 位 A 级 UP 时才支持展开，否则保持空列表走通用描述分支）
        up_a_list = official_up_a or dj.get("up_a_heroes") or []
        if not isinstance(up_a_list, list):
            up_a_list = [up_a_list] if up_a_list else []
        up_a_list = [int(x) for x in up_a_list if int(x) in DRAW_ITEM_A]

        # 根据系列构造详情（全量转换为 DrawItemCfg 条目 ID）
        if pool_group == "hero_precision_90":
            s_up_probability = 100
            target_hero = target_up_s if target_up_s else 1083
            target_did = DRAW_ITEM_LTD_S.get(target_hero) or DRAW_ITEM_STD_S.get(target_hero, 2050)
            s_up_item = [target_did]
            s_other_item = []  # 90抽绝不歪
            a_up_probability = 0
            a_up_item = []
            a_other_item = list(ALL_A_DRAW_ITEMS)
            b_item = list(BASE_B_DRAW_ITEMS)
        elif pool_group == "hero_precision_70":
            s_up_probability = 50  # 50% 出本期 UP
            target_hero = target_up_s if target_up_s else 1083
            target_did = DRAW_ITEM_LTD_S.get(target_hero) or DRAW_ITEM_STD_S.get(target_hero, 2050)
            s_up_item = [target_did]
            # 核心算法：精准70歪卡池严格不包括33位常驻S，只能歪其他限定S！
            s_other_item = [did for h, did in DRAW_ITEM_LTD_S.items() if did != target_did]
            a_up_probability = 0
            a_up_item = []
            a_other_item = list(ALL_A_DRAW_ITEMS)
            b_item = list(BASE_B_DRAW_ITEMS)
        elif pool_group == "weapon_servant_70":
            target_child = self.get_servant_sleeping_child(target_up_s) or 2590000
            s_up_probability = 100  # 出金100%命中当前自选神系
            target_child_did = DRAW_ITEM_SERVANT_5.get(target_child, 5006)
            s_up_item = [target_child_did]
            s_other_item = [did for c, did in DRAW_ITEM_SERVANT_5.items() if did != target_child_did]
            a_up_probability = 0
            a_up_item = []
            a_other_item = list(SERVANT_4_DRAW_ITEMS)
            b_item = list(SERVANT_B_DRAW_ITEMS)
        else:
            # hero_standard_70 常驻/标准池
            if target_up_s and target_up_s in DRAW_ITEM_STD_S:
                target_did = DRAW_ITEM_STD_S[target_up_s]
                s_up_probability = 100
                s_up_item = [target_did]
                s_other_item = [did for h, did in DRAW_ITEM_STD_S.items() if did != target_did]
            else:
                default_std_did = DRAW_ITEM_STD_S.get(1066, 1001)
                if pool_id == 4080101:
                    # 自选常驻探测：DRAW_POOL_DESC_TEMP_11 客户端要求 s_up_item[1] 存在于 DrawItemCfg
                    s_up_probability = 100
                    s_up_item = [default_std_did]
                    s_other_item = [did for h, did in DRAW_ITEM_STD_S.items() if did != default_did]
                else:
                    s_up_probability = 0
                    s_up_item = []
                    s_other_item = list(DRAW_ITEM_STD_S.values())
            a_up_probability = 0
            a_up_item = []
            a_other_item = list(ALL_A_DRAW_ITEMS)
            b_item = list(BASE_B_DRAW_ITEMS)

        return {
            "result": 0,
            "pool_details": {
                "s_up_probability": s_up_probability,
                "s_up_item": s_up_item,
                "s_other_item": s_other_item,
                "a_up_probability": a_up_probability,
                "a_up_item": a_up_item,
                "a_other_item": a_other_item,
                "b_item": b_item
            }
        }

    # ==================== 动态 sc_16015 组装与卡池管理 ====================

    def get_active_pools(self, uid=None, res_version=None):
        """获取当前开放上架的卡池列表（返回卡池 ID 列表，支持按客户端版本过滤）"""
        if self.db is None:
            pids = list(DEFAULT_ACTIVE_POOLS)
        else:
            rows = self.db.query("SELECT pool_id FROM active_draw_pool WHERE is_active=1 ORDER BY sort_order, pool_id")
            if rows:
                pids = [r["pool_id"] for r in rows]
            else:
                pids = list(DEFAULT_ACTIVE_POOLS)

        if res_version is not None and str(res_version) == "229":
            cfg = self.load_pool_activity_cfg()
            # 过滤 311/Theme 44 专属卡池（5030601/5030301 等），229 客户端不存在该卡池配置，下发会导致 DrawData:ConvertUpId 崩溃
            pids = [
                pid for pid in pids
                if not (str(pid).startswith("503") or cfg.get(str(pid), {}).get("theme", 0) >= 44)
            ]
        return pids

    def set_active_pools(self, pool_ids):
        """设置上架的卡池列表（覆盖现有活跃池），并联动激活对应卡池活动"""
        if self.db is None:
            return False, "数据库不可用"
        now = int(time.time())
        try:
            self.db.execute("UPDATE active_draw_pool SET is_active=0")
            for idx, pid in enumerate(pool_ids):
                self.db.execute(
                    "INSERT INTO active_draw_pool (pool_id, sort_order, is_active, update_ts) VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(pool_id) DO UPDATE SET is_active=1, sort_order=excluded.sort_order, update_ts=excluded.update_ts",
                    (int(pid), idx + 1, now)
                )
            # 联动同步激活在架卡池对应的活动，解除客户端时间锁
            self.sync_active_pool_activities()
            return True, f"成功上架 {len(pool_ids)} 个卡池"
        except Exception as e:
            return False, str(e)

    def load_pool_activity_cfg(self):
        """加载 pool_to_activity_cfg.json 静态配置"""
        if getattr(self, "_pool_activity_cfg", None) is None:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool_to_activity_cfg.json")
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    self._pool_activity_cfg = json.load(f)
            except Exception as e:
                logger.warning(f"[DrawService] 加载 pool_to_activity_cfg.json 失败: {e}")
                self._pool_activity_cfg = {}
        return self._pool_activity_cfg

    def get_active_activity_entries(self, uid=None, res_version=None):
        """获取当前活跃在架卡池对应的活动条目列表，供 sc_11001 动态下发，解除卡池活动时间锁与置灰。"""
        cfg = self.load_pool_activity_cfg()
        pids = self.get_active_pools(uid=uid, res_version=res_version)
        entries = []
        seen_acts = set()
        for pid in pids:
            item = cfg.get(str(pid))
            if item and item.get("activity_id"):
                aid = int(item["activity_id"])
                if aid not in seen_acts:
                    seen_acts.add(aid)
                    entries.append({
                        "activity_id": aid,
                        "name": item.get("name", ""),
                        "theme": int(item.get("theme", 0)),
                        "template": int(item.get("template", 3)),
                        "start_time": 0,
                        "stop_time": 2440962000,
                        "state": 1,
                        "sub_activity_id_list": []
                    })
        return entries

    def sync_active_pool_activities(self, uid=None):
        """将当前活跃卡池对应的活动同步至 activity 表，保障时间窗口激活；并将已下架卡池的活动置为 0，防止主界面 Banner 轮播噪点与 229 白屏"""
        if self.db is None:
            return
        cfg = self.load_pool_activity_cfg()
        all_pool_aids = {int(item["activity_id"]) for item in cfg.values() if item.get("activity_id")}

        uids = [uid] if uid is not None else []
        if not uids:
            try:
                rows = self.db.query("SELECT DISTINCT uid FROM activity")
                uids = [r["uid"] for r in rows if r.get("uid")]
            except Exception:
                pass
            if not uids:
                uids = [2174928301]

        for u in uids:
            entries = self.get_active_activity_entries(u)
            active_aids = {ent["activity_id"] for ent in entries}

            for ent in entries:
                try:
                    self.db.execute(
                        "INSERT INTO activity (uid, activity_id, name, start_time, stop_time, state, theme, template, sub_activity_id_list, update_ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 0) "
                        "ON CONFLICT(uid, activity_id) DO UPDATE SET state=1, stop_time=2440962000",
                        (u, ent["activity_id"], ent["name"], ent["start_time"], ent["stop_time"], ent["state"], ent["theme"], ent["template"])
                    )
                except Exception as e:
                    logger.debug(f"[DrawService] 同步卡池活动到 activity 表异常: {e}")

            inactive_aids = all_pool_aids - active_aids
            if inactive_aids:
                try:
                    placeholders = ",".join("?" * len(inactive_aids))
                    self.db.execute(
                        f"UPDATE activity SET state=0 WHERE uid=? AND activity_id IN ({placeholders})",
                        (u, *inactive_aids)
                    )
                except Exception as e:
                    logger.debug(f"[DrawService] 下架卡池活动状态置零异常: {e}")

    def sanitize_pools_for_version(self, target_version):
        """
        全链路版本守卫：当目标客户端资源版本为 229 时，执行幂等清洗，
        强制将 311 独有卡池（503 开头及 Theme >= 44）从 active_draw_pool 中下架（is_active=0），
        并联动同步关闭 activity 表中对应的活动 Banner，防止老客户端在登录洪流与探测大厅崩溃。
        返回被强制下架的卡池数量。
        """
        if self.db is None:
            return 0
        if str(target_version) != "229":
            return 0

        cfg = self.load_pool_activity_cfg()
        disabled_count = 0
        now = int(time.time())
        try:
            rows = self.db.query("SELECT pool_id FROM active_draw_pool WHERE is_active=1")
            active_pids = [r["pool_id"] for r in rows] if rows else []
            for pid in active_pids:
                if str(pid).startswith("503") or cfg.get(str(pid), {}).get("theme", 0) >= 44:
                    self.db.execute("UPDATE active_draw_pool SET is_active=0, update_ts=? WHERE pool_id=?", (now, pid))
                    disabled_count += 1
            if disabled_count > 0:
                self.sync_active_pool_activities()
                logger.info(f"[DrawService] 触发 Build 229 版本守卫：已强制关停 {disabled_count} 个 311 专属卡池并下架关联活动。")
        except Exception as e:
            logger.warning(f"[DrawService] sanitize_pools_for_version 异常: {e}")
        return disabled_count

    def apply_preset(self, preset_key):
        """应用卡池预设方案模板"""
        if preset_key not in POOL_PRESETS:
            return False, f"未知的卡池预设 '{preset_key}'，可用: {list(POOL_PRESETS.keys())}"
        p = POOL_PRESETS[preset_key]
        ok, msg = self.set_active_pools(p["pool_ids"])
        if ok:
            try:
                import res_version_manager
                cur_ver = res_version_manager.get_current_version()
                if str(cur_ver) == "229":
                    self.sanitize_pools_for_version("229")
            except Exception:
                pass
            return True, f"已切换至预设「{p['name']}」: {msg}"
        return False, msg

    def build_16015_payload(self, uid, res_version=None):
        """为特定 UID 动态构建下发给客户端的 sc_16015 协议载荷"""
        if res_version is None:
            try:
                import res_version_manager
                res_version = res_version_manager.get_current_version()
            except Exception:
                res_version = "229"
        active_ids = self.get_active_pools(uid, res_version=res_version)
        draw_info_list = []

        for pid in active_ids:
            prow = self.db.query("SELECT detail_json FROM draw_pool WHERE pool_id=?", (pid,))
            dj = prow[0]["detail_json"] if prow else ""
            pgroup = self.get_pool_group(pid, dj)
            st = self.get_draw_state(uid, pgroup)
            
            # 单 UP 固定卡池在 sc_16015 中 up 恒为 0（对齐抓包，客户端自动根据本地配置展示本命角色）
            # 自选卡池在 sc_16015 中 up 下发该自选池自身选中的 up_id（按 pool_id 隔离）
            if self.is_selectable_pool(pid):
                pool_up_val = self.get_pool_user_up(uid, pid)
            else:
                pool_up_val = 0

            draw_info_list.append({
                "id": pid,
                "ssr_draw_times": st.get("since_ssr", 0),
                "up": pool_up_val,
                "up_times": 0,
                "is_new": 0
            })

        return {
            "draw_info_list": draw_info_list,
            "first_ssr_draw_flag": False,
            "today_draw_times": 0,
            "newbie_choose_draw_flag": True
        }

    # ==================== 抽卡核心业务调度 ====================

    def draw(self, ctx, uid, pool_id, dtype=10):
        """执行单抽或十连抽卡核心逻辑，返回封装好的全部下行帧"""
        pool_id = int(pool_id)
        dtype = int(dtype)
        pool = self.db.query("SELECT * FROM draw_pool WHERE pool_id=?", (pool_id,))
        if not pool:
            raise ValueError(f"抽卡池 {pool_id} 不存在")

        dj = {}
        try:
            dj = json.loads(pool[0].get("detail_json") or "{}")
        except Exception:
            dj = {}

        pair = dj.get("cost_ten_times") if dtype == 10 else dj.get("cost_once")
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            cost_id, cost_num = int(pair[0]), int(pair[1])
        else:
            cost_id = int(dj.get("cost_id", 38))
            cost_num = int(dj.get("cost_ten", 10) if dtype == 10 else dj.get("cost_once", 1))

        # 验资
        cnt = self._item_balance(uid, cost_id)
        if cnt < cost_num:
            raise ValueError(f"抽卡道具不足（需 {cost_num}，持有 {cnt}）")

        # 扣券
        self._item_deduct(uid, cost_id, cost_num)
        if hasattr(ctx, "touched_items") and ctx.touched_items is not None:
            ctx.touched_items.add(cost_id)

        pool_group = self.get_pool_group(pool_id, dj)
        state = self.get_draw_state(uid, pool_group)
        since = state["since_ssr"]
        since_sr = state.get("since_sr", 0)
        total_draws = state["total_draws"]
        is_up_guaranteed = state["is_up_guaranteed"]
        up_id = state["up_id"]

        is_keyong_pool = (pool_group == "weapon_servant_70")
        pity_threshold = 90 if (pool_group == "hero_precision_90") else 70

        # 获取 UP S 角色
        pool_cfg_all = self.load_pool_up_cfg()
        pcfg = pool_cfg_all.get(str(pool_id)) or pool_cfg_all.get(int(pool_id)) or {}
        official_up_s = int(pcfg.get("up_s") or 0)
        official_up_a = [int(x) for x in pcfg.get("up_a", []) if int(x) in ALL_A_HEROES]

        pool_name = str(pcfg.get("pool_name") or dj.get("name", ""))
        default_up_s = official_up_s or int(dj.get("up_hero") or POOL_UP_HERO_MAP.get(pool_name) or 0)

        # 核心隔离：仅自选池采用玩家自选的 UP 角色；单 UP 池强制命中该池固有的 default_up_s（出本命角色，绝不串池）
        if self.is_selectable_pool(pool_id):
            user_up = self.get_pool_user_up(uid, pool_id)
            target_up = user_up if user_up else default_up_s
        else:
            target_up = default_up_s

        up_a_list = official_up_a or dj.get("up_a_heroes") or []
        if not isinstance(up_a_list, list):
            up_a_list = [up_a_list] if up_a_list else []
        up_a_list = [int(x) for x in up_a_list if int(x) in ALL_A_HEROES]

        n = 10 if dtype == 10 else 1

        # 核心产出容器
        rec_items = []            # 给 sc_16011.item
        items_for_inventory = []  # 移交材料管理器的标品资产 [(item_id, count), ...]
        records_to_insert = []    # 待存入 draw_record 表 [(uid, pool_id, pool_group, item_id, item_num, draw_ts), ...]
        total_huimang = 0         # 累加附属代币（共鸣辉芒 item_id=36）
        new_heroes, dups, keyongs = 0, 0, 0
        got_ssr = False
        now = int(time.time())

        for idx in range(n):
            since += 1
            since_sr += 1
            total_draws += 1

            prob_ssr = self.calc_ssr_prob(pool_group, since)
            is_ssr = (random.random() < prob_ssr) or (since >= pity_threshold)

            if is_ssr:
                since = 0
                got_ssr = True
                if is_keyong_pool:
                    target_child = self.get_servant_sleeping_child(up_id)
                    selected_child = target_child if target_child else random.choice(SLEEPING_CHILDREN)
                    
                    _, rebate = self.calc_draw_rebate(selected_child)
                    total_huimang += rebate
                    keyongs += 1
                    
                    items_for_inventory.append((selected_child, 1))
                    records_to_insert.append((uid, pool_id, pool_group, selected_child, 1, now))
                    rec_items.append({"id": selected_child, "num": 1})
                else:
                    if pool_group == "hero_precision_90":
                        chosen_hero = target_up if target_up else random.choice(LIMITED_S_HEROES)
                    elif pool_group == "hero_precision_70":
                        if is_up_guaranteed or random.random() < 0.5:
                            chosen_hero = target_up if target_up else random.choice(LIMITED_S_HEROES)
                            is_up_guaranteed = 0
                        else:
                            off_pool = [h for h in LIMITED_S_HEROES if h != target_up] or STANDARD_S_HEROES
                            chosen_hero = random.choice(off_pool)
                            is_up_guaranteed = 1
                    else:
                        if target_up and target_up in STANDARD_S_HEROES:
                            chosen_hero = target_up
                        else:
                            chosen_hero = random.choice(STANDARD_S_HEROES)

                    _, rebate = self.calc_draw_rebate(chosen_hero)
                    total_huimang += rebate
                    records_to_insert.append((uid, pool_id, pool_group, chosen_hero, 1, now))

                    # 角色解锁边界判定：已拥有则转碎片交库管，未拥有则 DrawService 自管入库
                    exists = self.db.query("SELECT id FROM hero WHERE uid=? AND id=?", (uid, chosen_hero))
                    if not exists:
                        new_heroes += 1
                        self.db.execute(
                            "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) VALUES (?, ?, 1, 300, 0, '[]', 1, ?)",
                            (uid, chosen_hero, now)
                        )
                        try:
                            import event_bus
                            event_bus.bus.emit(event_bus.Events.HERO_UNLOCK, ctx, uid, hero_id=chosen_hero)
                        except Exception:
                            pass
                        rec_items.append({"id": chosen_hero, "num": 1})
                    else:
                        dups += 1
                        piece_id = 10000 + chosen_hero
                        piece_add = 30
                        items_for_inventory.append((piece_id, piece_add))
                        rec_items.append({
                            "id": piece_id,
                            "num": piece_add,
                            "convert_from": {"id": chosen_hero, "num": 1}
                        })
            else:
                prob_sr = self.calc_sr_prob(pool_group, since_sr)
                is_sr = (since_sr >= 10) or (random.random() < prob_sr)
                if is_sr:
                    since_sr = 0
                    if is_keyong_pool:
                        kid = random.choice(SERVANT_4_LIST)
                        _, rebate = self.calc_draw_rebate(kid)
                        total_huimang += rebate
                        keyongs += 1
                        items_for_inventory.append((kid, 1))
                        records_to_insert.append((uid, pool_id, pool_group, kid, 1, now))
                        rec_items.append({"id": kid, "num": 1})
                    else:
                        if up_a_list and random.random() < 0.5:
                            chosen_hero = random.choice(up_a_list)
                        else:
                            chosen_hero = random.choice(ALL_A_HEROES) if ALL_A_HEROES else 1011

                        _, rebate = self.calc_draw_rebate(chosen_hero)
                        total_huimang += rebate
                        records_to_insert.append((uid, pool_id, pool_group, chosen_hero, 1, now))

                        exists = self.db.query("SELECT id FROM hero WHERE uid=? AND id=?", (uid, chosen_hero))
                        if not exists:
                            new_heroes += 1
                            self.db.execute(
                                "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) VALUES (?, ?, 1, 200, 0, '[]', 1, ?)",
                                (uid, chosen_hero, now)
                            )
                            try:
                                import event_bus
                                event_bus.bus.emit(event_bus.Events.HERO_UNLOCK, ctx, uid, hero_id=chosen_hero)
                            except Exception:
                                pass
                            rec_items.append({"id": chosen_hero, "num": 1})
                        else:
                            dups += 1
                            piece_id = 10000 + chosen_hero
                            piece_add = 18
                            items_for_inventory.append((piece_id, piece_add))
                            rec_items.append({
                                "id": piece_id,
                                "num": piece_add,
                                "convert_from": {"id": chosen_hero, "num": 1}
                            })
                else:
                    # 低星基础副产物 (R级)
                    if is_keyong_pool:
                        kid = random.choice(SERVANT_3_LIST)
                        _, rebate = self.calc_draw_rebate(kid)
                        total_huimang += rebate
                        keyongs += 1
                        items_for_inventory.append((kid, 1))
                        records_to_insert.append((uid, pool_id, pool_group, kid, 1, now))
                        rec_items.append({"id": kid, "num": 1})
                    else:
                        # 角色池副产物：B 级角色 (6.00%) vs 3 星通用钥从 (84.60%)
                        if random.random() < (BASE_RATE_R_HERO / (BASE_RATE_R_HERO + BASE_RATE_R_WEAPON)):
                            chosen_b = random.choice(HERO_B_LIST)
                            _, rebate = self.calc_draw_rebate(chosen_b)
                            total_huimang += rebate
                            records_to_insert.append((uid, pool_id, pool_group, chosen_b, 1, now))

                            exists = self.db.query("SELECT id FROM hero WHERE uid=? AND id=?", (uid, chosen_b))
                            if not exists:
                                new_heroes += 1
                                self.db.execute(
                                    "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) VALUES (?, ?, 1, 100, 0, '[]', 1, ?)",
                                    (uid, chosen_b, now)
                                )
                                try:
                                    import event_bus
                                    event_bus.bus.emit(event_bus.Events.HERO_UNLOCK, ctx, uid, hero_id=chosen_b)
                                except Exception:
                                    pass
                                rec_items.append({"id": chosen_b, "num": 1})
                            else:
                                dups += 1
                                piece_id = 10000 + chosen_b
                                piece_add = 10
                                items_for_inventory.append((piece_id, piece_add))
                                rec_items.append({
                                    "id": piece_id,
                                    "num": piece_add,
                                    "convert_from": {"id": chosen_b, "num": 1}
                                })
                        else:
                            kid = random.choice(SERVANT_3_LIST)
                            _, rebate = self.calc_draw_rebate(kid)
                            total_huimang += rebate
                            keyongs += 1
                            items_for_inventory.append((kid, 1))
                            records_to_insert.append((uid, pool_id, pool_group, kid, 1, now))
                            rec_items.append({"id": kid, "num": 1})

        # 保存保底进度
        state["since_ssr"] = since
        state["since_sr"] = since_sr
        state["total_draws"] = total_draws
        state["is_up_guaranteed"] = is_up_guaranteed
        self.save_draw_state(uid, pool_group, state, pool_id)

        # 标品资产（重复碎片、钥从、附属辉芒）统一移交材料管理器 InventoryService 入库
        if total_huimang > 0:
            items_for_inventory.append((36, total_huimang))

        if items_for_inventory:
            try:
                import inventory_service
                inventory_service.grant_items(ctx, uid, items_for_inventory, source="gacha")
            except Exception as e:
                logger.error(f"[DrawService] 移交 InventoryService 入库失败: {e}")

        # 持久化出卡次序与历史记录（保留最近 100 条）
        if self.db is not None and records_to_insert:
            try:
                for rec in records_to_insert:
                    self.db.execute(
                        "INSERT INTO draw_record (uid, pool_id, pool_group, item_id, item_num, draw_ts) VALUES (?, ?, ?, ?, ?, ?)",
                        rec
                    )
                self.db.execute("""
                    DELETE FROM draw_record WHERE id IN (
                        SELECT id FROM draw_record WHERE uid=? AND pool_group=? ORDER BY id DESC LIMIT -1 OFFSET 100
                    )
                """, (uid, pool_group))
            except Exception as e:
                logger.warning(f"[DrawService] 保存 draw_record 异常: {e}")

        # 广播抽卡业务事件（供日常任务等消费）
        try:
            import event_bus
            event_bus.bus.emit(
                event_bus.Events.DRAW_PERFORM,
                ctx,
                uid,
                pool_id=pool_id,
                draw_count=n,
                pool_group=pool_group,
                items=rec_items
            )
            if n == 10:
                event_bus.bus.emit(
                    event_bus.Events.DRAW_TEN_RESULT,
                    ctx,
                    uid,
                    items=rec_items,
                    pool_id=pool_id
                )
        except Exception:
            pass

        ctx.log(f"[DrawService] 抽卡 pool={pool_id} (系列:{pool_group}) ×{cost_num}券"
                f" 出金={got_ssr} 新英雄{new_heroes} 重复转碎片{dups}"
                f" 钥从{keyongs} 附属辉芒={total_huimang} 保底计数={since}")

        return {
            "result": 0,
            "item": rec_items,
            "first_ssr_draw_flag": False,
            "newbie_choose_draw_flag": True,
            "ssr_draw_times": since,
            "pool_id": pool_id,
            "pool_group": pool_group,
            "total_huimang": total_huimang,
            "got_ssr": got_ssr,
            "new_heroes": new_heroes,
            "dups": dups,
            "keyongs": keyongs
        }

    # ==================== 内部资产与数据操作辅助 ====================

    def _item_balance(self, uid, item_id):
        if self.db is None:
            return 0
        r = self.db.query("SELECT num FROM currency WHERE uid=? AND id=?", (uid, item_id))
        if r:
            return int(r[0]["num"] or 0)
        r2 = self.db.query("SELECT num FROM material WHERE uid=? AND id=?", (uid, item_id))
        return int(r2[0]["num"] or 0) if r2 else 0

    def _item_deduct(self, uid, item_id, count):
        if self.db is None:
            return
        cur = self._item_balance(uid, item_id)
        new_cnt = max(0, cur - int(count))
        self.db.execute("UPDATE currency SET num=? WHERE uid=? AND id=?", (new_cnt, uid, item_id))
        self.db.execute("UPDATE material SET num=? WHERE uid=? AND id=?", (new_cnt, uid, item_id))

    def _item_add(self, uid, item_id, count):
        if self.db is None:
            return
        cur = self._item_balance(uid, item_id)
        new_cnt = cur + int(count)
        self.db.execute(
            "INSERT INTO currency (uid, id, num) VALUES (?, ?, ?) ON CONFLICT(uid, id) DO UPDATE SET num=?",
            (uid, item_id, new_cnt, new_cnt)
        )

    def _grant_hero(self, uid, hero_id):
        star = 300 if hero_id in (STANDARD_S_HEROES + LIMITED_S_HEROES) else 200
        piece_add = 30 if star >= 300 else 18
        exists = self.db.query("SELECT id FROM hero WHERE uid=? AND id=?", (uid, hero_id))
        now = int(time.time())
        if not exists:
            self.db.execute(
                "INSERT INTO hero (uid, id, level, star, exp, skill_list, unlock, update_ts) VALUES (?, ?, 1, ?, 0, '[]', 1, ?)",
                (uid, hero_id, star, now)
            )
            try:
                import event_bus
                event_bus.bus.emit(event_bus.Events.HERO_UNLOCK, None, uid, hero_id=hero_id)
            except Exception:
                pass
            return True, False, hero_id, 0
        else:
            prow = self.db.query("SELECT num FROM hero_piece WHERE uid=? AND hero_id=?", (uid, hero_id))
            cur = prow[0]["num"] if prow else 0
            new_cnt = cur + piece_add
            self.db.execute(
                "INSERT INTO hero_piece (uid, hero_id, num) VALUES (?, ?, ?) ON CONFLICT(uid, hero_id) DO UPDATE SET num=excluded.num",
                (uid, hero_id, new_cnt)
            )
            return False, True, hero_id, piece_add

    def _insert_servant(self, uid, prefab_id):
        try:
            import inventory_service
            summary = inventory_service.grant_item(None, uid, int(prefab_id), 1, source="gacha")
            if summary and summary.get("servants"):
                return summary["servants"][0]["id"]
        except Exception:
            pass
        star = 5 if prefab_id >= 2500000 else (4 if prefab_id >= 2400000 else 3)
        max_row = self.db.query("SELECT MAX(id) AS m FROM servant WHERE uid=?", (uid,))
        new_uid = (max_row[0]["m"] or 1000) + 1
        self.db.execute(
            "INSERT INTO servant (uid, id, prefab_id, stage, starlevel, is_locked) VALUES (?, ?, ?, 1, ?, 0)",
            (uid, new_uid, int(prefab_id), star)
        )
        try:
            import event_bus
            event_bus.bus.emit(event_bus.Events.SERVANT_OBTAIN, None, uid, servant_id=int(prefab_id), count=1)
        except Exception:
            pass
        return new_uid

    # ==================== GM 与控制台开放接口 ====================

    def set_pity(self, uid, pool_group, since_ssr, is_up_guaranteed=None, since_sr=None):
        """GM 调控玩家的垫抽数与大保底状态"""
        st = self.get_draw_state(uid, pool_group)
        st["since_ssr"] = int(since_ssr)
        if since_sr is not None:
            st["since_sr"] = int(since_sr)
        if is_up_guaranteed is not None:
            st["is_up_guaranteed"] = int(is_up_guaranteed)
        self.save_draw_state(uid, pool_group, st)
        return True, f"成功设置 UID {uid} 系列 {pool_group} 垫抽数为 {since_ssr}"

    def list_all_pools(self):
        """列出全服所有卡池元数据与当前上架/安全评级状态"""
        if self.db is None:
            return []
        rows = self.db.query("SELECT * FROM draw_pool ORDER BY pool_id")
        active_set = set(self.get_active_pools())
        out = []
        pool_cfg_all = self.load_pool_up_cfg()

        for r in rows:
            pid = r["pool_id"]
            dj_str = r.get("detail_json") or "{}"
            grade, is_ltd, desc = self.check_pool_safety(pid, dj_str)
            pgroup = self.get_pool_group(pid, dj_str)
            pcfg = pool_cfg_all.get(str(pid)) or pool_cfg_all.get(int(pid)) or {}
            up_s = pcfg.get("up_s") or POOL_UP_HERO_MAP.get(r.get("name")) or 0

            out.append({
                "pool_id": pid,
                "name": r.get("name") or "",
                "pool_group": pgroup,
                "up_hero_id": up_s,
                "safety_grade": grade,
                "is_limited_voucher": is_ltd,
                "is_active": pid in active_set,
                "desc": desc
            })
        return out
