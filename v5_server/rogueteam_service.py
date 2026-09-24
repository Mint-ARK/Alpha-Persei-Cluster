# -*- coding: utf-8 -*-
"""
rogueteam_service.py — 虚构推演（Challenge Rogue Team / 88xxx 协议族）核心服务与状态机引擎

功能：
1. 静态配置加载与缓存（28 科技树节点、185 道具、4 难度、19 挑战词缀、楼层与事件树）。
2. 局内随机 7 列分支地图拓扑生成器（RogueTeamMapGenerator）。
3. 外围数据服务（28 科技树升级、图鉴查看、积分奖励、历史通关与难度梯度解锁）。
4. 局内推演状态机（难度1~4开局选人与自选Debuff、选关移动、事件选项分支、游商购物、步数环境演化、多维战斗掉落与终局结算）。
"""

import json
import os
import random
import time
import account_db

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rogueteam_cfg.json")


class RogueTeamConst:
    # 模板与活动
    TEMPLATE_ID = 100001
    ACTIVITY_ID = 1100
    FETTERS_ACTIVITY_ID = 1000
    POINT_ITEM_ID = 70
    TECH_ITEM_ID = 71

    # 节点类型 (NODE_TYPE)
    NODE_TYPE_BATTLE_NORMAL = 1
    NODE_TYPE_BATTLE_ELITE = 2
    NODE_TYPE_BATTLE_BOSS = 3
    NODE_TYPE_BATTLE_LAST_BOSS = 4
    NODE_TYPE_SHOP = 5
    NODE_TYPE_REST = 6
    NODE_TYPE_EVENT = 7
    NODE_TYPE_PLOT = 8
    NODE_TYPE_REWARD = 9
    NODE_TYPE_NULL = 10
    NODE_TYPE_BATTLE_DEMON = 11

    # 节点状态 (NODE_STATE)
    NODE_STATE_UNCLEAN = 0
    NODE_STATE_CLEAN = 1
    NODE_STATE_LOCK = 2
    NODE_STATE_OVER = 3

    # 楼层状态 (FLOOR_STATE)
    FLOOR_STATE_NORMAL = 0
    FLOOR_STATE_OVER = 1
    FLOOR_STATE_FAIL = 2

    # 局内属性 ID (ATTRIBUTE_ENUM & RogueTeamAttributeCfg)
    ATTR_MECHANISM_VALUE = 1          # 机制值 (0~99999)
    ATTR_MAX_COL = 2                  # 最大列数 (12)
    ATTR_DEFAULT_HERO_CNT = 3         # 初始出战修正者数 (3)
    ATTR_REVIVE_CNT = 4               # 当前剩余复活次数
    ATTR_REVIVE_LIMIT_CNT = 5         # 复活次数上限
    ATTR_GOLD = 6                     # 当前持有微光晶砾
    ATTR_TREASURE_RESET_CNT = 7       # 藏品重置/刷新次数
    ATTR_HERO_RECRUIT_CNT = 8         # 修正者招募次数
    ATTR_TREASURE_SELECT_CNT = 9      # 藏品可选数量 (默认3)
    ATTR_RELIC_SELECT_CNT = 10        # 圣物可选数量 (默认3)
    ATTR_SHOP_PRICE_RATE = 11         # 商店价格倍率 (1000=1.0)
    ATTR_SHOP_SELL_RELIC_MAX = 12     # 商店圣物售卖栏位 (默认2)
    ATTR_SHOP_SELL_TREASURE_MAX = 13  # 商店藏品售卖栏位 (默认2)
    ATTR_TREASURE_WEIGHT_1 = 14       # 普通藏品权重 (默认750)
    ATTR_TREASURE_WEIGHT_2 = 15       # 稀有藏品权重 (默认200)
    ATTR_TREASURE_WEIGHT_3 = 16       # 史诗藏品权重 (默认50)
    ATTR_TREASURE_WEIGHT_4 = 17       # 传说藏品权重 (默认10)
    ATTR_HP_RECOVERY_RATE = 18        # 战后回血百分比 (默认50 = 5%)
    ATTR_GOLD_GAIN_RATE = 19          # 战斗胜利微光晶砾倍率 (默认1000 = 1.0)
    ATTR_MECHANISM_THRESHOLD = 20     # 机制值阈值 (默认100)
    ATTR_MECHANISM_GAIN_VALUE = 21    # 机制值单次增长
    ATTR_MECHANISM_GAIN_RATE = 22     # 机制值增长速率倍率 (默认1000 = 1.0)
    ATTR_HERO_MAX_HP_PERCENT = 23     # 修正者最大生命百分比 (默认1000 = 100%)
    ATTR_SHOP_DISCOUNT = 25           # 商店折扣标记 (0/1)
    ATTR_NO_GOLD_GAIN = 26            # 无法获得微光晶砾标记 (苦修 0/1)
    ATTR_EVENT_ONCE_MORE = 27         # 事件再来一次标记 (0/1)
    ATTR_BATTLE_EXEMPT = 28           # 战胜免战次数 (势如破竹)
    ATTR_EXTRA_TREASURE_DROP = 29     # 战功延展 (前2战额外掉落藏品 0/1)
    ATTR_SHOP_REFRESH = 30            # 商店刷新标记 (0/1)
    ATTR_GOLD_GAIN_BUFF_PERCENT = 31  # 全局代币获取额外加成 (远期支票 +500)
    ATTR_NORMAL_BATTLE_GOLD_BUFF = 32 # 普通战斗专属代币加成 (招财钱罐 +800)

    # 道具类型 (ITEM_TYPE)
    ITEM_TYPE_INIT_REWARD = 1
    ITEM_TYPE_MECHANISM = 2
    ITEM_TYPE_RELIC = 3
    ITEM_TYPE_TREASURE = 4
    ITEM_TYPE_SUIT_SKILL = 5
    ITEM_TYPE_AFFIX = 6

    # 弹窗未决事件 (NODE_UNOPERATE_EVENT)
    POP_EVENT_TREASURE = 1
    POP_EVENT_RELIC = 2
    POP_EVENT_HERO_RECRUIT = 3
    POP_EVENT_EVENT = 4
    POP_EVENT_INIT_REWARD = 5
    POP_EVENT_WORLD_LINE = 6
    POP_EVENT_SHOP = 7
    POP_EVENT_TREASURE_UPGRADE = 8
    POP_EVENT_BAG_REPLACE = 9
    POP_EVENT_NODE_TYPE_CHANGED = 10
    POP_EVENT_MECHANISM = 11
    POP_EVENT_STORY = 12

    # 触发时机 (EFFECT_TRIGGER_MOMENT)
    TRIGGER_MOMENT_IMMEDIATE = 1
    TRIGGER_MOMENT_FOREVER = 2
    TRIGGER_MOMENT_TIMER = 3
    TRIGGER_MOMENT_WINBATTLE = 4
    TRIGGER_MOMENT_OUTANYROOM = 10
    TRIGGER_MOMENT_ENTERROOM = 20
    TRIGGER_MOMENT_ENTERSHOPROOM = 21
    TRIGGER_MOMENT_ENTERRESTROOM = 22
    TRIGGER_MOMENT_BUYITEM = 23

    # 封顶设置
    OPTIONAL_AFFIX_MULTIPLE = 5       # 每点词缀增加 5% 积分倍率
    OPTIONAL_AFFIX_MAX_MULTIPLE = 250 # 难度 4 总积分倍率封顶 250%


class RogueTeamConfig:
    _instance = None

    def __init__(self):
        self.cfg = {}
        self.load()

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = RogueTeamConfig()
        return cls._instance

    def load(self):
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, 'r', encoding='utf-8') as f:
                self.cfg = json.load(f)
        else:
            self.cfg = {}

    @property
    def skill_tree(self):
        return self.cfg.get('skill_tree', {})

    @property
    def items(self):
        return self.cfg.get('items', {})

    @property
    def difficulties(self):
        return self.cfg.get('difficulties', [])

    @property
    def optional_affixes(self):
        return self.cfg.get('optional_affixes', {})

    @property
    def effects(self):
        return self.cfg.get('effects', {})

    @property
    def floors(self):
        return self.cfg.get('floors', {})

    @property
    def rooms(self):
        return self.cfg.get('rooms', {})

    @property
    def events(self):
        evs = self.cfg.get('events', {})
        if isinstance(evs, list):
            return {e["id"]: e for e in evs}
        return evs

    @property
    def event_options(self):
        opts = self.cfg.get('event_options', {})
        if isinstance(opts, list):
            return {o["id"]: o for o in opts}
        return opts

    def get_skill_node(self, node_id):
        return self.skill_tree.get(str(node_id)) or self.skill_tree.get(int(node_id))

    def get_item(self, item_id):
        return self.items.get(str(item_id)) or self.items.get(int(item_id))

    def get_difficulty(self, diff_id):
        for d in self.difficulties:
            if d.get("id") == diff_id or d.get("difficulty") == diff_id:
                return d
        return {"id": diff_id, "difficulty": diff_id, "score": 100, "unlock_condition": 0, "params": [1000, 1000, 1000]}

    def get_optional_affix(self, affix_id):
        return self.optional_affixes.get(str(affix_id)) or self.optional_affixes.get(int(affix_id)) or {}

    def get_effect(self, effect_id):
        return self.effects.get(str(effect_id)) or self.effects.get(int(effect_id)) or {}

    def get_event(self, event_id):
        return self.events.get(str(event_id)) or self.events.get(int(event_id))

    @property
    def conditions(self):
        return self.cfg.get('conditions', {})

    def get_condition(self, cond_id):
        return self.conditions.get(str(cond_id)) or self.conditions.get(int(cond_id))

    def get_event_option(self, option_id):
        return self.cfg.get("event_options", {}).get(str(option_id))

    def get_event_effect(self, effect_id):
        return self.cfg.get("event_effects", {}).get(str(effect_id))

    def get_item_default_rare(self, item_id, item_cfg=None):
        """
        官方稀有度规格映射：
        - 141xxxx: 基础单流派藏品 (Rare 1 蓝色，可通过科技/苦修升级至 Rare 2/3)
        - 142xxxx: 双流派高级藏品 (Rare 2 紫色，如 1420202 压制·暴风雨)
        - 143xxxx: 史诗强力藏品 (Rare 3 金色)
        - 144xxxx: 传说终极藏品 (Rare 4 红色)
        - 120xxxx: 外接程序 (Rare 1，专属深蓝框)
        - 131/132/133: 圣物 (Rare 1~2)
        """
        if not item_id:
            return 1
        s_id = str(item_id)
        if s_id.startswith("144"):
            return 4
        elif s_id.startswith("143"):
            return 3
        elif s_id.startswith("142"):
            return 2
        elif s_id.startswith("141"):
            return 1
        cfg = item_cfg or self.get_item(item_id) or {}
        return int(cfg.get("rare") or cfg.get("sub_type", 1) or 1)

    def roll_treasure_quality(self, bonus_rare3=0, bonus_rare4=0):
        """基于权重动态抽取藏品稀有度：1:普通(750), 2:稀有(200), 3:史诗(50+bonus), 4:传说(10+bonus)"""
        w1 = 750
        w2 = 200
        w3 = max(10, 50 + bonus_rare3)
        w4 = max(5, 10 + bonus_rare4)
        return random.choices([1, 2, 3, 4], weights=[w1, w2, w3, w4], k=1)[0]

    def get_random_items(self, item_type=RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=None, pool_id=None):
        camp_filter = None
        if pool_id and pool_id in (401, 402, 403, 404, 405, 406):
            camp_filter = pool_id - 400

        pool = []
        for iid, data in self.items.items():
            if item_type is not None and data.get('type') != item_type:
                continue
            item_rare = data.get('rare')
            if rare is not None and item_rare is not None and item_rare != rare:
                continue
            if camp_filter is not None:
                iid_str = str(iid)
                if not (iid_str.startswith(f"1410{camp_filter}") or iid_str.startswith(f"1420{camp_filter}")):
                    continue
            pool.append(int(iid))
        if not pool and camp_filter is not None:
            # 兜底降级：取该阵营所有道具
            pool = [int(iid) for iid, data in self.items.items() if (item_type is None or data.get('type') == item_type) and (str(iid).startswith(f"1410{camp_filter}") or str(iid).startswith(f"1420{camp_filter}"))]
        if not pool:
            # 兜底降级：取该类型所有道具
            pool = [int(iid) for iid, data in self.items.items() if item_type is None or data.get('type') == item_type]
        if not pool:
            return []
        return random.sample(pool, min(count, len(pool)))

    def get_random_shop_items(self, item_type=RogueTeamConst.ITEM_TYPE_TREASURE, count=3):
        if item_type == RogueTeamConst.ITEM_TYPE_TREASURE:
            pool = self.cfg.get('shop_treasures', [])
        elif item_type == RogueTeamConst.ITEM_TYPE_RELIC:
            pool = self.cfg.get('shop_relics', [])
        else:
            pool = [int(k) for k, v in self.items.items() if v.get('type') == item_type]
        if not pool:
            return []
        return random.sample(pool, min(count, len(pool)))


class RogueTeamMapGenerator:
    """
    7 列多分支随机肉鸽地图拓扑生成器
    符合 ChallengeRogueTeamMapData 客户端解析规格。
    """
    # === [TEMP TEST STUB: 优先重测 10110/10120/10140，随后轮换剩余未测事件] ===
    _test_event_cursor = 0
    _ALL_49_EVENTS = [
        # 优先重测目标事件：
        10110, 10120, 10140,
        # 其余未测事件轮换池：
        10150, 10160, 20010, 20030, 20040, 20050, 20060,
        20070, 20080, 20090, 20100, 20110, 20120, 20140, 20150, 20160, 30010,
        30020, 30030, 30050, 30060, 30070, 30080, 30090, 31100, 40010, 40020,
        40040, 40050, 40060, 50010, 60010, 60020, 60030, 60040, 60050
    ]

    @classmethod
    def get_next_test_event_id(cls):
        ev_id = cls._ALL_49_EVENTS[cls._test_event_cursor % len(cls._ALL_49_EVENTS)]
        cls._test_event_cursor += 1
        return ev_id

    @classmethod
    def generate_floor_map(cls, floor_num=1, difficult=1):
        config = RogueTeamConfig.get()
        nodes = []
        max_col = 7

        # 7 列行号布局
        col_layout = [
            [2, 4],        # 列 1: 起点 (2个节点)
            [1, 3, 5],     # 列 2: 3个节点
            [2, 4],        # 列 3: 2个节点
            [1, 3, 5],     # 列 4: 3个节点
            [2, 4],        # 列 5: 2个节点
            [3],           # 列 6: 1个节点 (休整/游商)
            [3]            # 列 7: 1个节点 (Boss)
        ]

        col_row_to_node = {}

        # 预先分配节点 ID 与类型
        for col_idx, rows in enumerate(col_layout, start=1):
            for row in rows:
                node_id = col_idx * 100 + row
                
                # 决定节点类型
                if floor_num == 1:
                    # === [TEMP TEST STUB: 第一层全部定向为抉择事件节点，无重复轮换 49 种事件] ===
                    if col_idx == 7:
                        node_type = RogueTeamConst.NODE_TYPE_BATTLE_BOSS
                        state = RogueTeamConst.NODE_STATE_LOCK
                        param = cls._get_node_param(node_type, floor_num)
                    else:
                        node_type = RogueTeamConst.NODE_TYPE_EVENT
                        state = RogueTeamConst.NODE_STATE_UNCLEAN if col_idx == 1 else RogueTeamConst.NODE_STATE_LOCK
                        param = cls.get_next_test_event_id()
                else:
                    if col_idx == 1:
                        node_type = RogueTeamConst.NODE_TYPE_BATTLE_NORMAL
                        state = RogueTeamConst.NODE_STATE_UNCLEAN
                    elif col_idx == 2:
                        node_type = RogueTeamConst.NODE_TYPE_BATTLE_ELITE
                        state = RogueTeamConst.NODE_STATE_LOCK
                    elif col_idx == 7:
                        node_type = RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS if floor_num >= 3 else RogueTeamConst.NODE_TYPE_BATTLE_BOSS
                        state = RogueTeamConst.NODE_STATE_LOCK
                    elif col_idx == 6:
                        node_type = random.choice([RogueTeamConst.NODE_TYPE_REST, RogueTeamConst.NODE_TYPE_SHOP])
                        state = RogueTeamConst.NODE_STATE_LOCK
                    else:
                        weights = [
                            (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, 35),
                            (RogueTeamConst.NODE_TYPE_BATTLE_ELITE, 20),
                            (RogueTeamConst.NODE_TYPE_SHOP, 15),
                            (RogueTeamConst.NODE_TYPE_EVENT, 15),
                            (RogueTeamConst.NODE_TYPE_REST, 10),
                            (RogueTeamConst.NODE_TYPE_REWARD, 5),
                        ]
                        types, wts = zip(*weights)
                        node_type = random.choices(types, weights=wts, k=1)[0]
                        state = RogueTeamConst.NODE_STATE_LOCK
                    param = cls._get_node_param(node_type, floor_num)

                node_data = {
                    'node_id': node_id,
                    'col': col_idx,
                    'row': row,
                    'node_type': node_type,
                    'next_id_list': [],
                    'state': state,
                    'param': param
                }
                col_row_to_node[(col_idx, row)] = node_data
                nodes.append(node_data)

        # 构建连线拓扑（确保每列连通）
        for col_idx in range(1, max_col):
            curr_rows = col_layout[col_idx - 1]
            next_rows = col_layout[col_idx]

            for r_curr in curr_rows:
                curr_node = col_row_to_node[(col_idx, r_curr)]
                
                # 寻找下一列相邻或最近行
                for r_next in next_rows:
                    if abs(r_curr - r_next) <= 1:
                        next_node = col_row_to_node[(col_idx + 1, r_next)]
                        if next_node['node_id'] not in curr_node['next_id_list']:
                            curr_node['next_id_list'].append(next_node['node_id'])
                
                # 兜底保底：若无连线，连向下一列中行距最近的节点
                if not curr_node['next_id_list']:
                    closest_row = min(next_rows, key=lambda r: abs(r - r_curr))
                    curr_node['next_id_list'].append(col_row_to_node[(col_idx + 1, closest_row)]['node_id'])

        # 确保下一列每个节点都至少有一个入边
        for col_idx in range(1, max_col):
            curr_rows = col_layout[col_idx - 1]
            next_rows = col_layout[col_idx]
            for r_next in next_rows:
                target_id = col_row_to_node[(col_idx + 1, r_next)]['node_id']
                has_in = any(target_id in col_row_to_node[(col_idx, r_curr)]['next_id_list'] for r_curr in curr_rows)
                if not has_in:
                    closest_prev = min(curr_rows, key=lambda r: abs(r - r_next))
                    col_row_to_node[(col_idx, closest_prev)]['next_id_list'].append(target_id)

        return nodes

    @classmethod
    def _get_node_param(cls, node_type, floor_num):
        config = RogueTeamConfig.get()
        rooms = config.rooms
        
        # 优先按层数精确匹配对应 Room ID
        floor_key = min(3, max(1, floor_num))
        room_by_floor = {
            RogueTeamConst.NODE_TYPE_BATTLE_NORMAL: {1: 1001, 2: 1002, 3: 1003},
            RogueTeamConst.NODE_TYPE_BATTLE_ELITE: {1: 2001, 2: 2002, 3: 2003},
            RogueTeamConst.NODE_TYPE_BATTLE_BOSS: {1: 3001, 2: 3002, 3: 3001},
            RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS: {1: 4001, 2: 4002, 3: 4003},
            RogueTeamConst.NODE_TYPE_REST: {1: 6001, 2: 6001, 3: 6001},
            RogueTeamConst.NODE_TYPE_EVENT: {1: 7001, 2: 7002, 3: 7003},
            RogueTeamConst.NODE_TYPE_PLOT: {1: 8001, 2: 8002, 3: 8003},
            RogueTeamConst.NODE_TYPE_REWARD: {1: 9001, 2: 9002, 3: 9003},
        }
        
        target_room_id = room_by_floor.get(node_type, {}).get(floor_key)
        room_obj = rooms.get(str(target_room_id)) if target_room_id else None
        if not room_obj or not room_obj.get("params"):
            # 备选：从所有该 room_type 的房间中随机取一个
            matched = [r for r in rooms.values() if r.get('room_type') == node_type and r.get('params')]
            room_obj = random.choice(matched) if matched else None

        if room_obj and room_obj.get("params"):
            p = random.choice(room_obj["params"])
            if isinstance(p, dict):
                return p.get("stage_id", 4101101)
            elif isinstance(p, (list, tuple)):
                return p[1] if len(p) > 1 else p[0]
            return p
        
        # 默认关卡/事件兜底
        defaults = {
            RogueTeamConst.NODE_TYPE_BATTLE_NORMAL: 4101101,
            RogueTeamConst.NODE_TYPE_BATTLE_ELITE: 4101201,
            RogueTeamConst.NODE_TYPE_BATTLE_BOSS: 4101301,
            RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS: 4103301,
            RogueTeamConst.NODE_TYPE_EVENT: 10010,
            RogueTeamConst.NODE_TYPE_REST: 50010,
            RogueTeamConst.NODE_TYPE_REWARD: 40010,
            RogueTeamConst.NODE_TYPE_PLOT: 60010,
            RogueTeamConst.NODE_TYPE_BATTLE_DEMON: 4106013,
        }
        return defaults.get(node_type, 0)


class RogueTeamService:
    _instance = None

    def __init__(self, db=None):
        self._custom_db = db
        self.config = RogueTeamConfig.get()

    @classmethod
    def get(cls, db=None):
        if cls._instance is None:
            cls._instance = RogueTeamService(db=db)
        elif db is not None:
            cls._instance._custom_db = db
        return cls._instance

    @classmethod
    def get_instance(cls, db=None):
        return cls.get(db=db)

    def _db(self):
        return self._custom_db or account_db.get_db()

    # ==================== 1. 外围科技树服务 (28个核心节点) ====================

    def get_skill_tree_list(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        rows = db.query("SELECT node_id FROM rogueteam_tree WHERE uid=? AND template_id=?", (uid, template_id))
        return [r["node_id"] for r in rows]

    def unlock_skill_tree(self, uid, template_id, tree_id):
        node = self.config.get_skill_node(tree_id)
        if not node:
            return 3  # 缺少配置

        cost = node.get("cost", 0)
        db = self._db()
        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,))
        current_tech_point = user_row.get("tech_point", 0) if user_row else 0
        if current_tech_point < cost:
            return 12  # 道具/点数不足

        existing = db.get("rogueteam_tree", uid, "AND template_id=? AND node_id=?", (template_id, tree_id))
        if existing:
            return 2  # 已解锁

        db.upsert("rogueteam_tree", uid, {
            "template_id": template_id,
            "node_id": tree_id,
            "unlock_ts": int(time.time())
        }, keys=("uid", "template_id", "node_id"))

        new_point = current_tech_point - cost
        db.upsert("rogueteam_user", uid, {
            "template_id": template_id,
            "tech_point": new_point,
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        return 0

    # ---- 图鉴白名单：unlock_collection / view_collection 只能装真实道具 ID ----
    _VALID_ITEM_IDS = None

    def _valid_item_ids(self):
        """客户端 RogueTeamItemCfg 里真实存在的 185 个道具 ID（全为 7 位数）。

        客户端 ChallengeRogueTeamIllustratedData 的 UpdateUnlockIllustrated /
        UpdateViewedIllustrated 会对列表里每个 ID 执行 RogueTeamItemCfg[id].type，
        一旦遇到配置表里不存在的 ID（例如结局 ID 1~10），会直接抛
        "attempt to index a nil value"，并且**中断 InitOutSideFromServer 的后续步骤**
        （UpdateViewedIllustrated / InitSkillTreeFromServer 天赋树 /
        SetLastScoreId / InitRedPoint 全部不再执行）。
        结局这类非道具条目只能走 collection_list（自带 type 字段，客户端不查 ItemCfg）。
        """
        cls = RogueTeamService
        if cls._VALID_ITEM_IDS is None:
            try:
                cls._VALID_ITEM_IDS = frozenset(int(k) for k in self.config.items.keys())
            except Exception:
                cls._VALID_ITEM_IDS = frozenset()
        return cls._VALID_ITEM_IDS

    _RELIC_ITEM_IDS = None

    def _relic_item_ids(self):
        """RogueTeamItemCfg 里 type=3（圣物）的全部 id。"""
        cls = RogueTeamService
        if cls._RELIC_ITEM_IDS is None:
            ids = set()
            try:
                for k, v in self.config.items.items():
                    if isinstance(v, dict) and int(v.get("type", 0) or 0) == RogueTeamConst.ITEM_TYPE_RELIC:
                        ids.add(int(k))
            except Exception:
                ids = set()
            cls._RELIC_ITEM_IDS = frozenset(ids)
        return cls._RELIC_ITEM_IDS

    # ==================== 2. 难度与外围用户数据 ====================

    def get_user_outside_info(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,))
        if not user_row:
            user_row = {
                "template_id": template_id,
                "difficult": 1,
                "max_difficult": 1,
                "tech_point": 9999,
                "last_score_id": 0,
                "update_ts": int(time.time())
            }
            db.upsert("rogueteam_user", uid, user_row, keys=("uid", "template_id"))

        his_row = db.get("rogueteam_history", uid, "AND template_id=?", (template_id,))
        his_diff = json.loads(his_row.get("diff_clear_json") or "[]") if his_row else []
        his_avg = json.loads(his_row.get("ending_pass_json") or "[]") if his_row else []

        colls = db.query("SELECT item_id, item_type, is_viewed FROM rogueteam_illustrated WHERE uid=? AND template_id=?", (uid, template_id))
        collection_by_type = {}
        unlock_collection = []
        view_collection = []
        valid_item_ids = self._valid_item_ids()
        for c in colls:
            itype = c["item_type"]
            iid = c["item_id"]
            collection_by_type.setdefault(itype, []).append(iid)
            if iid in valid_item_ids:
                # 只有真实道具 ID 能进 unlock / view 列表（结局 ID 会让客户端空指针）
                unlock_collection.append(iid)
                if c.get("is_viewed"):
                    view_collection.append(iid)

        collection_list = [{"type": t, "item_list": items} for t, items in collection_by_type.items()]
        tree_list = self.get_skill_tree_list(uid, template_id)

        # 全量下发「已录入图鉴槽位」（未收集的显示灰色）——这是刻意为之：
        # 64 个圣物里有 54 个 condition=0，而客户端 RogueTeamConditionCfg 没有 [0] 项，
        # 未解锁的圣物会让 ChallengeRogueTeamIllustratedRelicPanel:ShowItemInfo 取
        # RogueTeamConditionCfg[0].desc 空指针崩溃（进图鉴即崩、返回键同时失效）。
        # 注意：这里**只能**放 valid_item_ids，绝不能混入结局 ID
        #（原 list(range(1, 11)) 就是 ③图鉴崩溃 + ④天赋不生效 的共同崩溃源）。
        # 子智能体 A 的客户端取证结论：unlock_collection 只应放**圣物 id（ITEM_TYPE=3）**，
        # 且必须覆盖全部 condition==0 的圣物，否则 ShowItemInfo 的 black 分支
        # RogueTeamConditionCfg[0].desc 空指针 → 进圣物图鉴即崩、返回键同时失效。
        full_unlock_collection = sorted(self._relic_item_ids() | set(unlock_collection))

        return {
            "template_id": template_id,
            "difficult": user_row.get("difficult", 1),
            "max_difficult": user_row.get("max_difficult", 1),
            "his_difficult": his_diff,
            "his_avg": his_avg,
            "collection_list": collection_list,
            "unlock_collection": full_unlock_collection,
            "view_collection": view_collection,
            "tree_list": tree_list,
            "last_id": user_row.get("last_score_id", 0)
        }

    def set_last_score_id(self, uid, main_id, target_id):
        db = self._db()
        db.upsert("rogueteam_user", uid, {
            "template_id": main_id,
            "last_score_id": target_id,
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0

    # ==================== 3. 局内状态机核心 (cs_88100/88004/88008/88024/88222/88306) ====================

    def start_run(self, uid, template_id, difficult, hero_list, affix_pool_id_list):
        """
        88100 开启虚构推演：
        - 校验难度 1~4 解锁状态
        - 难度 4 自选 Debuff 词缀与积分倍率计算（封顶 250%）
        - 加载 28 科技树有效节点属性（初始金币/免战/复活/回血/重置）
        - 初始化 Session、首层地图与全局步数跟踪
        """
        db = self._db()
        template_id = template_id or RogueTeamConst.TEMPLATE_ID
        difficult = max(1, min(4, difficult))

        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,))
        max_diff = user_row.get("max_difficult", 1) if user_row else 1
        if difficult > max_diff:
            # 容错：若未解锁则自动纠偏至当前最高已解锁难度
            difficult = max_diff

        # 1. 难度与积分倍率计算
        diff_cfg = self.config.get_difficulty(difficult)
        base_score_rate = diff_cfg.get("score", 100)
        
        affix_list = affix_pool_id_list or []
        if difficult == 4 and affix_list:
            opt_points = sum(self.config.get_optional_affix(aid).get("point", 0) for aid in affix_list)
            # 公式：基础 150% + sum(point) * 5%，封顶 250%
            score_rate = min(RogueTeamConst.OPTIONAL_AFFIX_MAX_MULTIPLE, base_score_rate + opt_points * RogueTeamConst.OPTIONAL_AFFIX_MULTIPLE)
        else:
            score_rate = base_score_rate

        # 2. 生成第一层地图
        map_info = RogueTeamMapGenerator.generate_floor_map(floor_num=1, difficult=difficult)

        # 3. 组装初始修正者
        heroes = []
        for h in hero_list:
            hid = h.get("hero_id") or h.get("id") or 0
            tid = h.get("temp_id") or 0
            heroes.append({
                "hero_id": hid,
                "temp_id": tid,
                "hp_ratio": 10000  # 100.00%
            })

        # 4. 查询 28 科技树已解锁节点并计算初始属性
        unlocked_trees = {r["node_id"] for r in db.query("SELECT node_id FROM rogueteam_tree WHERE uid=? AND template_id=?", (uid, template_id))}

        # 初始金币：基础 100 + (10202/10502/10802/11102 每个+80，最高+320 => 420)
        gold_nodes = (10202, 10502, 10802, 11102)
        extra_gold = sum(80 for nid in gold_nodes if nid in unlocked_trees)
        init_gold = 100 + extra_gold

        # 初始藏品重置点：基础 2 + (10203 +2 => 4)
        init_reset = 2 + (2 if 10203 in unlocked_trees else 0)

        # 初始复活次数与上限：基础 1 + (10999 +1 => 2)
        extra_revive = 1 if 10999 in unlocked_trees else 0
        init_revive = 1 + extra_revive
        init_revive_limit = 1 + extra_revive

        # 战胜免战次数：10699 (+2)
        init_exempt = 2 if 10699 in unlocked_trees else 0

        # 战功延展：10399 (前2战额外藏品)
        extra_treasure_flag = 1 if 10399 in unlocked_trees else 0

        # 免战次数 > 0 → 首层战斗节点标记为可跳过（param=0）
        RogueTeamService._apply_exempt_marks(map_info, init_exempt)

        # 商店额外栏位：10503 (+2藏品位), 10803 (+2圣物位)
        shop_treasure_slots = 2 + (2 if 10503 in unlocked_trees else 0)
        shop_relic_slots = 2 + (2 if 10803 in unlocked_trees else 0)

        # 高级展品：11299 (史诗权重+20, 传说权重+20)
        bonus_epic_weight = 20 if 11299 in unlocked_trees else 0
        bonus_legend_weight = 20 if 11299 in unlocked_trees else 0

        # 回复强化：10201/10501/10801/11101 (每个+2%，最高+8%)
        heal_nodes = (10201, 10501, 10801, 11101)
        recovery_rate = sum(20 for nid in heal_nodes if nid in unlocked_trees)  # 20 = 2%

        # 体魄强化：10101/10401/10701/11001 (每个+10%，最高+40%)
        hp_nodes = (10101, 10401, 10701, 11001)
        max_hp_percent = 1000 + sum(100 for nid in hp_nodes if nid in unlocked_trees)

        # 组装初始全局属性字典
        attr_dict = {
            RogueTeamConst.ATTR_GOLD: init_gold,
            RogueTeamConst.ATTR_REVIVE_CNT: init_revive,
            RogueTeamConst.ATTR_REVIVE_LIMIT_CNT: init_revive_limit,
            RogueTeamConst.ATTR_TREASURE_RESET_CNT: init_reset,
            RogueTeamConst.ATTR_HERO_RECRUIT_CNT: 1,
            RogueTeamConst.ATTR_TREASURE_SELECT_CNT: 3,
            RogueTeamConst.ATTR_RELIC_SELECT_CNT: 3,
            RogueTeamConst.ATTR_SHOP_PRICE_RATE: 1000,
            RogueTeamConst.ATTR_SHOP_SELL_TREASURE_MAX: shop_treasure_slots,
            RogueTeamConst.ATTR_SHOP_SELL_RELIC_MAX: shop_relic_slots,
            RogueTeamConst.ATTR_TREASURE_WEIGHT_1: 750,
            RogueTeamConst.ATTR_TREASURE_WEIGHT_2: 200,
            RogueTeamConst.ATTR_TREASURE_WEIGHT_3: 50 + bonus_epic_weight,
            RogueTeamConst.ATTR_TREASURE_WEIGHT_4: 10 + bonus_legend_weight,
            RogueTeamConst.ATTR_HP_RECOVERY_RATE: recovery_rate,
            RogueTeamConst.ATTR_GOLD_GAIN_RATE: 1000,
            RogueTeamConst.ATTR_MECHANISM_VALUE: 0,
            RogueTeamConst.ATTR_MECHANISM_THRESHOLD: 100,
            RogueTeamConst.ATTR_HERO_MAX_HP_PERCENT: max_hp_percent,
            RogueTeamConst.ATTR_BATTLE_EXEMPT: init_exempt,
            RogueTeamConst.ATTR_EXTRA_TREASURE_DROP: extra_treasure_flag,
        }
        attr_list = [{"attr_id": k, "value": v} for k, v in attr_dict.items()]

        # 组装科技树战斗/局内效果列表
        effect_list = []
        for nid in unlocked_trees:
            node_cfg = self.config.get_skill_node(nid)
            if node_cfg and node_cfg.get("effects"):
                for eid in node_cfg["effects"]:
                    effect_list.append({
                        "effect_id": eid,
                        "source_type": 1,  # SKILL
                        "source_id": nid,
                        "calc_value": 0,
                        "moment_time": 0,
                        "left_time": 999999,
                        "trigger_cd": 0
                    })

        # 5. 保存 Session
        session_data = {
            "template_id": template_id,
            "difficult": difficult,
            "floor_num": 1,
            "floor_state": RogueTeamConst.FLOOR_STATE_NORMAL,
            "select_node_id": 0,
            "room_passed_count": 0,
            "battle_passed_count": 0,
            "score_rate": score_rate,
            "map_info_json": json.dumps(map_info, ensure_ascii=False),
            "second_map_json": "[]",
            "treasure_list_json": "[]",
            "other_item_list_json": "[]",
            "effect_list_json": json.dumps(effect_list, ensure_ascii=False),
            "hero_list_json": json.dumps(heroes, ensure_ascii=False),
            "attr_list_json": json.dumps(attr_list, ensure_ascii=False),
            "shop_info_json": "{}",
            "other_info_json": "{}",
            "affix_list_json": json.dumps(affix_list, ensure_ascii=False),
            "in_game": 1,
            "update_ts": int(time.time())
        }
        db.upsert("rogueteam_session", uid, session_data, keys=("uid", "template_id"))

        # 更新用户主表当前难度
        db.upsert("rogueteam_user", uid, {
            "template_id": template_id,
            "difficult": difficult,
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        return 0, self.get_session_data(uid, template_id)

    def get_valid_node_domain(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        """获取当前玩家在推演地图上的合法值域（Frontier Valid Set）"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s or not s.get("in_game"):
            return [], 0

        map_info = json.loads(s.get("map_info_json") or "[]")
        if not map_info:
            return [], 0

        active_nodes = [n["node_id"] for n in map_info if n.get("state") in (RogueTeamConst.NODE_STATE_UNCLEAN, RogueTeamConst.NODE_STATE_CLEAN)]
        if not active_nodes:
            active_nodes = [n["node_id"] for n in map_info if n.get("col") == 1]

        current_node_id = s.get("select_node_id", 0)
        if current_node_id not in active_nodes and active_nodes:
            current_node_id = active_nodes[0]

        return active_nodes, current_node_id

    def is_valid_node(self, uid, node_id, template_id=RogueTeamConst.TEMPLATE_ID):
        """校验目标节点是否处于当前合法值域内"""
        valid_domain, current_id = self.get_valid_node_domain(uid, template_id)
        if not valid_domain:
            return False, node_id or 102, "Map is empty or session not in game"
        if node_id in valid_domain:
            return True, node_id, "OK"
        return False, valid_domain[0], f"Node {node_id} is out of valid frontier domain {valid_domain}"

    # ==================== 协议结构标准化工具 ====================

    @staticmethod
    def _apply_exempt_marks(map_info, exempt_cnt):
        """势如破竹（天赋 10699）：免战次数 > 0 时把未通关的普通/精英战斗节点 param 置 0。
        客户端 ChallengeRogueTeamSectionInfoView.OnClickBtn 只有 param == 0 才走免战分支
        （SelectedNode + 免战动画），param != 0 会直接进战斗——所以"能不能跳过"完全由服务端决定。"""
        if not exempt_cnt or exempt_cnt <= 0:
            return
        for n in map_info or []:
            if n.get("node_type") in (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE) \
                    and n.get("state") != RogueTeamConst.NODE_STATE_CLEAN:
                n["param"] = 0

    @staticmethod
    def _norm_node(n):
        """NODE_INFO：node_id/col/row/node_type/next_id_list/state/param"""
        return {
            "node_id": int(n.get("node_id", 0)),
            "col": int(n.get("col", 0)),
            "row": int(n.get("row", 0)),
            "node_type": int(n.get("node_type", 0)),
            "next_id_list": [int(x) for x in n.get("next_id_list", [])],
            "state": int(n.get("state", 0)),
            "param": int(n.get("param", 0) or 0)
        }

    @staticmethod
    def _treasure_entries(treasures):
        """ROGUE_TREASURE_INFO{id, rare} 列表"""
        out = []
        for t in treasures or []:
            if isinstance(t, dict):
                tid = int(t.get("id", 0) or 0)
                if tid:
                    out.append({"id": tid, "rare": int(t.get("rare", 1) or 1)})
            else:
                try:
                    out.append({"id": int(t), "rare": 1})
                except (TypeError, ValueError):
                    continue
        return out

    @staticmethod
    def _other_item_ids(other_items):
        """other_item_list / ROUGE_ITEM.other_list 都是 repeated uint32（纯 id），
        绝不能塞 {id, value} 字典——codec 会整条丢弃，外接程序在背包里就消失了。
        堆叠数量用重复 id 表达。"""
        out = []
        for o in other_items or []:
            if isinstance(o, dict):
                oid = int(o.get("id", 0) or 0)
                if not oid:
                    continue
                cnt = int(o.get("value", 1) or 1)
                out.extend([oid] * max(1, min(cnt, 99)))
            else:
                try:
                    out.append(int(o))
                except (TypeError, ValueError):
                    continue
        return out

    @classmethod
    def _bag_item_list(cls, treasures, other_items, opt=1):
        """sc_88021.item_list = [ROUGE_ITEM{opt, treasure_list, other_list}]"""
        return [{
            "opt": int(opt),
            "treasure_list": cls._treasure_entries(treasures),
            "other_list": cls._other_item_ids(other_items)
        }]

    @classmethod
    def _is_floor_clear(cls, map_info):
        """判断当前层是否已全通（所有可探索前沿都已打完，无 UNCLEAN/CLEAN 节点）"""
        if not map_info:
            return False
        has_active = any(n.get("state") in (RogueTeamConst.NODE_STATE_UNCLEAN, RogueTeamConst.NODE_STATE_CLEAN) for n in map_info)
        has_over = any(n.get("state") == RogueTeamConst.NODE_STATE_OVER for n in map_info)
        return has_over and not has_active


    def get_session_data(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return None

        map_info = json.loads(s.get("map_info_json") or "[]")
        second_map = json.loads(s.get("second_map_json") or "[]")
        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        effects = json.loads(s.get("effect_list_json") or "[]")
        heroes = json.loads(s.get("hero_list_json") or "[]")
        attr_list = json.loads(s.get("attr_list_json") or "[]")
        shop_info = json.loads(s.get("shop_info_json") or "{}")
        other_info = json.loads(s.get("other_info_json") or "{}")

        affix_list = json.loads(s.get("affix_list_json") or "[]")

        # ---- sc_88001 字段名必须与 p88_pb.lua 完全一致 ----
        # 真实字段：floor_num / floor_state / select_node_id / map_info / treasure_list /
        #           other_item_list / effect_list / second_map_info / template_id /
        #           other_info / shop_info / affix_list / plot_id
        # 旧版曾用 map_list / second_map_list / item_list —— codec 找不到字段会静默丢弃，
        # 导致 sc_88001 下发出去时地图为空，客户端层数/节点指针错乱（中继岛等异常）。
        map_nodes = [self._norm_node(n) for n in map_info]
        second_map_nodes = [self._norm_node(n) for n in second_map]

        # sc_88001.other_info 是 ROUGE_EVENT（event_type/param_list/drop_type），
        # 事件房弹窗（event_id/opt_list）走 sc_88023，不能塞进这里。
        # 必须走 _norm_event：内部标记 _extra_draw 绝不能带进协议（codec 会丢字段告警）。
        sc_other_info = self._norm_event(other_info) if isinstance(other_info, dict) else {}
        sc_shop_info = self._norm_event(shop_info) if isinstance(shop_info, dict) else {}

        payload = {
            # —— sc_88001 真实字段 ——
            "template_id": s.get("template_id", template_id),
            "floor_num": s.get("floor_num", 1),
            "floor_state": s.get("floor_state", 0),
            "select_node_id": s.get("select_node_id", 0),
            "map_info": map_nodes,
            "second_map_info": second_map_nodes,
            "treasure_list": self._treasure_entries(treasures),
            "other_item_list": self._other_item_ids(other_items),
            "effect_list": effects,
            "affix_list": [int(a) for a in affix_list if isinstance(a, (int, float))],
            "plot_id": s.get("plot_id", 0) or 0,
            # —— 以下为服务端内部使用（sc_88001 无这些字段，编码时会被丢弃）——
            "difficult": s.get("difficult", 1),
            "hero_list": heroes,
            "attr_list": attr_list,
            "in_game": s.get("in_game", 0)
        }
        # 恒发 other_info/shop_info（event_type 不存在时给 0 兜底）：
        # 客户端 ClearUnOperateData/EventUpdate 直接索引 unOperateData_，
        # 不初始化（nil）会在某些路径抛 nil 索引（B 报告 3.x 补充）。
        # UpdateOperateData 对 event_type=0 走不到任何分支，只写一个空窗口，安全。
        payload["other_info"] = sc_other_info or {"event_type": 0, "param_list": [], "drop_type": 0}
        payload["shop_info"] = sc_shop_info or {"event_type": 0, "param_list": [], "drop_type": 0}
        return payload

    def select_node(self, uid, node_id, template_id=RogueTeamConst.TEMPLATE_ID):
        """
        处理 88004 地图节点选择与路径移动：
        - 同列其他节点全部锁定 (NODE_STATE_OVER = 3)
        - 仅解锁目标节点的 next_id_list 节点为可挑战 (NODE_STATE_UNCLEAN = 0)
        - 免战节点 (param == 0) 自动触发战胜结算：发放金币、战后回血、步数推进、科技10399双重选牌与扣减免战次数
        - 商店/安全屋/事件触发对应交互数据
        """
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, None

        # 选路值域校验与自动纠偏
        is_valid, effective_id, _ = self.is_valid_node(uid, node_id, template_id=template_id)
        if not is_valid:
            node_id = effective_id

        map_info = json.loads(s.get("map_info_json") or "[]")
        target_node = None
        for n in map_info:
            if n["node_id"] == node_id:
                target_node = n
                break

        if not target_node:
            return 2, None

        # 0. 一致性守卫：战斗节点 param=0（客户端据此走"免战"分支）但免战次数已耗尽。
        #    此时若放行，客户端 GoToNextWindow 会带着 param=0 进 GotoRogueTeamReserve(section=0)
        #    直接崩。这里把残留的 param 修回真实关卡 id，回 result=2 让玩家重点一次。
        _pre_attr = {a["attr_id"]: a["value"] for a in json.loads(s.get("attr_list_json") or "[]")}
        _is_battle_node = target_node.get("node_type") in (
            RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE,
            RogueTeamConst.NODE_TYPE_BATTLE_DEMON)
        if _is_battle_node and int(target_node.get("param", 0) or 0) == 0 and _pre_attr.get(RogueTeamConst.ATTR_BATTLE_EXEMPT, 0) <= 0:
            for n in map_info:
                if n.get("node_type") in (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE) \
                        and n.get("state") != RogueTeamConst.NODE_STATE_CLEAN and int(n.get("param", 0) or 0) == 0:
                    n["param"] = 4101101 if n.get("node_type") == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL else 4101201
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "map_info_json": json.dumps(map_info, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))
            return 2, {"map_info": [self._norm_node(n) for n in map_info]}

        # 1. 更新选中节点状态
        #    真·战斗节点（param != 0）必须保持 UNCLEAN：客户端 ChallengeRogueTeamAction.TriggerBattle
        #    的进战斗前置条件就是 node.state == NODE_STATE.UNCLEAN，提前置 CLEAN 会导致点了节点
        #    却进不去战斗。它的 CLEAN/解锁后续节点由 settle_battle_node（战斗结算）负责。
        _real_battle = _is_battle_node and int(target_node.get("param", 0) or 0) != 0
        if not _real_battle:
            target_node["state"] = RogueTeamConst.NODE_STATE_CLEAN

        # 2. 同列其他节点全部置为 OVER (3)，禁止同列多选
        for n in map_info:
            if n.get("col") == target_node.get("col") and n["node_id"] != target_node["node_id"]:
                if n.get("state") != RogueTeamConst.NODE_STATE_CLEAN:
                    n["state"] = RogueTeamConst.NODE_STATE_OVER

        # 3. 仅解锁该节点下一跳中的 LOCK 节点（战斗节点等打赢后再解锁）
        if not _real_battle:
            for next_id in target_node.get("next_id_list", []):
                for n in map_info:
                    if n["node_id"] == next_id and n.get("state") == RogueTeamConst.NODE_STATE_LOCK:
                        n["state"] = RogueTeamConst.NODE_STATE_UNCLEAN

        shop_info = {}
        other_info = {}
        attr_list = json.loads(s.get("attr_list_json") or "[]")
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        heroes = json.loads(s.get("hero_list_json") or "[]")

        ntype = target_node.get("node_type", RogueTeamConst.NODE_TYPE_BATTLE_NORMAL)
        is_exempt_battle = (ntype in (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE)
                            and int(target_node.get("param", 0) or 0) == 0
                            and attr_dict.get(RogueTeamConst.ATTR_BATTLE_EXEMPT, 0) > 0)

        if is_exempt_battle:
            # 势如破竹：免战直接胜利结算！
            # 1. 扣减免战次数
            rem_exempt = max(0, attr_dict.get(RogueTeamConst.ATTR_BATTLE_EXEMPT, 0) - 1)
            attr_dict[RogueTeamConst.ATTR_BATTLE_EXEMPT] = rem_exempt

            # 若免战次数用尽，将后续未通关的免战节点还原为战斗关卡
            if rem_exempt == 0:
                for n in map_info:
                    if n.get("node_type") in (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE) and n.get("state") != RogueTeamConst.NODE_STATE_CLEAN and n.get("param", 0) == 0:
                        n["param"] = 4101101 if n.get("node_type") == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL else 4101201

            # 2. 步数推进与环境演化
            self.advance_room_step(uid, s, ntype, template_id)
            b_cnt = s.get("battle_passed_count", 1)

            # 3. 战后金币发放
            base_gold = 80 if ntype == RogueTeamConst.NODE_TYPE_BATTLE_ELITE else 40
            if attr_dict.get(RogueTeamConst.ATTR_NO_GOLD_GAIN) == 1:
                gold_gain = 0
            else:
                gold_rate = attr_dict.get(RogueTeamConst.ATTR_GOLD_GAIN_RATE, 1000) / 1000.0
                buff_rate = 1.0 + (attr_dict.get(RogueTeamConst.ATTR_GOLD_GAIN_BUFF_PERCENT, 0) / 1000.0)
                if ntype == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL:
                    buff_rate += (attr_dict.get(RogueTeamConst.ATTR_NORMAL_BATTLE_GOLD_BUFF, 0) / 1000.0)
                gold_gain = int(base_gold * gold_rate * buff_rate)

            attr_dict[RogueTeamConst.ATTR_GOLD] = attr_dict.get(RogueTeamConst.ATTR_GOLD, 0) + gold_gain

            # 4. 战后全员回血
            heal_percent = attr_dict.get(RogueTeamConst.ATTR_HP_RECOVERY_RATE, 0)
            has_vitality_spring = any((t.get("id") == 1310010 if isinstance(t, dict) else t == 1310010) for t in treasures)
            if has_vitality_spring:
                heal_percent += 300
            if heal_percent > 0:
                for h in heroes:
                    if h.get("hp_ratio", 0) > 0:
                        h["hp_ratio"] = min(10000, h["hp_ratio"] + heal_percent * 10)

            # 5. 构造掉落三选一
            bonus_epic = attr_dict.get(RogueTeamConst.ATTR_TREASURE_WEIGHT_3, 50) - 50
            bonus_legend = attr_dict.get(RogueTeamConst.ATTR_TREASURE_WEIGHT_4, 10) - 10
            pop_event = RogueTeamConst.POP_EVENT_TREASURE
            drop_type = 1

            if ntype == RogueTeamConst.NODE_TYPE_BATTLE_ELITE:
                roll = random.random()
                if roll < 0.35:
                    pop_event = RogueTeamConst.POP_EVENT_RELIC
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_RELIC, count=3, rare=2) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_RELIC, count=3)
                elif roll < 0.70:
                    pop_event = RogueTeamConst.POP_EVENT_TREASURE
                    q = max(2, self.config.roll_treasure_quality(bonus_epic, bonus_legend))
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=q) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3)
                else:
                    pop_event = RogueTeamConst.POP_EVENT_MECHANISM
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_MECHANISM, count=3)
            else:
                roll = random.random()
                if roll < 0.35:
                    pop_event = RogueTeamConst.POP_EVENT_MECHANISM
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_MECHANISM, count=3)
                else:
                    pop_event = RogueTeamConst.POP_EVENT_TREASURE
                    q = self.config.roll_treasure_quality(bonus_epic, bonus_legend)
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=q) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3)

                if attr_dict.get(RogueTeamConst.ATTR_EXTRA_TREASURE_DROP) == 1 and b_cnt <= 2:
                    drop_type = 2

            if not reward_items:
                reward_items = [1410001, 1410002, 1410003]

            param_list = []
            for i, itm_id in enumerate(reward_items):
                item_obj = self.config.get_item(itm_id) or {}
                # 外接程序标准化品质为 1
                r_val = 1 if (item_obj.get("type") == 2 or str(itm_id).startswith("12")) else (item_obj.get("rare") or item_obj.get("sub_type", 1) or 1)
                param_list.append({
                    "index": i + 1,
                    "param": int(itm_id),
                    "rare": int(r_val),
                    "is_new": 1
                })

            other_info = {
                "event_type": pop_event,
                "param_list": param_list,
                "drop_type": drop_type,
                # 天赋 10399「战功延展」：drop_type=2 表示这轮选完后还要再给一次弹窗，
                # 由 commit_event_selection 读取 _extra_draw 续推 sc_88015（内部字段，不入协议）
                "_extra_draw": 1 if drop_type == 2 else 0
            }

        elif ntype == RogueTeamConst.NODE_TYPE_SHOP:
            # 步数推进
            self.advance_room_step(uid, s, ntype, template_id)
            shop_data = self.generate_shop_data(uid, template_id)
            shop_info = shop_data
        elif ntype == RogueTeamConst.NODE_TYPE_REST:
            self.advance_room_step(uid, s, ntype, template_id)
            event_id = target_node.get("param") or 50010
            hist_events, hist_options = self._get_session_history(s)
            hist_events.add(event_id)
            unlocked_opts = self.filter_unlocked_options(uid, event_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id)
            other_info = {
                "event_id": event_id,
                "opt_list": [self._build_option_entry(opt) for opt in unlocked_opts],
                "trigger_type": 1
            }
            self._save_session_history(other_info, hist_events, hist_options)
        elif ntype in (RogueTeamConst.NODE_TYPE_EVENT, RogueTeamConst.NODE_TYPE_PLOT):
            self.advance_room_step(uid, s, ntype, template_id)
            event_id = target_node.get("param") or 10010
            hist_events, hist_options = self._get_session_history(s)
            hist_events.add(event_id)
            unlocked_opts = self.filter_unlocked_options(uid, event_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id)
            other_info = {
                "event_id": event_id,
                "opt_list": [self._build_option_entry(opt) for opt in unlocked_opts],
                "trigger_type": 1
            }
            self._save_session_history(other_info, hist_events, hist_options)

        # 保存 Session
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "select_node_id": node_id,
            "room_passed_count": s.get("room_passed_count", 0),
            "battle_passed_count": s.get("battle_passed_count", 0),
            "map_info_json": json.dumps(map_info, ensure_ascii=False),
            "other_info_json": json.dumps(other_info, ensure_ascii=False),
            "shop_info_json": json.dumps(shop_info, ensure_ascii=False),
            "treasure_list_json": s.get("treasure_list_json", "[]"),
            "hero_list_json": json.dumps(heroes, ensure_ascii=False),
            "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        res_attr_list = [{"attr_id": k, "value": v} for k, v in attr_dict.items()]
        return 0, {
            "map_info": [{"col": n["col"], "row": n["row"], "node_id": n["node_id"], "node_type": n["node_type"], "next_id_list": n.get("next_id_list", []), "state": n["state"], "param": n.get("param", 0)} for n in map_info],
            "shop_info": shop_info,
            "other_info": other_info,
            "attr_list": res_attr_list
        }

    def set_selected_node(self, uid, node_id, template_id=RogueTeamConst.TEMPLATE_ID):
        """设置/记录当前选中的地图节点 ID"""
        db = self._db()
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "select_node_id": node_id,
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0

    def advance_room_step(self, uid, session, node_type, template_id=RogueTeamConst.TEMPLATE_ID):
        """
        步数环境演进引擎：
        - 步数计数器递增 (room_passed_count / battle_passed_count)
        - 科技 11103 (高瞻远瞩)：每过 5 间房升级 1 个藏品稀有度
        - 藏品 1310022 (苦修)：每过 3 间房升级 1 个藏品稀有度
        - 进房环境触发：走私商 1310002 进非商店房间 +15 金币；招财钱罐 1310056 进普通战斗 +80% 掉落金币
        """
        r_cnt = session.get("room_passed_count", 0) + 1
        b_cnt = session.get("battle_passed_count", 0)
        if node_type in (RogueTeamConst.NODE_TYPE_BATTLE_NORMAL, RogueTeamConst.NODE_TYPE_BATTLE_ELITE, RogueTeamConst.NODE_TYPE_BATTLE_BOSS, RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS):
            b_cnt += 1

        session["room_passed_count"] = r_cnt
        session["battle_passed_count"] = b_cnt

        treasures = json.loads(session.get("treasure_list_json") or "[]")
        attr_list = json.loads(session.get("attr_list_json") or "[]")
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}

        # 1. 检查科技 11103 (每 5 房升阶藏品)
        db = self._db()
        unlocked_trees = {r["node_id"] for r in db.query("SELECT node_id FROM rogueteam_tree WHERE uid=? AND template_id=?", (uid, template_id))}
        if 11103 in unlocked_trees and r_cnt % 5 == 0:
            for t in treasures:
                if isinstance(t, dict) and t.get("rare", 1) == 1:
                    tid = t.get("id")
                    item_cfg = self.config.get_item(tid) or {}
                    if item_cfg.get("sub_type") == 1:
                        t["rare"] = 2
                        break

        # 2. 检查藏品 1310022 (苦修: 每 3 房升阶藏品)
        has_ascetic = any((t.get("id") == 1310022 if isinstance(t, dict) else t == 1310022) for t in treasures)
        if has_ascetic:
            attr_dict[RogueTeamConst.ATTR_NO_GOLD_GAIN] = 1
            if r_cnt % 3 == 0:
                for t in treasures:
                    if isinstance(t, dict) and t.get("rare", 1) < 3:
                        tid = t.get("id")
                        item_cfg = self.config.get_item(tid) or {}
                        if item_cfg.get("sub_type") == 1:
                            t["rare"] += 1
                            break

        # 3. 检查进房道具效果
        has_smuggler = any((t.get("id") == 1310002 if isinstance(t, dict) else t == 1310002) for t in treasures)
        if has_smuggler and node_type != RogueTeamConst.NODE_TYPE_SHOP:
            attr_dict[RogueTeamConst.ATTR_GOLD] = attr_dict.get(RogueTeamConst.ATTR_GOLD, 0) + 15

        has_piggy = any((t.get("id") == 1310056 if isinstance(t, dict) else t == 1310056) for t in treasures)
        if has_piggy and node_type == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL:
            attr_dict[RogueTeamConst.ATTR_NORMAL_BATTLE_GOLD_BUFF] = 800  # +80%

        session["treasure_list_json"] = json.dumps(treasures, ensure_ascii=False)
        session["attr_list_json"] = json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False)

    def settle_battle_node(self, uid, node_id, win=True, template_id=RogueTeamConst.TEMPLATE_ID):
        """
        结算虚构推演战斗节点：
        - 解锁后续节点状态
        - 步数推进与环境演化
        - 精英关 vs 普通关掉落物类型与品质权重抽取
        - 金币倍率乘算（基础 + 科技 + 道具 + 词缀）
        - 战后全员 HP 恢复计算
        - 额外掉落（科技 10399 战功延展 / 藏品 1310017 赏金令）
        """
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {"treasure_num": 0, "relic_num": 0, "coin_num": 0}, {}
        
        map_info = json.loads(s.get("map_info_json") or "[]")
        target_node = None
        for n in map_info:
            if n["node_id"] == node_id:
                target_node = n
                break
        
        if not target_node:
            return 2, {"treasure_num": 0, "relic_num": 0, "coin_num": 0}, {}

        if win:
            target_node["state"] = RogueTeamConst.NODE_STATE_CLEAN
            # 同列其他节点全部置为 OVER (3)
            for n in map_info:
                if n.get("col") == target_node.get("col") and n["node_id"] != target_node["node_id"]:
                    if n.get("state") != RogueTeamConst.NODE_STATE_CLEAN:
                        n["state"] = RogueTeamConst.NODE_STATE_OVER

            # 仅解锁下一跳中的 LOCK 节点
            for next_id in target_node.get("next_id_list", []):
                for n in map_info:
                    if n["node_id"] == next_id:
                        if n["state"] == RogueTeamConst.NODE_STATE_LOCK:
                            n["state"] = RogueTeamConst.NODE_STATE_UNCLEAN
            
            ntype = target_node.get("node_type", RogueTeamConst.NODE_TYPE_BATTLE_NORMAL)
            
            # 步数推进
            self.advance_room_step(uid, s, ntype, template_id)
            
            attr_list = json.loads(s.get("attr_list_json") or "[]")
            attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
            treasures = json.loads(s.get("treasure_list_json") or "[]")
            heroes = json.loads(s.get("hero_list_json") or "[]")
            b_cnt = s.get("battle_passed_count", 1)

            # 1. 战后金币计算
            base_gold = 40
            if ntype == RogueTeamConst.NODE_TYPE_BATTLE_ELITE:
                base_gold = 80
            elif ntype == RogueTeamConst.NODE_TYPE_BATTLE_BOSS:
                base_gold = 150
            elif ntype == RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS:
                base_gold = 300

            # 苦修判断
            if attr_dict.get(RogueTeamConst.ATTR_NO_GOLD_GAIN) == 1:
                gold_gain = 0
            else:
                gold_rate = attr_dict.get(RogueTeamConst.ATTR_GOLD_GAIN_RATE, 1000) / 1000.0
                buff_rate = 1.0 + (attr_dict.get(RogueTeamConst.ATTR_GOLD_GAIN_BUFF_PERCENT, 0) / 1000.0)
                if ntype == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL:
                    buff_rate += (attr_dict.get(RogueTeamConst.ATTR_NORMAL_BATTLE_GOLD_BUFF, 0) / 1000.0)
                gold_gain = int(base_gold * gold_rate * buff_rate)

            attr_dict[RogueTeamConst.ATTR_GOLD] = attr_dict.get(RogueTeamConst.ATTR_GOLD, 0) + gold_gain

            # 2. 战后全队回血计算 (Attr 18 科技回血 + 活力泉水 1310010)
            heal_percent = attr_dict.get(RogueTeamConst.ATTR_HP_RECOVERY_RATE, 0)
            has_vitality_spring = any((t.get("id") == 1310010 if isinstance(t, dict) else t == 1310010) for t in treasures)
            if has_vitality_spring:
                heal_percent += 300  # +30%
            if heal_percent > 0:
                for h in heroes:
                    if h.get("hp_ratio", 0) > 0:
                        h["hp_ratio"] = min(10000, h["hp_ratio"] + heal_percent * 10)

            # 3. 掉落物类型与品质权重生成
            bonus_epic = attr_dict.get(RogueTeamConst.ATTR_TREASURE_WEIGHT_3, 50) - 50
            bonus_legend = attr_dict.get(RogueTeamConst.ATTR_TREASURE_WEIGHT_4, 10) - 10
            
            pop_event = RogueTeamConst.POP_EVENT_TREASURE
            reward_items = []
            drop_stat = {"treasure_num": 1, "relic_num": 0, "coin_num": gold_gain}
            drop_type = 1

            if ntype == RogueTeamConst.NODE_TYPE_BATTLE_ELITE:
                # 精英关：35% 圣物, 35% 稀有/史诗藏品, 30% 外接程序
                roll = random.random()
                if roll < 0.35:
                    pop_event = RogueTeamConst.POP_EVENT_RELIC
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_RELIC, count=3, rare=2) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_RELIC, count=3)
                    drop_stat = {"treasure_num": 0, "relic_num": 1, "coin_num": gold_gain}
                elif roll < 0.70:
                    pop_event = RogueTeamConst.POP_EVENT_TREASURE
                    q = max(2, self.config.roll_treasure_quality(bonus_epic, bonus_legend))
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=q) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3)
                    drop_stat = {"treasure_num": 1, "relic_num": 0, "coin_num": gold_gain}
                else:
                    pop_event = RogueTeamConst.POP_EVENT_MECHANISM
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_MECHANISM, count=3)
                    drop_stat = {"treasure_num": 1, "relic_num": 0, "coin_num": gold_gain}

                # 检查赏金令 (1310017: 击败精英额外奖励)
                has_bounty = any((t.get("id") == 1310017 if isinstance(t, dict) else t == 1310017) for t in treasures)
                if has_bounty:
                    drop_stat["relic_num"] += 1
            elif ntype in (RogueTeamConst.NODE_TYPE_BATTLE_BOSS, RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS):
                # Boss关：必出史诗/传说藏品或高级外接程序
                roll = random.random()
                if roll < 0.5:
                    pop_event = RogueTeamConst.POP_EVENT_TREASURE
                    q = max(3, self.config.roll_treasure_quality(bonus_epic + 50, bonus_legend + 30))
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=q) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3)
                else:
                    pop_event = RogueTeamConst.POP_EVENT_MECHANISM
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_MECHANISM, count=3)
                drop_stat = {"treasure_num": 1, "relic_num": 1, "coin_num": gold_gain}
            else:
                # 普通关：65% 藏品 (权重抽取品质), 35% 外接程序
                roll = random.random()
                if roll < 0.35:
                    pop_event = RogueTeamConst.POP_EVENT_MECHANISM
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_MECHANISM, count=3)
                    drop_stat = {"treasure_num": 1, "relic_num": 0, "coin_num": gold_gain}
                else:
                    pop_event = RogueTeamConst.POP_EVENT_TREASURE
                    q = self.config.roll_treasure_quality(bonus_epic, bonus_legend)
                    reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3, rare=q) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=3)
                    drop_stat = {"treasure_num": 1, "relic_num": 0, "coin_num": gold_gain}

                # 科技 10399 战功延展：前 2 次战斗胜利额外多获得 1 次藏品
                if attr_dict.get(RogueTeamConst.ATTR_EXTRA_TREASURE_DROP) == 1 and b_cnt <= 2:
                    drop_type = 2  # 双重掉落选牌标记
                    drop_stat["treasure_num"] = 2

            # 兜底
            if not reward_items:
                if pop_event == RogueTeamConst.POP_EVENT_MECHANISM:
                    reward_items = [1200001, 1200002, 1200003]
                elif pop_event == RogueTeamConst.POP_EVENT_RELIC:
                    reward_items = [1310001, 1310002, 1310003]
                else:
                    reward_items = [1410001, 1410002, 1410003]

            param_list = []
            for i, itm_id in enumerate(reward_items):
                item_obj = self.config.get_item(itm_id) or {}
                r_val = self.config.get_item_default_rare(itm_id, item_obj)
                param_list.append({
                    "index": i + 1,
                    "param": int(itm_id),
                    "rare": int(r_val),
                    "is_new": 1
                })

            other_info = {
                "event_type": pop_event,
                "param_list": param_list,
                "drop_type": drop_type,
                # 天赋 10399「战功延展」：drop_type=2 表示这轮选完后还要再给一次弹窗，
                # 由 commit_event_selection 读取 _extra_draw 续推 sc_88015（内部字段，不入协议）
                "_extra_draw": 1 if drop_type == 2 else 0
            }

            # 保存 Session
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "select_node_id": 0,
                "room_passed_count": s.get("room_passed_count", 0),
                "battle_passed_count": b_cnt,
                "map_info_json": json.dumps(map_info, ensure_ascii=False),
                "other_info_json": json.dumps(other_info, ensure_ascii=False),
                "treasure_list_json": s.get("treasure_list_json", "[]"),
                "hero_list_json": json.dumps(heroes, ensure_ascii=False),
                "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

            return 0, drop_stat, self._norm_event(other_info)
        return 0, {"treasure_num": 0, "relic_num": 0, "coin_num": 0}, {}

    def next_floor(self, uid, sign=0, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, None

        cur_floor = s.get("floor_num", 1)
        diff = s.get("difficult", 1)

        if cur_floor < 3:
            new_floor = cur_floor + 1
            new_map = RogueTeamMapGenerator.generate_floor_map(floor_num=new_floor, difficult=diff)
            # 新层继承剩余免战次数的可跳过标记
            _attr = {a["attr_id"]: a["value"] for a in json.loads(s.get("attr_list_json") or "[]")}
            self._apply_exempt_marks(new_map, _attr.get(RogueTeamConst.ATTR_BATTLE_EXEMPT, 0))
            # 新层还没选过节点：select_node_id 必须归 0，
            # 否则客户端会以为已经站在某个节点上（层数/前沿指针错乱的来源之一）
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "floor_num": new_floor,
                "floor_state": RogueTeamConst.FLOOR_STATE_NORMAL,
                "select_node_id": 0,
                "map_info_json": json.dumps(new_map, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))
            return 0, self.get_session_data(uid, template_id)
        else:
            return 0, self.get_session_data(uid, template_id)

    def select_item_reward(self, uid, event_id, param_arg, template_id=RogueTeamConst.TEMPLATE_ID):
        """处理 88200 道具/藏品/事件三选一领取"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}

        other_info = json.loads(s.get("other_info_json") or "{}")
        param_list = other_info.get("param_list", [])

        target_item_id = param_arg
        for p in param_list:
            if p.get("index") == param_arg:
                target_item_id = p.get("param", param_arg)
                break

        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")

        item_cfg = self.config.get_item(target_item_id)
        itype = item_cfg.get("type") if item_cfg else None

        is_treasure = (itype == RogueTeamConst.ITEM_TYPE_TREASURE or itype == 4 or (itype is None and (str(target_item_id).startswith("14") or 1400000 <= target_item_id < 1500000)))
        
        if is_treasure:
            rare_val = self.config.get_item_default_rare(target_item_id, item_cfg)
            treasures.append({"id": int(target_item_id), "rare": int(rare_val)})
            self.unlock_collection_item(uid, template_id, target_item_id, 1)
        else:
            other_items.append({"id": int(target_item_id), "value": 1})
            # 注意：unlock_collection_item 的第 4 参是 **ITEM_TYPE**（2机制/3圣物/4藏品），
            # 内部再转 COLLECTION_TYPE（1藏品/2事件/3圣物/4机制）。传错会把机制记成藏品，
            # 客户端藏品图鉴 RogueTeamItemCfg[id].camp 会崩。
            self.unlock_collection_item(uid, template_id, target_item_id, itype or RogueTeamConst.ITEM_TYPE_RELIC)

        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "treasure_list_json": json.dumps(treasures, ensure_ascii=False),
            "other_item_list_json": json.dumps(other_items, ensure_ascii=False),
            "other_info_json": "{}",
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        res_item_list = self._bag_item_list(treasures, other_items)
        return 0, {"item_list": res_item_list, "window_opt": True}

    def _get_session_history(self, s):
        """获取局内经历过的事件与已选选项历史记录"""
        other = json.loads(s.get("other_info_json") or "{}") if s else {}
        hist = other.get("_history") or {}
        return set(hist.get("events", [])), set(hist.get("options", []))

    def _save_session_history(self, other_dict, history_events, history_options):
        """将局内历史记录持久化保存至 other_info 内部隐藏字段"""
        if isinstance(other_dict, dict):
            other_dict["_history"] = {
                "events": list(history_events),
                "options": list(history_options)
            }

    def evaluate_condition(self, uid, cond_id, s, attr_dict, treasures, other_items, heroes, history_events, history_options, template_id=RogueTeamConst.TEMPLATE_ID):
        """通用事件与选项前置条件校验器 (接入 rogueteamconditioncfg 全量条件规则)"""
        cond = self.config.get_condition(cond_id)
        if not cond:
            return True

        ctype = cond.get("type", 0)
        params = cond.get("params", [])

        if ctype in (101, 105):
            # 拥有 cnt 个 camp 流派外接程序 (camp == 0 为任意流派)
            camp = params[0] if len(params) > 0 else 0
            cnt = params[1] if len(params) > 1 else 1
            matched = 0
            for t in treasures:
                tid = t.get("id") if isinstance(t, dict) else t
                icfg = self.config.get_item(tid) or {}
                if icfg.get("type") in (2, 4) or str(tid).startswith("14") or str(tid).startswith("12"):
                    if camp == 0 or icfg.get("sub_type") == camp:
                        matched += 1
            return matched >= cnt

        elif ctype == 102:
            # 曾选择过选项 opt_id
            opt_id = params[0] if params else 0
            return opt_id in history_options

        elif ctype == 103:
            # 曾经历过事件 event_id
            ev_id = params[0] if params else 0
            return ev_id in history_events

        elif ctype == 104:
            # 需通关第 battle_idx 场普通战斗
            b_idx = params[0] if params else 1
            b_cnt = s.get("battle_passed_count", 0) if s else 0
            return b_cnt >= b_idx

        elif ctype == 106:
            # 需持有 val 点 属性 attr_id (如 6 微光晶砾, 7 重置点数, 1 见闻值, 4 复活点数)
            attr_id = params[0] if len(params) > 0 else 1
            val = params[1] if len(params) > 1 else 0
            cur = attr_dict.get(attr_id, 0)
            return cur >= val

        elif ctype == 107:
            # 拥有 cnt 个稀有度 rare 的藏品 / 收集品 (rare=99 非剧情, rare=2 负面, rare=1 普通)
            rare = params[0] if len(params) > 0 else 1
            cnt = params[1] if len(params) > 1 else 1
            matched = 0
            if rare == 99:
                for itm in other_items:
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    icfg = self.config.get_item(iid) or {}
                    if icfg.get("sub_type") != 3:
                        matched += 1
                for _ in treasures:
                    matched += 1
            elif rare == 2:
                for itm in other_items:
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    icfg = self.config.get_item(iid) or {}
                    if icfg.get("sub_type") == 2 or str(iid).startswith("132"):
                        matched += 1
            elif rare == 1:
                for itm in other_items:
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    icfg = self.config.get_item(iid) or {}
                    if icfg.get("sub_type") == 1 or str(iid).startswith("131"):
                        matched += 1
            return matched >= cnt

        elif ctype == 108:
            # 拥有指定物品 item_id (如结局剧情圣物 1330002 / 1330003)
            item_id = params[0] if params else 0
            for itm in other_items:
                iid = itm.get("id") if isinstance(itm, dict) else itm
                if iid == item_id:
                    return True
            for t in treasures:
                tid = t.get("id") if isinstance(t, dict) else t
                if tid == item_id:
                    return True
            return False

        elif ctype in (109, 113):
            # 拥有前置藏品之一 / 复合前置藏品
            for req_id in params:
                for itm in other_items:
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    if iid == req_id:
                        return True
                for t in treasures:
                    tid = t.get("id") if isinstance(t, dict) else t
                    if tid == req_id:
                        return True
            return False

        elif ctype == 110:
            # 复活点数未达到上限 (达上限则锁定)
            cur_attr = params[0] if len(params) > 0 else 4
            max_attr = params[1] if len(params) > 1 else 5
            cur_val = attr_dict.get(cur_attr, 0)
            max_val = attr_dict.get(max_attr, 5)
            return cur_val < max_val

        elif ctype == 111:
            # 存在可提升稀有度的外接程序 (无不可升级程序则锁定)
            has_upgradeable = False
            for t in treasures:
                if isinstance(t, dict):
                    tid = t.get("id", 0)
                    icfg = self.config.get_item(tid) or {}
                    if icfg.get("sub_type") == 1 and t.get("rare", 1) < 3:
                        has_upgradeable = True
                        break
                elif isinstance(t, int):
                    icfg = self.config.get_item(t) or {}
                    if icfg.get("sub_type") == 1:
                        has_upgradeable = True
                        break
            return has_upgradeable

        elif ctype == 112:
            # 拥有 cnt 个稀有度 rare 的外接程序
            target_rare = params[0] if len(params) > 0 else 1
            cnt = params[1] if len(params) > 1 else 1
            matched = sum(1 for t in treasures if isinstance(t, dict) and t.get("rare", 1) == target_rare)
            return matched >= cnt

        elif ctype == 115:
            # 队伍中存在指定修正者且生命值 <= hp_threshold
            hp_limit = params[0] if len(params) > 0 else 10000
            hero_ids = set(params[1:]) if len(params) > 1 else set()
            for h in heroes:
                hid = h.get("hero_id", 0)
                hp = h.get("hp_ratio", 10000)
                if (not hero_ids or hid in hero_ids) and hp <= hp_limit:
                    return True
            return False

        elif ctype == 201:
            # 处于难度 diff 或以上
            diff = params[0] if params else 1
            cur_diff = s.get("difficult", 1) if s else 1
            return cur_diff >= diff

        elif ctype == 202:
            # 通关结局 ending_id 至少 cnt 次
            ending_id = params[0] if len(params) > 0 else 1
            cnt = params[1] if len(params) > 1 else 1
            db = self._db()
            his_row = db.get("rogueteam_history", uid, "AND template_id=?", (template_id,))
            ending_list = json.loads(his_row.get("ending_pass_json") or "[]") if his_row else []
            clears = sum(e.get("value", 0) for e in ending_list if e.get("key") == ending_id)
            return clears >= cnt

        elif ctype == 204:
            # 解锁科技树节点 tech_id
            tech_id = params[0] if params else 0
            db = self._db()
            user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,)) or {}
            unlocked_tree = json.loads(user_row.get("unlocked_tree_json") or "[]")
            return tech_id in unlocked_tree

        elif ctype == 205:
            # 单局机制值/见闻值累计达到 val
            attr_id = params[0] if len(params) > 0 else 1
            val = params[1] if len(params) > 1 else 0
            cur = attr_dict.get(attr_id, 0)
            return cur >= val

        return True

    def is_option_unlocked(self, uid, opt_id, s, attr_dict, treasures, other_items, heroes, history_events, history_options, template_id=RogueTeamConst.TEMPLATE_ID):
        """判断单个选项的所有前置条件是否均满足"""
        opt_cfg = self.config.get_event_option(opt_id)
        if not opt_cfg:
            return True
        cond_list = opt_cfg.get("condition_list", [])
        for cid in cond_list:
            if not self.evaluate_condition(uid, cid, s, attr_dict, treasures, other_items, heroes, history_events, history_options, template_id):
                return False
        return True

    def filter_unlocked_options(self, uid, event_id, s, attr_dict, treasures, other_items, heroes, history_events=None, history_options=None, template_id=RogueTeamConst.TEMPLATE_ID):
        """为目标事件提取满足条件的可选选项列表"""
        ev_cfg = self.config.get_event(event_id)
        if not ev_cfg:
            return []
        opt_list = ev_cfg.get("option_list", [])
        if history_events is None or history_options is None:
            history_events, history_options = self._get_session_history(s)
        unlocked = []
        for opt_id in opt_list:
            if self.is_option_unlocked(uid, opt_id, s, attr_dict, treasures, other_items, heroes, history_events, history_options, template_id):
                unlocked.append(opt_id)
        return unlocked

    def _build_option_entry(self, opt_id):
        """构建事件选项下发数据 (包含道具预览/Tips绑定，用于替换客户端 desc 中的 %s 并展示道具卡片)"""
        OPTION_ITEM_PREVIEWS = {
            100901: [{"id": 1310023, "rare": 1}],  # 《共同发展协议》
            100902: [{"id": 1310028, "rare": 1}],  # 《装置数据报告》
            100911: [{"id": 1310028, "rare": 1}],
            100921: [{"id": 1310023, "rare": 1}],
            100932: [{"id": 1310021, "rare": 1}],  # 《镜·鉴》
            101001: [{"id": 1310027, "rare": 1}],  # 《明灭灯盏》
            101101: [{"id": 1310005, "rare": 1}],  # 《虚恒旧币》
            101401: [{"id": 1310056, "rare": 1}],  # 《猪猪存钱罐》
            101501: [{"id": 1310025, "rare": 1}],  # 《野外防身短刀》
            311001: [{"id": 1310021, "rare": 1}],  # 《镜·鉴》
            311011: [{"id": 1310021, "rare": 1}],  # 《镜·鉴》
            600211: [{"id": 1330001, "rare": 1}],  # 《诡异云丝》
            600421: [{"id": 1330003, "rare": 1}],  # 《维修工具箱》
            600521: [{"id": 1330003, "rare": 1}],
            600531: [{"id": 1330003, "rare": 1}],
        }
        items = list(OPTION_ITEM_PREVIEWS.get(opt_id, []))
        if not items:
            opt_obj = self.config.get_event_option(opt_id)
            if opt_obj:
                for eid in opt_obj.get("effect_ids", []):
                    eff = self.config.get_event_effect(eid)
                    if eff:
                        act = eff.get("action")
                        params = eff.get("params", [])
                        if act == 6 and params:
                            items.append({"id": int(params[0]), "rare": 1})
                        elif act in (8, 1008) and params:
                            iid = int(params[0])
                            icfg = self.config.get_item(iid) or {}
                            items.append({"id": iid, "rare": icfg.get("rare", 1)})
                        elif act == 1053 and params:
                            items.append({"id": int(params[0]), "rare": 1})

        return {
            "opt_id": opt_id,
            "item_id_list": items,
            "stage_id": 0,
            "jump_battle_room_id": 0
        }

    def get_option_jump_event(self, opt_id):
        """查询选项的目标跳转事件 ID (支持 49 类多步分支/序言事件推进)"""
        EVENT_OPTION_JUMPS = {
            # 10000 渐空的茶杯
            100001: 10001, 100002: 10002,
            # 10010 数据统筹
            100101: 10011, 100102: 10012, 100103: 10013,
            # 10040 古朴民居
            100401: 10041, 100402: 10042,
            # 10060 潜存的担忧
            100601: 10061, 100602: 10062, 100603: 10063,
            # 10070 岱屿旧事
            100701: 10071, 100702: 10072,
            # 10080 云间蜃景
            100801: 10081, 100811: 10082, 100812: 10083, 100813: 10084,
            100821: 10085, 100831: 10085, 100841: 10085,
            # 10090 中立立场
            100901: 10091, 100902: 10092,
            100911: 10093, 100912: 10094,
            100921: 10093, 100922: 10094,
            100931: 10094, 100932: 10094, 100933: 10094,
            # 10100 鬼灯轶事
            101001: 10101, 101002: 10102, 101003: 10103,
            # 10110 云外来客
            101101: 10111, 101102: 10112,
            # 10120 喋喋不休
            101201: 10121,
            101211: 10122, 101212: 10123, 101213: 10124,
            101221: 10125, 101222: 10125, 101223: 10125,
            101231: 10125, 101232: 10125, 101233: 10125,
            101241: 10125, 101242: 10125, 101243: 10125,
            # 10140 小小心愿
            101401: 10141,
            # 10150 意外援助
            101501: 10151,
            # 10160 暗巷呓语
            101601: 10161, 101602: 10162,
            # 20040 商贩姐妹？
            200401: 20041, 200402: 20042, 200403: 20043,
            # 20050 失败了失败了失败了
            200501: 20051, 200502: 20052, 200503: 20053,
            200511: 20054, 200512: 20055, 200513: 20056,
            200521: 20054, 200522: 20055, 200523: 20056,
            # 20060 基础问题
            200601: 20061, 200611: 20062, 200612: 20063, 200613: 20064,
            # 20070 祛厄
            200701: 20071, 200711: 20072, 200712: 20073, 200713: 20074,
            # 20080 研究困境
            200801: 20082, 200802: 20081, 200803: 20083,
            # 20090 小型浮岛
            200901: 20091, 200902: 20092,
            # 20100 各取所需
            201001: 20101, 201002: 20102,
            # 20110 前路为何
            201101: 20111, 201102: 20112, 201103: 20113,
            # 20120 沉默的石碑
            201201: 20121, 201202: 20122,
            # 20140 被遗忘的宝物
            201401: 20141, 201411: 20142, 201421: 20143, 201431: 20144,
            # 20150 清仓大甩卖
            201501: 20151, 201511: 20152, 201521: 20153, 201541: 20155, 201551: 20156, 201561: 20157,
            # 20160 三仙归洞
            201601: 20161, 201611: 20162, 201613: 20164,
            # 30010 身临其境
            300101: 30011, 300111: 30012, 300112: 30013, 300121: 30014, 300151: 30016,
            # 30020 深林尖啸
            300201: 30021, 300211: 30022, 300212: 30023, 300221: 30024, 300222: 30025,
            # 30030 眼睛？
            300301: 30031, 300311: 30032, 300321: 30033,
            # 30050 异常增数
            300501: 30051, 300511: 30052, 300512: 30053,
            # 30080 崖边的谜题
            300801: 30081, 300811: 30082, 300821: 30083, 300831: 30084, 300841: 30085, 300851: 30086,
            # 30090 待兴
            300901: 30091, 300911: 30092, 300921: 30093,
            # 31100 流言为真
            311001: 31101, 311002: 31102, 311011: 31102,
            # 40010 街边「茶馆」
            400101: 40011, 400102: 40012,
            # 40020 随行驿站
            400201: 40021,
            # 40040 智械也疯狂
            400401: 40041, 400402: 40042,
            # 40050 直至最后时刻
            400501: 40051,
            400511: 40052, 400512: 40052, 400513: 40052,
            400521: 40053, 400522: 40053, 400523: 40053,
            400531: 40054, 400532: 40054, 400533: 40054,
            400541: 40055, 400542: 40055, 400543: 40055,
            400551: 40056, 400552: 40056, 400553: 40056,
            400561: 40057, 400562: 40057, 400563: 40057,
            # 40060 当铺
            400601: 40061, 400602: 40067, 400603: 40065,
            400611: 40062, 400612: 40063, 400613: 40064,
            400621: 40066, 400622: 40066,
            400631: 40066, 400632: 40066,
            400641: 40066, 400642: 40066,
            400671: 40068, 400672: 40069,
            400681: 40066, 400682: 40066,
            400691: 40066, 400692: 40066,
            # 60010 迫近的风暴
            600101: 60011, 600102: 60012, 600103: 60013, 600104: 60014,
            # 60020 守望
            600201: 60021, 600202: 60022, 600203: 60023,
            600211: 60024, 600212: 60023, 600221: 60023,
            # 60030
            600301: 60031, 600302: 60032,
            # 60040
            600401: 60041, 600402: 60042, 600403: 60042, 600411: 60042,
            # 60050
            600501: 60051, 600502: 60052, 600511: 60053
        }
        return EVENT_OPTION_JUMPS.get(opt_id, 0)

    def _execute_event_effects(self, uid, template_id, s, opt_cfg, attr_dict, treasures, other_items, heroes):
        """执行一个选项绑定的所有特效(返回 new_treasures, new_other_items, update_list, extra)"""
        effect_ids = opt_cfg.get("effect_ids", [])
        new_treasures = []
        new_other_items = []
        update_list = []
        extra = {}

        # 1. 如果选项绑定了特定收集品/信物（如 101101《虚恒旧币》, 101401《猪猪存钱罐》, 100901/100902/101001/101501 等）
        opt_id = opt_cfg.get("id", 0)
        direct_items = self._build_option_entry(opt_id).get("item_id_list", [])
        for p_item in direct_items:
            iid = p_item.get("id")
            if iid:
                has_it = any((itm.get("id") == iid if isinstance(itm, dict) else itm == iid) for itm in other_items)
                if not has_it:
                    other_items.append({"id": int(iid), "value": 1})
                    new_other_items.append(iid)
                    update_list.append({
                        "operate": 1, "id": int(iid), "is_new": 1, "rare": int(p_item.get("rare", 1)),
                        "source_rare": 0, "source_type": 2, "source_item_id": 0
                    })
                    self.unlock_collection_item(uid, template_id, iid, RogueTeamConst.ITEM_TYPE_RELIC)

        if not effect_ids:
            return new_treasures, new_other_items, update_list, extra
            
        for eff_id in effect_ids:
            eff = self.config.get_event_effect(eff_id)
            if not eff:
                continue
                
            action = eff.get("action")
            params = eff.get("params", [])
            
            if action in (1, 1001):
                # 属性修改: params = [op, attr_id, ..., val]
                # 例如 [1, 6, 0, 300] -> 给钱300; [0, 6, 0, -40] -> 扣钱40; [1, 1, 0, 20] -> 机制值+20; [1, 7, 0, 2] -> 重置点数+2
                if len(params) >= 4:
                    op = params[0]
                    attr_id = params[1]
                    val = params[3]
                    cur = attr_dict.get(attr_id, 0)
                    if op == 1:
                        attr_dict[attr_id] = max(0, cur + val)
                    else:
                        attr_dict[attr_id] = max(0, cur - abs(val))
                elif len(params) == 2:
                    attr_id, val = params[0], params[1]
                    attr_dict[attr_id] = max(0, val)

            elif action in (2, 1002):
                # 血量修改: params = [op, val] 
                # [1, 600] -> 全员回血 60%; [0, -150] -> 全员扣血 15%
                if len(params) >= 2:
                    op = params[0]
                    val = params[1]
                    delta = val * 10 if abs(val) <= 1000 else val
                    for h in heroes:
                        cur_hp = h.get("hp_ratio", 10000)
                        if op == 1 or delta > 0:
                            if cur_hp > 0:
                                h["hp_ratio"] = min(10000, cur_hp + abs(delta))
                        else:
                            h["hp_ratio"] = max(1, cur_hp - abs(delta))

            elif action in (6, 1006):
                # 指定获得圣物: params = [relic_id] (例如 1330001, 1330002, 1330003)
                if params:
                    relic_id = params[0]
                    has_it = any((itm.get("id") == relic_id if isinstance(itm, dict) else itm == relic_id) for itm in other_items)
                    if not has_it:
                        other_items.append({"id": int(relic_id), "value": 1})
                        new_other_items.append(relic_id)
                        update_list.append({
                            "operate": 1, "id": int(relic_id), "is_new": 1, "rare": 1,
                            "source_rare": 0, "source_type": 2, "source_item_id": 0
                        })
                        self.unlock_collection_item(uid, template_id, relic_id, RogueTeamConst.ITEM_TYPE_RELIC)

            elif action in (8, 1008):
                # 明确获得道具/圣物: params = [item_id, count]
                if len(params) >= 1:
                    item_id = params[0]
                    cnt = params[1] if len(params) > 1 else 1
                    item_cfg = self.config.get_item(item_id)
                    if item_cfg:
                        itype = item_cfg.get("type", 1)
                        if itype == RogueTeamConst.ITEM_TYPE_TREASURE:
                            r_val = self.config.get_item_default_rare(item_id, item_cfg)
                            for _ in range(cnt):
                                treasures.append({"id": item_id, "rare": r_val})
                                new_treasures.append({"id": item_id, "rare": r_val})
                                update_list.append({
                                    "operate": 1, "id": int(item_id), "is_new": 1, "rare": r_val,
                                    "source_rare": 0, "source_type": 2, "source_item_id": 0
                                })
                        else:
                            for _ in range(cnt):
                                other_items.append({"id": int(item_id), "value": 1})
                                new_other_items.append(item_id)
                                update_list.append({
                                    "operate": 1, "id": int(item_id), "is_new": 1, "rare": 1,
                                    "source_rare": 0, "source_type": 2, "source_item_id": 0
                                })
                        self.unlock_collection_item(uid, template_id, item_id, itype)
                    else:
                        items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=cnt)
                        for it in items:
                            r_val = self.config.get_item_default_rare(it)
                            treasures.append({"id": it, "rare": r_val})
                            new_treasures.append({"id": it, "rare": r_val})
                            update_list.append({
                                "operate": 1, "id": int(it), "is_new": 1, "rare": r_val,
                                "source_rare": 0, "source_type": 2, "source_item_id": 0
                            })
                            self.unlock_collection_item(uid, template_id, it, 1)

            elif action == 9:
                # 丢弃/变卖藏品/外接程序: params = [min_rare, max_rare, count] (如当铺 400611: [1, 1, 1])
                min_r = params[0] if len(params) > 0 else 1
                max_r = params[1] if len(params) > 1 else min_r
                cnt = params[2] if len(params) > 2 else 1
                removed_cnt = 0
                i = len(treasures) - 1
                while i >= 0 and removed_cnt < cnt:
                    t = treasures[i]
                    t_rare = t.get("rare", 1) if isinstance(t, dict) else 1
                    if min_r <= t_rare <= max_r:
                        del_item = treasures.pop(i)
                        tid = del_item.get("id") if isinstance(del_item, dict) else del_item
                        update_list.append({
                            "operate": 2, "id": int(tid), "is_new": 0, "rare": int(t_rare),
                            "source_rare": int(t_rare), "source_type": 2, "source_item_id": 0
                        })
                        removed_cnt += 1
                    i -= 1

            elif action == 10:
                # 丢弃/变卖圣物: params = [sub_type, count] (如当铺 400671: [1], 400672: [2])
                target_sub = params[0] if len(params) > 0 else 1
                cnt = params[1] if len(params) > 1 else 1
                removed_cnt = 0
                i = len(other_items) - 1
                while i >= 0 and removed_cnt < cnt:
                    itm = other_items[i]
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    icfg = self.config.get_item(iid) or {}
                    isub = icfg.get("sub_type", 1)
                    if isub == target_sub:
                        del_itm = other_items.pop(i)
                        update_list.append({
                            "operate": 2, "id": int(iid), "is_new": 0, "rare": 1,
                            "source_rare": 1, "source_type": 2, "source_item_id": 0
                        })
                        removed_cnt += 1
                    i -= 1

            elif action == 12:
                # 提升外接程序稀有度 / 流派强化: params = [count, rare_up] (如 100703: [1, 2], 101213: [1, 1])
                cnt = params[0] if len(params) > 0 else 1
                rare_up = params[1] if len(params) > 1 else 1
                upgraded = 0
                for t in treasures:
                    if isinstance(t, dict) and upgraded < cnt:
                        old_rare = t.get("rare", 1)
                        if old_rare < 3:
                            t["rare"] = min(3, old_rare + rare_up)
                            update_list.append({
                                "operate": 3, "id": int(t["id"]), "is_new": 0, "rare": int(t["rare"]),
                                "source_rare": int(old_rare), "source_type": 2, "source_item_id": 0
                            })
                            upgraded += 1

            elif action in (14, 16, 1016):
                # 随机抽取藏品 / 外接程序: params = [pool_id, min_rare, max_rare, count]
                pool_id = params[0] if len(params) > 0 else 0
                cnt = params[3] if len(params) >= 4 else (params[2] if len(params) >= 3 else (params[1] if len(params) >= 2 else 1))
                req_rare = params[1] if len(params) >= 2 and params[1] in (1, 2, 3, 4) else None
                items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=cnt, rare=req_rare, pool_id=pool_id) or self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=cnt)
                for item_id in items:
                    r_val = req_rare or self.config.get_item_default_rare(item_id)
                    treasures.append({"id": item_id, "rare": r_val})
                    new_treasures.append({"id": item_id, "rare": r_val})
                    update_list.append({
                        "operate": 1, "id": int(item_id), "is_new": 1, "rare": r_val,
                        "source_rare": 0, "source_type": 2, "source_item_id": 0
                    })
                    self.unlock_collection_item(uid, template_id, item_id, 1)

            elif action == 15:
                # 选择并获得收集品 (POP_EVENT_RELIC / 三选一收集品选择): params = [pool_id, count]
                pool_id = params[0] if len(params) > 0 else 301
                cnt = params[1] if len(params) > 1 else 1

                candidates = []
                if pool_id == 314:
                    # 负面效果收集品池 (sub_type == 2 / 1320001~1320006)
                    pool = [int(iid) for iid, itm in self.config.items.items() if itm.get("type") == RogueTeamConst.ITEM_TYPE_RELIC and itm.get("sub_type") == 2]
                    candidates = random.sample(pool, min(3, len(pool))) if pool else [1320001, 1320002, 1320003]
                elif pool_id == 315:
                    candidates = [1310056]
                elif pool_id == 316:
                    candidates = [1310025]
                elif pool_id == 317:
                    candidates = [1310021]
                else:
                    # 普通收集品三选一池 (sub_type == 1)
                    pool = [int(iid) for iid, itm in self.config.items.items() if itm.get("type") == RogueTeamConst.ITEM_TYPE_RELIC and itm.get("sub_type") == 1]
                    candidates = random.sample(pool, min(3, len(pool))) if pool else [1310001, 1310002, 1310003]

                param_list = [{"index": i + 1, "param": iid, "rare": self.config.get_item_default_rare(iid)} for i, iid in enumerate(candidates)]
                extra["pop_window"] = {
                    "event_type": RogueTeamConst.POP_EVENT_RELIC,
                    "param_list": param_list,
                    "drop_type": 1
                }

            elif action == 17:
                # 队伍全员复活并恢复满生命值
                for h in heroes:
                    h["hp_ratio"] = 10000

            elif action == 19:
                # 商店折扣 / 特殊效果
                attr_dict[RogueTeamConst.ATTR_SHOP_DISCOUNT] = 1

            elif action == 20:
                # 清除全队所有负面圣物 (Sub_type == 2 / 132xxxx)
                i = len(other_items) - 1
                while i >= 0:
                    itm = other_items[i]
                    iid = itm.get("id") if isinstance(itm, dict) else itm
                    icfg = self.config.get_item(iid) or {}
                    if icfg.get("sub_type") == 2 or str(iid).startswith("132"):
                        del_itm = other_items.pop(i)
                        update_list.append({
                            "operate": 2, "id": int(iid), "is_new": 0, "rare": 1,
                            "source_rare": 1, "source_type": 2, "source_item_id": 0
                        })
                    i -= 1

            elif action == 21:
                # 免战次数增加 / 推进标记
                val = params[0] if params else 1
                cur_ex = attr_dict.get(RogueTeamConst.ATTR_BATTLE_EXEMPT, 0)
                attr_dict[RogueTeamConst.ATTR_BATTLE_EXEMPT] = cur_ex + val

            elif action == 22:
                # 触发世界线跃迁 / 剧情分支变更
                if params:
                    worldline_id = params[0]
                    extra["world_line_id"] = worldline_id

            elif action == 1051:
                # 达成结局分支
                if params:
                    ending_id = params[0]
                    extra["ending_id"] = ending_id

            elif action == 1053:
                # 指定获得机制道具: params = [mechanism_id]
                if params:
                    m_id = params[0]
                    has_it = any((itm.get("id") == m_id if isinstance(itm, dict) else itm == m_id) for itm in other_items)
                    if not has_it:
                        other_items.append({"id": int(m_id), "value": 1})
                        new_other_items.append(m_id)
                        update_list.append({
                            "operate": 1, "id": int(m_id), "is_new": 1, "rare": 1,
                            "source_rare": 0, "source_type": 2, "source_item_id": 0
                        })
                        self.unlock_collection_item(uid, template_id, m_id, RogueTeamConst.ITEM_TYPE_MECHANISM)

            elif action == 1054:
                # 全队最大生命值提升
                if params:
                    val = params[0]
                    cur_max = attr_dict.get(RogueTeamConst.ATTR_HERO_MAX_HP_PERCENT, 1000)
                    attr_dict[RogueTeamConst.ATTR_HERO_MAX_HP_PERCENT] = cur_max + val

        return new_treasures, new_other_items, update_list, extra

    def choose_event_option(self, uid, opt_id, template_id=RogueTeamConst.TEMPLATE_ID):
        """处理 88024 奇遇/安全屋事件分支推进"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}

        opt_id = int(opt_id or 0)
        attr_list = json.loads(s.get("attr_list_json") or "[]")
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        heroes = json.loads(s.get("hero_list_json") or "[]")
        map_info = json.loads(s.get("map_info_json") or "[]")

        # ----------------------------------------------------
        # 1. opt_id == 0: 离开事件 / 安全屋收尾 / 节点结算
        # ----------------------------------------------------
        if opt_id == 0:
            select_node_id = s.get("select_node_id", 0)
            for n in map_info:
                if n.get("node_id") == select_node_id or (select_node_id == 0 and n.get("state") == RogueTeamConst.NODE_STATE_CLEAN):
                    n["state"] = RogueTeamConst.NODE_STATE_OVER
                    for next_id in n.get("next_id_list", []):
                        for nn in map_info:
                            if nn.get("node_id") == next_id and nn.get("state") == RogueTeamConst.NODE_STATE_LOCK:
                                nn["state"] = RogueTeamConst.NODE_STATE_UNCLEAN
                    break

            is_clear = self._is_floor_clear(map_info)
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "select_node_id": 0,
                "map_info_json": json.dumps(map_info, ensure_ascii=False),
                "other_info_json": "{}",
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

            return 0, {
                "result": 0,
                "opt_id": 0,
                "map_info": [self._norm_node(n) for n in map_info],
                "next_event": {"event_id": 0, "opt_list": [], "trigger_type": 0},
                "is_floor_clear": is_clear
            }

        # ----------------------------------------------------
        # 2. 安全屋阶段 1：联系增援 (500101)
        # ----------------------------------------------------
        if opt_id == 500101:
            pop_win = {"event_type": RogueTeamConst.POP_EVENT_HERO_RECRUIT, "param_list": []}
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "other_info_json": json.dumps(pop_win, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))
            return 0, {
                "result": 0,
                "opt_id": opt_id,
                "pop_window": pop_win
            }

        # ----------------------------------------------------
        # 3. 安全屋阶段 1：不联系增援 (500102) -> 推进至阶段 2 设施整备 (50011)
        # ----------------------------------------------------
        if opt_id == 500102:
            next_ev = {
                "event_id": 50011,
                "opt_list": [{"opt_id": 500111}, {"opt_id": 500112}, {"opt_id": 500113}],
                "trigger_type": 1
            }
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "other_info_json": json.dumps(next_ev, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))
            return 0, {
                "result": 0,
                "opt_id": opt_id,
                "next_event": next_ev
            }

        # ----------------------------------------------------
        # 4. 安全屋阶段 2：稍事休息 (500111 / 500131)
        #    效果：复活点数+1，全员生命值恢复满血，推进到 50012 离开描述
        # ----------------------------------------------------
        if opt_id in (500111, 500131):
            cur_rev = attr_dict.get(RogueTeamConst.ATTR_REVIVE_CNT, 0)
            lim_rev = attr_dict.get(RogueTeamConst.ATTR_REVIVE_LIMIT_CNT, 5)
            attr_dict[RogueTeamConst.ATTR_REVIVE_CNT] = min(lim_rev, cur_rev + 1)

            for h in heroes:
                if h.get("hp_ratio", 0) > 0:
                    h["hp_ratio"] = 10000

            next_ev = {"event_id": 50012, "opt_list": [], "trigger_type": 1}
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
                "hero_list_json": json.dumps(heroes, ensure_ascii=False),
                "other_info_json": json.dumps(next_ev, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

            return 0, {
                "result": 0,
                "opt_id": opt_id,
                "attr_list": [{"attr_id": k, "value": v} for k, v in attr_dict.items()],
                "hero_list": heroes,
                "next_event": next_ev
            }

        # ----------------------------------------------------
        # 5. 安全屋阶段 2：调试玄机 (500112 / 500132)
        #    效果：随机提升 1 个外接程序稀有度，推进到 50012 离开描述
        #    注：只有 sub_type == 1 的单阵营普通外接程序具有 1->2->3 阶品质成长！
        # ----------------------------------------------------
        if opt_id in (500112, 500132):
            upgradeable = []
            for t in treasures:
                if isinstance(t, dict):
                    tid = t.get("id")
                    item_cfg = self.config.get_item(tid) or {}
                    if item_cfg.get("sub_type") == 1 and t.get("rare", 1) < 3:
                        upgradeable.append(t)

            update_list = []
            if upgradeable:
                target_t = random.choice(upgradeable)
                old_rare = target_t.get("rare", 1)
                target_t["rare"] = old_rare + 1
                update_list = [{
                    "operate": 3,  # ITEM_OPERATE.UPDATE
                    "id": int(target_t["id"]),
                    "rare": int(target_t["rare"]),
                    "is_new": 0,
                    "source_rare": int(old_rare),
                    "source_type": 1,
                    "source_item_id": 0
                }]

            next_ev = {"event_id": 50012, "opt_list": [], "trigger_type": 1}
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "treasure_list_json": json.dumps(treasures, ensure_ascii=False),
                "other_info_json": json.dumps(next_ev, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

            return 0, {
                "result": 0,
                "opt_id": opt_id,
                "item_list": self._bag_item_list(treasures, other_items),
                "update_list": update_list,
                "next_event": next_ev
            }

        # ----------------------------------------------------
        # 6. 安全屋阶段 2：整理装备 (500113 / 500133)
        #    效果：藏品重置点数+2，推进到 50012 离开描述
        # ----------------------------------------------------
        if opt_id in (500113, 500133):
            attr_dict[RogueTeamConst.ATTR_TREASURE_RESET_CNT] = attr_dict.get(RogueTeamConst.ATTR_TREASURE_RESET_CNT, 0) + 2
            next_ev = {"event_id": 50012, "opt_list": [], "trigger_type": 1}
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
                "other_info_json": json.dumps(next_ev, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))

            return 0, {
                "result": 0,
                "opt_id": opt_id,
                "attr_list": [{"attr_id": k, "value": v} for k, v in attr_dict.items()],
                "next_event": next_ev
            }

        # ----------------------------------------------------
        # 7. 通用事件解析与多分支推进引擎 (接入前置条件校验与历史记录)
        # ----------------------------------------------------
        hist_events, hist_options = self._get_session_history(s)
        cur_other = json.loads(s.get("other_info_json") or "{}")
        cur_event_id = cur_other.get("event_id", 0)

        # 防御机制：如果客户端发来了过期的旧选项（与当前事件不匹配且非0）
        if cur_event_id:
            cur_ev_cfg = self.config.get_event(cur_event_id)
            valid_opts = cur_ev_cfg.get("option_list", []) if cur_ev_cfg else []
            if valid_opts and opt_id not in valid_opts and opt_id != 0:
                # 重新推送当前真实事件给客户端
                unlocked_sub_opts = self.filter_unlocked_options(
                    uid, cur_event_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id)
                return 0, {
                    "result": 0,
                    "opt_id": opt_id,
                    "attr_list": [{"attr_id": k, "value": v} for k, v in attr_dict.items()],
                    "hero_list": heroes,
                    "next_event": {
                        "event_id": cur_event_id,
                        "opt_list": [self._build_option_entry(o) for o in unlocked_sub_opts],
                        "trigger_type": 1
                    }
                }

        if cur_event_id:
            hist_events.add(cur_event_id)
        hist_options.add(opt_id)

        opt_cfg = self.config.get_event_option(opt_id)
        next_ev = {"event_id": 0, "opt_list": [], "trigger_type": 0}
        new_treasures, new_other_items, update_list, eff_extra = [], [], [], {}
        if opt_cfg:
            # 校验前置条件是否满足，若不满足则拦截
            if not self.is_option_unlocked(uid, opt_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id):
                return 2, {"result": 2}

            new_treasures, new_other_items, update_list, eff_extra = self._execute_event_effects(
                uid, template_id, s, opt_cfg, attr_dict, treasures, other_items, heroes)
            jump_id = opt_cfg.get("jump_event_id", 0) or self.get_option_jump_event(opt_id)
            if jump_id:
                jump_ev_cfg = self.config.get_event(jump_id)
                if jump_ev_cfg:
                    hist_events.add(jump_id)
                    unlocked_sub_opts = self.filter_unlocked_options(
                        uid, jump_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id)
                    next_ev = {
                        "event_id": jump_id,
                        "opt_list": [self._build_option_entry(o) for o in unlocked_sub_opts],
                        "trigger_type": 1
                    }
            
            if not jump_id:
                self.advance_room_step(uid, s, RogueTeamConst.NODE_TYPE_EVENT, template_id)
        else:
            jump_id = self.get_option_jump_event(opt_id)
            if jump_id:
                jump_ev_cfg = self.config.get_event(jump_id)
                if jump_ev_cfg:
                    hist_events.add(jump_id)
                    unlocked_sub_opts = self.filter_unlocked_options(
                        uid, jump_id, s, attr_dict, treasures, other_items, heroes, hist_events, hist_options, template_id)
                    next_ev = {
                        "event_id": jump_id,
                        "opt_list": [self._build_option_entry(o) for o in unlocked_sub_opts],
                        "trigger_type": 1
                    }
            if not jump_id:
                self.advance_room_step(uid, s, RogueTeamConst.NODE_TYPE_EVENT, template_id)
                attr_dict[RogueTeamConst.ATTR_GOLD] = attr_dict.get(RogueTeamConst.ATTR_GOLD, 0) + 30
                reward_items = self.config.get_random_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=1)
                if reward_items:
                    r_val = self.config.get_item_default_rare(reward_items[0])
                    treasures.append({"id": reward_items[0], "rare": r_val})
                    new_treasures.append({"id": reward_items[0], "rare": r_val})
                    update_list.append({
                        "operate": 1, "id": int(reward_items[0]), "is_new": 1, "rare": r_val,
                        "source_rare": 0, "source_type": 2, "source_item_id": 0
                    })

        select_node_id = s.get("select_node_id", 0)
        is_clear = False
        if next_ev["trigger_type"] == 0:
            # 事件分支结束：当前节点置 OVER 并解锁下一列
            for n in map_info:
                if n.get("node_id") == select_node_id or (select_node_id == 0 and n.get("state") == RogueTeamConst.NODE_STATE_CLEAN):
                    n["state"] = RogueTeamConst.NODE_STATE_OVER
                    for next_id in n.get("next_id_list", []):
                        for nn in map_info:
                            if nn.get("node_id") == next_id and nn.get("state") == RogueTeamConst.NODE_STATE_LOCK:
                                nn["state"] = RogueTeamConst.NODE_STATE_UNCLEAN
                    break
            select_node_id = 0
            is_clear = self._is_floor_clear(map_info)

        self._save_session_history(next_ev, hist_events, hist_options)

        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "select_node_id": select_node_id,
            "map_info_json": json.dumps(map_info, ensure_ascii=False),
            "treasure_list_json": json.dumps(treasures, ensure_ascii=False),
            "other_item_list_json": json.dumps(other_items, ensure_ascii=False),
            "hero_list_json": json.dumps(heroes, ensure_ascii=False),
            "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
            "other_info_json": json.dumps(next_ev, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        item_delta = []
        if new_treasures or new_other_items:
            item_delta = [{
                "opt": 1,
                "treasure_list": new_treasures,
                "other_list": new_other_items
            }]

        ret_extra = {
            "result": 0,
            "opt_id": opt_id,
            "item_list": item_delta,
            "update_list": update_list,
            "attr_list": [{"attr_id": k, "value": v} for k, v in attr_dict.items()],
            "hero_list": heroes,
            "next_event": next_ev,
            "is_floor_clear": is_clear
        }
        if eff_extra:
            if eff_extra.get("pop_window"):
                ret_extra["pop_window"] = eff_extra["pop_window"]
            if eff_extra.get("world_line_id"):
                ret_extra["world_line_id"] = eff_extra["world_line_id"]
            if eff_extra.get("ending_id"):
                ret_extra["ending_id"] = eff_extra["ending_id"]

        if next_ev["trigger_type"] == 0:
            ret_extra["map_info"] = [self._norm_node(n) for n in map_info]

        return 0, ret_extra

    def generate_shop_data(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        """生成游商货架列表"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        attr_list = json.loads(s.get("attr_list_json") or "[]") if s else []
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}

        t_count = attr_dict.get(RogueTeamConst.ATTR_SHOP_SELL_TREASURE_MAX, 2)
        r_count = attr_dict.get(RogueTeamConst.ATTR_SHOP_SELL_RELIC_MAX, 2)

        treasures = self.config.get_random_shop_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=t_count)
        relics = self.config.get_random_shop_items(RogueTeamConst.ITEM_TYPE_RELIC, count=r_count)

        param_list = []
        idx = 1
        for tid in treasures:
            r_val = self.config.get_item_default_rare(tid)
            param_list.append({"index": idx, "param": int(tid), "buy_times": 1, "rare": r_val})
            idx += 1
        for rid in relics:
            param_list.append({"index": idx, "param": int(rid), "buy_times": 1, "rare": 2})
            idx += 1

        shop_info = {
            "event_type": RogueTeamConst.POP_EVENT_SHOP,
            "param_list": param_list
        }
        if s:
            db.upsert("rogueteam_session", uid, {
                "template_id": template_id,
                "shop_info_json": json.dumps(shop_info, ensure_ascii=False),
                "update_ts": int(time.time())
            }, keys=("uid", "template_id"))
        return shop_info

    def buy_shop_item(self, uid, index, template_id=RogueTeamConst.TEMPLATE_ID):
        """处理 88222 游商购买道具"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}

        shop_info = json.loads(s.get("shop_info_json") or "{}")
        param_list = shop_info.get("param_list", [])

        target_param = None
        for p in param_list:
            if p.get("index") == index:
                target_param = p
                break

        if not target_param or target_param.get("buy_times", 0) <= 0:
            return 2, {}  # 已售罄或无效

        cost = 60
        attr_list = json.loads(s.get("attr_list_json") or "[]")
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
        cur_gold = attr_dict.get(RogueTeamConst.ATTR_GOLD, 0)
        if cur_gold < cost:
            return 12, {}  # 金币不足

        attr_dict[RogueTeamConst.ATTR_GOLD] = cur_gold - cost
        target_param["buy_times"] = 0

        target_item_id = target_param["param"]
        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        item_cfg = self.config.get_item(target_item_id)
        itype = item_cfg.get("type") if item_cfg else None

        if itype == RogueTeamConst.ITEM_TYPE_TREASURE or str(target_item_id).startswith("14"):
            r_val = self.config.get_item_default_rare(target_item_id, item_cfg)
            treasures.append({"id": int(target_item_id), "rare": r_val})
            self.unlock_collection_item(uid, template_id, target_item_id, 1)
        else:
            other_items.append({"id": int(target_item_id), "value": 1})
            self.unlock_collection_item(uid, template_id, target_item_id, 3)

        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "treasure_list_json": json.dumps(treasures, ensure_ascii=False),
            "other_item_list_json": json.dumps(other_items, ensure_ascii=False),
            "shop_info_json": json.dumps(shop_info, ensure_ascii=False),
            "attr_list_json": json.dumps([{"attr_id": k, "value": v} for k, v in attr_dict.items()], ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        res_item_list = self._bag_item_list(treasures, other_items)
        return 0, {
            "result": 0,
            "item_list": res_item_list,
            "attr_list": [{"attr_id": k, "value": v} for k, v in attr_dict.items()],
            "shop_info": shop_info
        }

    def settle_run(self, uid, ending_id=1, is_win=True, template_id=RogueTeamConst.TEMPLATE_ID):
        """
        处理 88306 推演全量结算：
        - 胜利通关非失败结局解锁下一难度 (max_difficult 1->2->3->4)
        - 按 score_rate (最高250%) 计算最终推演总积分
        - 汇总勘察目标与全量图鉴列表
        """
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}

        cur_diff = s.get("difficult", 1)
        floor_num = s.get("floor_num", 1)
        score_rate = s.get("score_rate", 100)
        map_info = json.loads(s.get("map_info_json") or "[]")
        clean_nodes = [n for n in map_info if n.get("state") in (RogueTeamConst.NODE_STATE_CLEAN, RogueTeamConst.NODE_STATE_OVER)]

        normal_battles = sum(1 for n in clean_nodes if n.get("node_type") == RogueTeamConst.NODE_TYPE_BATTLE_NORMAL)
        elite_battles = sum(1 for n in clean_nodes if n.get("node_type") == RogueTeamConst.NODE_TYPE_BATTLE_ELITE)
        boss_battles = sum(1 for n in clean_nodes if n.get("node_type") in (RogueTeamConst.NODE_TYPE_BATTLE_BOSS, RogueTeamConst.NODE_TYPE_BATTLE_LAST_BOSS))

        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        mechanisms = [o for o in other_items if (isinstance(o, dict) and str(o.get("id", "")).startswith("12")) or (isinstance(o, int) and str(o).startswith("12"))]
        relics = [o for o in other_items if (isinstance(o, dict) and str(o.get("id", "")).startswith("13")) or (isinstance(o, int) and str(o).startswith("13"))]

        attr_list = json.loads(s.get("attr_list_json") or "[]")
        gold = next((a["value"] for a in attr_list if a["attr_id"] == RogueTeamConst.ATTR_GOLD), 0)

        # 积分公式：基础分 * score_rate%
        base_score = (floor_num * 1000) + (len(clean_nodes) * 100) + (len(treasures) * 100) + (len(relics) * 200) + (len(mechanisms) * 100) + (normal_battles * 100) + (elite_battles * 150) + (boss_battles * 1000)
        final_score = int(base_score * (score_rate / 100.0))

        # 难度解锁与历史记录
        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,))
        max_diff = user_row.get("max_difficult", 1) if user_row else 1

        # 判定是否为胜利结局 (结局 1~4)
        is_victory_ending = is_win and (1 <= ending_id <= 4)
        if is_victory_ending:
            if cur_diff == max_diff and max_diff < 4:
                max_diff += 1
            self.unlock_collection_item(uid, template_id, ending_id, 5)

        # 更新历史通关统计
        his_row = db.get("rogueteam_history", uid, "AND template_id=?", (template_id,))
        his_diff_list = json.loads(his_row.get("diff_clear_json") or "[]") if his_row else []
        his_avg_list = json.loads(his_row.get("ending_pass_json") or "[]") if his_row else []

        if is_victory_ending:
            # 更新难度通过次数
            found_diff = False
            for d_item in his_diff_list:
                if d_item.get("key") == cur_diff:
                    d_item["value"] = d_item.get("value", 0) + 1
                    found_diff = True
                    break
            if not found_diff:
                his_diff_list.append({"key": cur_diff, "value": 1})

            # 更新结局通过次数
            found_end = False
            for e_item in his_avg_list:
                if e_item.get("key") == ending_id:
                    e_item["value"] = e_item.get("value", 0) + 1
                    found_end = True
                    break
            if not found_end:
                his_avg_list.append({"key": ending_id, "value": 1})

        db.upsert("rogueteam_history", uid, {
            "template_id": template_id,
            "diff_clear_json": json.dumps(his_diff_list, ensure_ascii=False),
            "ending_pass_json": json.dumps(his_avg_list, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        # 更新用户表
        db.upsert("rogueteam_user", uid, {
            "template_id": template_id,
            "max_difficult": max_diff,
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        # 结束局内 Session，全量清理残留事件、节点与商店
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "in_game": 0,
            "select_node_id": 0,
            "floor_state": RogueTeamConst.FLOOR_STATE_OVER if is_win else RogueTeamConst.FLOOR_STATE_FAIL,
            "other_info_json": "{}",
            "shop_info_json": "{}",
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        # 动态同步勘察目标任务进度
        self.update_survey_tasks(uid, template_id, add_gold=gold)

        # 查询全量图鉴
        colls = db.query("SELECT item_id, item_type FROM rogueteam_illustrated WHERE uid=? AND template_id=?", (uid, template_id))
        collection_by_type = {}
        unlock_collection = []
        valid_item_ids = self._valid_item_ids()
        for c in colls:
            itype = c["item_type"]
            iid = c["item_id"]
            collection_by_type.setdefault(itype, []).append(iid)
            if iid in valid_item_ids:
                # 结局 ID 等非道具条目只走 collection_list，进 unlock_collection 会让客户端崩
                unlock_collection.append(iid)
        collection_list = [{"type": t, "item_list": items} for t, items in collection_by_type.items()]

        end_info_list = [
            {"key": 1, "value": floor_num if is_win else max(0, floor_num - 1)},
            {"key": 2, "value": len(clean_nodes)},
            {"key": 3, "value": len(treasures)},
            {"key": 4, "value": len(relics)},
            {"key": 5, "value": len(mechanisms)},
            {"key": 6, "value": normal_battles},
            {"key": 7, "value": elite_battles},
            {"key": 8, "value": boss_battles},
            {"key": 100, "value": base_score},
            {"key": 101, "value": final_score}
        ]

        return 0, {
            "result": 0,
            "ending_id": ending_id,
            "end_info_list": end_info_list,
            "collection_list": collection_list,
            "unlock_collection": unlock_collection,
            "total_time": 180
        }

    def unlock_collection_item(self, uid, template_id, item_id, item_type=0):
        """记录图鉴解锁入库（标准化为 1:藏品, 2:奇遇, 3:圣物, 4:外接程序, 5:结局）"""
        if not item_id:
            return
        c_type = item_type
        s_id = str(item_id)
        if not c_type:
            if s_id.startswith("14"):
                c_type = 1
            elif s_id.startswith("13"):
                c_type = 3
            elif s_id.startswith("12"):
                c_type = 4
            elif 1 <= item_id <= 10:
                c_type = 5
            else:
                c_type = 2
        else:
            if c_type == 4 or s_id.startswith("14"):
                c_type = 1
            elif c_type == 2 and (10000 <= item_id < 99999 or s_id.startswith("50")):
                c_type = 2
            elif c_type == 3 or s_id.startswith("13"):
                c_type = 3
            elif c_type == 2 or s_id.startswith("12"):
                c_type = 4
            elif c_type == 5 or 1 <= item_id <= 10:
                c_type = 5

        db = self._db()
        existing = db.get("rogueteam_illustrated", uid, "AND template_id=? AND item_id=?", (template_id, int(item_id)))
        if not existing:
            db.upsert("rogueteam_illustrated", uid, {
                "template_id": template_id,
                "item_id": int(item_id),
                "item_type": c_type,
                "is_viewed": 0,
                "unlock_ts": int(time.time())
            }, keys=("uid", "template_id", "item_id"))

    # ==================================================================
    # 兼容/补全层（2026-08-31 修复）
    # 19:55 那次重写把 operations.py / generator.py 依赖的 10 个方法名一起丢了：
    #   get_outside_data / unlock_tech_node / view_collection / record_score_id /
    #   hero_recruit / commit_event_selection / discard_items / play_ending_plot /
    #   shop_buy / shop_refresh
    # 缺失后果：sc_88305 整帧发不出去（图鉴 unlock_collection + 天赋 tree_list 全丢）
    #   -> 圣物图鉴 RogueTeamConditionCfg[0] 空指针崩溃 + 天赋不生效；
    #   游商购买/刷新、掉落三选一提交、丢弃、招募、结局剧情全部 AttributeError。
    # ==================================================================

    EVENT_PARAM_KEYS = ("index", "param", "buy_times", "rare", "discount", "is_new")

    @classmethod
    def _norm_event(cls, ev):
        """ROUGE_EVENT{event_type, param_list[EVENT_PARAM], drop_type, shop_refresh_times}
        只保留协议真实字段，内部标记（_extra_draw 等）不能带进 codec。"""
        if not isinstance(ev, dict) or not ev.get("event_type"):
            return {}
        params = []
        for p in ev.get("param_list") or []:
            if not isinstance(p, dict):
                continue
            item = {"index": int(p.get("index", 0)), "param": int(p.get("param", 0)), "buy_times": int(p.get("buy_times", 0))}
            for k in ("rare", "discount", "is_new"):
                if p.get(k) is not None:
                    item[k] = int(p.get(k))
            params.append(item)
        out = {"event_type": int(ev.get("event_type", 0)), "param_list": params, "drop_type": int(ev.get("drop_type", 0) or 0)}
        if ev.get("shop_refresh_times") is not None:
            out["shop_refresh_times"] = int(ev.get("shop_refresh_times"))
        return out

    def _roll_pop_items(self, uid, pop_event, template_id=RogueTeamConst.TEMPLATE_ID, count=3):
        """按弹窗类型抽 count 个候选道具，返回 EVENT_PARAM 列表"""
        type_map = {
            RogueTeamConst.POP_EVENT_RELIC: RogueTeamConst.ITEM_TYPE_RELIC,
            RogueTeamConst.POP_EVENT_MECHANISM: RogueTeamConst.ITEM_TYPE_MECHANISM,
            RogueTeamConst.POP_EVENT_TREASURE: RogueTeamConst.ITEM_TYPE_TREASURE,
        }
        item_type = type_map.get(pop_event, RogueTeamConst.ITEM_TYPE_TREASURE)
        items = self.config.get_random_items(item_type, count=count) or []
        param_list = []
        for i, iid in enumerate(items):
            cfg = self.config.get_item(iid) or {}
            param_list.append({
                "index": i + 1,
                "param": int(iid),
                "buy_times": 0,
                "rare": int(self.config.get_item_default_rare(iid, cfg)),
                "is_new": 1
            })
        return param_list

    # ---------- 外围数据（sc_88305 / sc_88309 / sc_88315） ----------

    def get_outside_data(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        out = self.get_user_outside_info(uid, template_id)
        db = self._db()
        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,)) or {}
        try:
            reward_list = json.loads(user_row.get("rewarded_list_json") or "[]")
        except Exception:
            reward_list = []
        # 局内进行中才回填 difficult，否则入口显示"开始推演"
        session = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        in_game = bool(session and session.get("in_game", 0))
        out["difficult"] = session.get("difficult", 0) if in_game else 0
        out.update({
            "point": int(user_row.get("point", 0) or 0),
            "reward_list": [int(r) for r in reward_list if str(r).isdigit() or isinstance(r, int)],
            "activity_id": RogueTeamConst.FETTERS_ACTIVITY_ID,
            "fetters_id": 1,
            "next_timestamps": int(time.time()) + 86400 * 7
        })
        return out

    # ---------- 天赋科技树（cs_88300） ----------

    def unlock_tech_node(self, uid, template_id, tree_id):
        node = self.config.get_skill_node(tree_id)
        if not node:
            return 3  # 缺少配置
        db = self._db()
        if db.query("SELECT 1 FROM rogueteam_tree WHERE uid=? AND template_id=? AND node_id=?", (uid, template_id, tree_id)):
            return 0  # 已解锁，幂等返回成功
        for p in node.get("pre_nodes", []) or []:
            if not db.query("SELECT 1 FROM rogueteam_tree WHERE uid=? AND template_id=? AND node_id=?", (uid, template_id, p)):
                return 2  # 前置未解锁
        cost = int(node.get("cost", 0) or 0)
        if cost > 0:
            cur = db.get_item_num(uid, RogueTeamConst.TECH_ITEM_ID)
            if cur < cost:
                return 12  # 材料不足
            db.set_material(uid, RogueTeamConst.TECH_ITEM_ID, cur - cost)
        db.upsert("rogueteam_tree", uid, {
            "template_id": template_id,
            "node_id": tree_id,
            "unlock_ts": int(time.time())
        }, keys=("uid", "template_id", "node_id"))
        return 0

    # ---------- 图鉴已读（cs_88312）与积分目标（cs_88316） ----------

    def view_collection(self, uid, template_id, collection_id, coll_type):
        db = self._db()
        id_list = collection_id if isinstance(collection_id, (list, tuple)) else [collection_id]
        for cid in id_list:
            if not cid:
                continue
            cid = int(cid)
            if not db.get("rogueteam_illustrated", uid, "AND template_id=? AND item_id=?", (template_id, cid)):
                self.unlock_collection_item(uid, template_id, cid, coll_type)
            db.execute("UPDATE rogueteam_illustrated SET is_viewed=1 WHERE uid=? AND template_id=? AND item_id=?", (uid, template_id, cid))
        return 0

    def record_score_id(self, uid, main_id, target_id):
        return self.set_last_score_id(uid, main_id, target_id)

    # ---------- 局内招募（cs_88102） ----------

    def hero_recruit(self, uid, new_heroes, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, [], None
        heroes = json.loads(s.get("hero_list_json") or "[]")
        existing = {h.get("hero_id") for h in heroes}
        for h in new_heroes or []:
            hid = h.get("hero_id") or h.get("id")
            if hid and hid not in existing:
                heroes.append({"hero_id": int(hid), "temp_id": int(h.get("temp_id", 0) or 0), "hp_ratio": int(h.get("hp_ratio", 10000) or 10000)})
                existing.add(hid)

        # 检查是否处于安全屋阶段 1 (500101)，若是则自动推入安全屋阶段 2 设施整备（Event 50011）
        select_node_id = s.get("select_node_id", 0)
        map_info = json.loads(s.get("map_info_json") or "[]")
        cur_node = next((n for n in map_info if n.get("node_id") == select_node_id), {})
        is_safehouse = cur_node.get("node_type") == RogueTeamConst.NODE_TYPE_REST

        next_ev = None
        if is_safehouse:
            next_ev = {
                "event_id": 50011,
                "opt_list": [{"opt_id": 500111}, {"opt_id": 500112}, {"opt_id": 500113}],
                "trigger_type": 1
            }

        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "hero_list_json": json.dumps(heroes, ensure_ascii=False),
            "other_info_json": json.dumps(next_ev or {}, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0, heroes, next_ev

    # ---------- 掉落三选一提交（cs_88200） ----------

    def commit_event_selection(self, uid, event_id, param_arg, template_id=RogueTeamConst.TEMPLATE_ID):
        """提交战后/事件三选一。支持天赋 10399「战功延展」的第二次选择：
        本次弹窗若带内部标记 _extra_draw>0，选完后再推一轮新弹窗（drop_type 递增）。"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}

        other_info = json.loads(s.get("other_info_json") or "{}")
        param_list = other_info.get("param_list") or []
        arg_val = int(param_arg or 0)

        target_item_id = 0
        target_rare = 1
        for p in param_list:
            if p.get("index") == arg_val or p.get("param") == arg_val:
                target_item_id = int(p.get("param", 0))
                target_rare = int(p.get("rare", 1) or 1)
                break
        if not target_item_id and 1 <= arg_val <= len(param_list):
            target_item_id = int(param_list[arg_val - 1].get("param", 0))
            target_rare = int(param_list[arg_val - 1].get("rare", 1) or 1)
        if not target_item_id:
            target_item_id = arg_val

        if target_item_id == 0:
            db.upsert("rogueteam_session", uid, {"template_id": template_id, "other_info_json": "{}", "update_ts": int(time.time())}, keys=("uid", "template_id"))
            return 0, {}

        item = self.config.get_item(target_item_id) or {}
        itype = item.get("type")
        if not target_rare or target_rare == 1:
            target_rare = int(self.config.get_item_default_rare(target_item_id, item))

        is_treasure = (itype == RogueTeamConst.ITEM_TYPE_TREASURE or itype == 4 or
                       (itype is None and (str(target_item_id).startswith("14") or 1400000 <= target_item_id < 1500000)))

        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        attr_list = json.loads(s.get("attr_list_json") or "[]")

        if is_treasure:
            treasures.append({"id": int(target_item_id), "rare": target_rare})
            item_list = [{"opt": 1, "treasure_list": [{"id": int(target_item_id), "rare": target_rare}], "other_list": []}]
            self.unlock_collection_item(uid, template_id, target_item_id, 1)
        else:
            other_items.append({"id": int(target_item_id), "value": 1})
            item_list = [{"opt": 1, "treasure_list": [], "other_list": [int(target_item_id)]}]
            # 第 4 参是 ITEM_TYPE（内部转 COLLECTION_TYPE），传 COLLECTION_TYPE 会归错类
            self.unlock_collection_item(uid, template_id, target_item_id, itype or RogueTeamConst.ITEM_TYPE_RELIC)

        # 初始物资特效
        for a in attr_list:
            if target_item_id == 1100001 and a.get("attr_id") == RogueTeamConst.ATTR_GOLD:
                a["value"] = a.get("value", 0) + 150
            elif target_item_id == 1100004 and a.get("attr_id") == RogueTeamConst.ATTR_MECHANISM_VALUE:
                a["value"] = a.get("value", 0) + 40

        update_list = [{
            "operate": 1, "id": int(target_item_id), "is_new": 1, "rare": target_rare,
            "source_rare": 0, "source_type": 1, "source_item_id": 0
        }]

        # ---- 天赋 10399：额外一次选择 ----
        next_pop = {}
        extra_draw = int(other_info.get("_extra_draw", 0) or 0)
        if extra_draw > 0:
            pop_event = int(other_info.get("event_type", RogueTeamConst.POP_EVENT_TREASURE))
            new_params = self._roll_pop_items(uid, pop_event, template_id)
            if new_params:
                next_pop = {
                    "event_type": pop_event,
                    "param_list": new_params,
                    "drop_type": min(2, int(other_info.get("drop_type", 1) or 1) + 1),
                    "_extra_draw": extra_draw - 1
                }

        # 节点收尾：本节点置 OVER、解锁下一列、清空 select_node_id
        select_node_id = s.get("select_node_id", 0)
        map_info = json.loads(s.get("map_info_json") or "[]")
        if not next_pop:
            for n in map_info:
                if n.get("node_id") == select_node_id or (select_node_id == 0 and n.get("state") == RogueTeamConst.NODE_STATE_CLEAN):
                    n["state"] = RogueTeamConst.NODE_STATE_OVER
                    for next_id in n.get("next_id_list", []):
                        for nn in map_info:
                            if nn.get("node_id") == next_id and nn.get("state") == RogueTeamConst.NODE_STATE_LOCK:
                                nn["state"] = RogueTeamConst.NODE_STATE_UNCLEAN
                    break

        is_clear = self._is_floor_clear(map_info)
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "select_node_id": select_node_id if next_pop else 0,
            "map_info_json": json.dumps(map_info, ensure_ascii=False),
            "treasure_list_json": json.dumps(treasures, ensure_ascii=False),
            "other_item_list_json": json.dumps(other_items, ensure_ascii=False),
            "attr_list_json": json.dumps(attr_list, ensure_ascii=False),
            "other_info_json": json.dumps(next_pop, ensure_ascii=False) if next_pop else "{}",
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))

        return 0, {
            "item_list": item_list,
            "update_list": update_list,
            "attr_list": attr_list,
            "map_info": [self._norm_node(n) for n in map_info],
            "next_pop": self._norm_event(next_pop),
            "is_floor_clear": is_clear
        }

    # ---------- 丢弃道具（cs_88212） ----------

    def discard_items(self, uid, item_id_list, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}
        treasures = json.loads(s.get("treasure_list_json") or "[]")
        other_items = json.loads(s.get("other_item_list_json") or "[]")
        del_ids = {int(x) for x in (item_id_list or []) if x}

        del_treasures, new_treasures = [], []
        for t in treasures:
            tid = t.get("id") if isinstance(t, dict) else int(t)
            (del_treasures if tid in del_ids else new_treasures).append(t)
        del_others, new_others = [], []
        for o in other_items:
            oid = o.get("id") if isinstance(o, dict) else int(o)
            (del_others if oid in del_ids else new_others).append(o)

        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "treasure_list_json": json.dumps(new_treasures, ensure_ascii=False),
            "other_item_list_json": json.dumps(new_others, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0, {"item_list": self._bag_item_list(del_treasures, del_others, opt=2)}

    def play_ending_plot(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        return 0

    # ---------- 三选一「刷新」按钮（cs_88202） ----------

    def reset_operate_data(self, uid, reset_type=1, template_id=RogueTeamConst.TEMPLATE_ID):
        """客户端 ChallengeRogueTeamTreasureSelectView 的 resetBtn_ ->
        ChallengeRogueTeamAction.ResetOperateData(RESET_OPERATE_TYPE.TREASURE=1) -> cs_88202。
        按钮可点条件是 ATTRIBUTE_ENUM.TREASURE_RESET_CNT(7) > 0，但候选列表由服务端持有，
        服务端只回 sc_88203 而不重掷 + 补推 sc_88015 的话，界面不会有任何变化 = "刷新按钮失效"。"""
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}
        other_info = json.loads(s.get("other_info_json") or "{}")
        if not other_info.get("event_type"):
            return 2, {}  # 当前没有待选窗口

        attr_list = json.loads(s.get("attr_list_json") or "[]")
        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
        left = attr_dict.get(RogueTeamConst.ATTR_TREASURE_RESET_CNT, 0)
        if left <= 0:
            return 12, {}  # 重置次数不足

        new_params = self._roll_pop_items(uid, int(other_info.get("event_type")), template_id)
        if not new_params:
            return 3, {}  # 缺少配置

        attr_dict[RogueTeamConst.ATTR_TREASURE_RESET_CNT] = left - 1
        other_info["param_list"] = new_params
        new_attr = [{"attr_id": k, "value": v} for k, v in attr_dict.items()]
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "other_info_json": json.dumps(other_info, ensure_ascii=False),
            "attr_list_json": json.dumps(new_attr, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0, {"attr_list": new_attr, "other_info": self._norm_event(other_info), "reset_left": left - 1}

    # ---------- 游商（cs_88222 购买/离开、cs_88224 刷新） ----------

    def shop_buy(self, uid, index, template_id=RogueTeamConst.TEMPLATE_ID):
        """index == 0 是客户端的「离开」按钮（ChallengeRogueTeamAction.ShopBuyItem:
        if index == 0 then ClearShopData() end），必须在这里收尾商店节点并把
        指针推进到下一列，否则玩家买完离开后地图不动。"""
        if int(index or 0) == 0:
            return self.leave_shop(uid, template_id)
        return self.buy_shop_item(uid, index, template_id)

    def leave_shop(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}
        map_info = json.loads(s.get("map_info_json") or "[]")
        select_node_id = s.get("select_node_id", 0)
        for n in map_info:
            if n.get("node_id") == select_node_id:
                n["state"] = RogueTeamConst.NODE_STATE_OVER
                for next_id in n.get("next_id_list", []):
                    for nn in map_info:
                        if nn.get("node_id") == next_id and nn.get("state") == RogueTeamConst.NODE_STATE_LOCK:
                            nn["state"] = RogueTeamConst.NODE_STATE_UNCLEAN
                break
        is_clear = self._is_floor_clear(map_info)
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "select_node_id": 0,
            "map_info_json": json.dumps(map_info, ensure_ascii=False),
            "shop_info_json": "{}",
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0, {
            "map_info": [self._norm_node(n) for n in map_info],
            "shop_info": {},
            "session": self.get_session_data(uid, template_id),
            "left_shop": True,
            "is_floor_clear": is_clear
        }

    def shop_refresh(self, uid, template_id=RogueTeamConst.TEMPLATE_ID):
        db = self._db()
        s = db.get("rogueteam_session", uid, "AND template_id=?", (template_id,))
        if not s:
            return 1, {}
        attr_list = json.loads(s.get("attr_list_json") or "[]")
        gold_attr = None
        for a in attr_list:
            if a.get("attr_id") == RogueTeamConst.ATTR_GOLD:
                gold_attr = a
                break
        shop_info = json.loads(s.get("shop_info_json") or "{}")
        times = int(shop_info.get("shop_refresh_times", 0) or 0)
        cost = 50 * (2 ** times)
        cur_gold = gold_attr.get("value", 0) if gold_attr else 0
        if cur_gold < cost:
            return 12, {}  # 微光晶砾不足
        if gold_attr:
            gold_attr["value"] = cur_gold - cost

        attr_dict = {a["attr_id"]: a["value"] for a in attr_list}
        t_count = attr_dict.get(RogueTeamConst.ATTR_SHOP_SELL_TREASURE_MAX, 2)
        r_count = attr_dict.get(RogueTeamConst.ATTR_SHOP_SELL_RELIC_MAX, 2)
        param_list = []
        idx = 1
        for tid in self.config.get_random_shop_items(RogueTeamConst.ITEM_TYPE_TREASURE, count=t_count):
            param_list.append({"index": idx, "param": int(tid), "buy_times": 1,
                               "rare": int(self.config.get_item_default_rare(tid)), "discount": 100, "is_new": 1})
            idx += 1
        for rid in self.config.get_random_shop_items(RogueTeamConst.ITEM_TYPE_RELIC, count=r_count):
            param_list.append({"index": idx, "param": int(rid), "buy_times": 1,
                               "rare": int(self.config.get_item_default_rare(rid)), "discount": 100, "is_new": 1})
            idx += 1

        new_shop = {
            "event_type": RogueTeamConst.POP_EVENT_SHOP,
            "param_list": param_list,
            "drop_type": 0,
            "shop_refresh_times": times + 1
        }
        db.upsert("rogueteam_session", uid, {
            "template_id": template_id,
            "shop_info_json": json.dumps(new_shop, ensure_ascii=False),
            "attr_list_json": json.dumps(attr_list, ensure_ascii=False),
            "update_ts": int(time.time())
        }, keys=("uid", "template_id"))
        return 0, {"attr_list": attr_list, "shop_info": new_shop}

    def update_survey_tasks(self, uid, template_id=100001, add_gold=0, add_mechanisms=0, add_events=0, add_treasures=0):
        """动态计算并更新肉鸽阶段任务「勘察目标」（30602001~30602020）"""
        db = self._db()
        colls = db.query("SELECT item_id, item_type FROM rogueteam_illustrated WHERE uid=? AND template_id=?", (uid, template_id))
        treasures_cnt = max(sum(1 for c in colls if c["item_type"] == 1), (db.get("task", uid, "AND task_id=30602010") or {}).get("progress", 0) + add_treasures)
        events_cnt = max(sum(1 for c in colls if c["item_type"] == 2), (db.get("task", uid, "AND task_id=30602006") or {}).get("progress", 0) + add_events)
        mechanisms_cnt = max(sum(1 for c in colls if c["item_type"] == 4), (db.get("task", uid, "AND task_id=30602004") or {}).get("progress", 0) + add_mechanisms)
        blade_mechanisms = (db.get("task", uid, "AND task_id=30602003") or {}).get("progress", 0)

        user_row = db.get("rogueteam_user", uid, "AND template_id=?", (template_id,))
        max_diff = user_row.get("max_difficult", 1) if user_row else 1
        his_row = db.get("rogueteam_history", uid, "AND template_id=?", (template_id,))
        ending_list = json.loads(his_row.get("ending_pass_json") or "[]") if his_row else []
        has_ending_1 = any(e.get("key") == 1 and e.get("value", 0) > 0 for e in ending_list)

        t_gold = db.get("task", uid, "AND task_id=30602002")
        current_gold = (t_gold.get("progress", 0) if t_gold else 0) + add_gold

        task_updates = {
            30602002: (current_gold, 300),
            30602003: (blade_mechanisms, 5),
            30602004: (mechanisms_cnt, 10),
            30602006: (events_cnt, 10),
            30602007: (current_gold, 800),
            30602008: (1 if has_ending_1 else 0, 1),
            30602010: (treasures_cnt, 12),
            30602011: (current_gold, 2000),
            30602012: (mechanisms_cnt, 30),
            30602014: (current_gold, 3000),
            30602015: (min(4, mechanisms_cnt // 2), 4),
            30602016: (1 if max_diff >= 2 else 0, 1),
            30602018: (current_gold, 4000),
            30602019: (min(6, mechanisms_cnt // 2), 6),
            30602020: (1 if max_diff >= 3 else 0, 1)
        }

        phase_map = {
            30602001: [30602002, 30602003, 30602004],
            30602005: [30602006, 30602007, 30602008],
            30602009: [30602010, 30602011, 30602012],
            30602013: [30602014, 30602015, 30602016],
            30602017: [30602018, 30602019, 30602020]
        }

        completed_ids = set()
        for tid, (prog, need) in task_updates.items():
            t = db.get("task", uid, "AND task_id=?", (tid,))
            claimed = t.get("claimed_ts", 0) if t else 0
            is_comp = 1 if (prog >= need or claimed > 0) else 0
            if is_comp:
                completed_ids.add(tid)
            db.upsert("task", uid, {
                "task_id": tid,
                "name": t.get("name", "") if t else "",
                "task_type": 1006,
                "progress": prog,
                "complete_flag": is_comp,
                "claimed_ts": claimed
            }, keys=("uid", "task_id"))

        for ptid, sub_tids in phase_map.items():
            done_cnt = sum(1 for s in sub_tids if s in completed_ids)
            t = db.get("task", uid, "AND task_id=?", (ptid,))
            claimed = t.get("claimed_ts", 0) if t else 0
            is_comp = 1 if (done_cnt >= 3 or claimed > 0) else 0
            db.upsert("task", uid, {
                "task_id": ptid,
                "name": t.get("name", "") if t else "",
                "task_type": 1006,
                "progress": min(3, done_cnt),
                "complete_flag": is_comp,
                "claimed_ts": claimed
            }, keys=("uid", "task_id"))
