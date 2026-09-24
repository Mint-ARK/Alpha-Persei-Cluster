# -*- coding: utf-8 -*-
"""
ai_translator.py — AI 转译器与多线程节日/生日问候调度系统

核心职责：
1. 本地日历驱动：对接 ai_calendar，获取今日公历节日、农历大节、二十四节气、修正者生日、玩家生日；
2. 严格 3 线程并发工作池 (Worker Pool)：平滑限制 QPS，绝不打爆第三方 API Rate Limit (429)；
3. 一次性临时会话窗口：构建独立 Prompt 上下文，绝对不读取也不追加写入角色日常 ai_chat_memory 历史；
4. 玩家选择权与双轨分流调度机制：
   - 轨一：hero_birthday（角色生日·寿星邀约专属轨）：发件人有且仅有寿星本人（1 人），不走面板配置，不走看板娘，绝不追加总览信；
   - 轨二：user_birthday / holiday / solar_term（玩家生日与节气时令轨）：
     * 面板优先：玩家在面板显式配置的角色全部选入（如配置 6 人则 6 人各发一封）；
     * 看板娘智能兜底：玩家未配置时，自动提取看板娘（<=3 全选，>3 选前 3 位），全空退化至 1084 薇儿丹蒂；
5. 特殊路由与深空之眼总览邮件（第七封公函）：
   - 联动角色 1045（赤音·亚莉莎）、1046（血弹·雪儿）自动路由至 1084 薇儿丹蒂；
   - 在玩家生日（user_birthday）或重大节日（holiday_major）时，在专属信件派发后追加一封发件人统一为“深空之眼”的统筹贺函/慰问通告；
6. 离线模板保底与附件安全：
   - 模型未配置、禁用或调用超时时，平滑降级至高质量官方风静态信件模板；
   - 附件严格遵循 ai_gift_whitelist 白名单，邮件全量走 MailService.send_mail 入库，杜绝直写背包。
7. 玩家生日年度防重锁：打通 game_user 表的 birth_month/birth_day，配合年度防重锁自动触发全员祝福。
"""

import os
import json
import time
import random
import logging
import datetime
import concurrent.futures
import threading
from account_db import get_db, ARCHIVE_MAP, HERO_TO_ARCHIVE
from mail_service import MailService
from ai_calendar import get_calendar_events
import ai_bot_config
import ai_bot_service

logger = logging.getLogger("ai_translator")
_login_greeting_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="AIGreetingLogin",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STICKER_MAP_PATH = os.path.join(BASE_DIR, "data", "ai_sticker_emotion_map.json")
GIFT_WHITELIST_PATH = os.path.join(BASE_DIR, "data", "ai_gift_whitelist.json")
HOLIDAY_TEMPLATES_PATH = os.path.join(BASE_DIR, "data", "ai_holiday_templates.json")
HERO_BIRTHDAYS_PATH = os.path.join(BASE_DIR, "data", "ai_hero_birthdays.json")
SKIN_DATA_PATH = os.path.join(os.path.dirname(BASE_DIR), "all_skins_data.json")


def _load_user_extra(db, uid):
    try:
        rows = db.query("SELECT extra FROM users WHERE uid=?", (uid,))
        if rows and rows[0].get("extra"):
            raw = rows[0]["extra"]
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.warning(f"读取用户 uid={uid} extra 异常: {e}")
    return {}


def _save_user_extra(db, uid, extra):
    try:
        raw = json.dumps(extra, ensure_ascii=False)
        cur = db.execute("UPDATE users SET extra=? WHERE uid=?", (raw, uid))
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"保存用户 uid={uid} extra 异常: {e}")
        return False


class AITranslator:
    """AI 转译中枢与问候调度服务"""

    _instance = None

    def __init__(self, db=None):
        self.db = db
        self._stickers = None
        self._gifts = None
        self._templates = None
        self._hero_birthdays = None
        self._skin_to_hero = None
        self._dispatch_lock = threading.Lock()
        self._ensure_loaded()

    @classmethod
    def get_instance(cls, db=None):
        if cls._instance is None:
            cls._instance = cls(db=db)
        elif db is not None:
            cls._instance.db = db
        return cls._instance

    def _get_db(self, db=None):
        return db or self.db or get_db()

    def _ensure_loaded(self):
        """加载表情包字典、礼物白名单、静态模板、生日名册与换装机体映射表"""
        if self._stickers is None and os.path.exists(STICKER_MAP_PATH):
            try:
                with open(STICKER_MAP_PATH, "r", encoding="utf-8") as f:
                    self._stickers = json.load(f)
            except Exception as e:
                logger.error(f"加载 ai_sticker_emotion_map.json 失败: {e}")
        self._stickers = self._stickers or {}

        if self._gifts is None and os.path.exists(GIFT_WHITELIST_PATH):
            try:
                with open(GIFT_WHITELIST_PATH, "r", encoding="utf-8") as f:
                    self._gifts = json.load(f)
            except Exception as e:
                logger.error(f"加载 ai_gift_whitelist.json 失败: {e}")
        self._gifts = self._gifts or {}

        if self._templates is None and os.path.exists(HOLIDAY_TEMPLATES_PATH):
            try:
                with open(HOLIDAY_TEMPLATES_PATH, "r", encoding="utf-8") as f:
                    self._templates = json.load(f)
            except Exception as e:
                logger.error(f"加载 ai_holiday_templates.json 失败: {e}")
        self._templates = self._templates or {}

        if self._hero_birthdays is None and os.path.exists(HERO_BIRTHDAYS_PATH):
            try:
                with open(HERO_BIRTHDAYS_PATH, "r", encoding="utf-8") as f:
                    self._hero_birthdays = json.load(f)
            except Exception as e:
                logger.error(f"加载 ai_hero_birthdays.json 失败: {e}")
        self._hero_birthdays = self._hero_birthdays or {}

        if self._skin_to_hero is None:
            self._skin_to_hero = {}
            if os.path.exists(SKIN_DATA_PATH):
                try:
                    with open(SKIN_DATA_PATH, "r", encoding="utf-8") as f:
                        skins = json.load(f)
                        if isinstance(skins, list):
                            self._skin_to_hero = {
                                int(s["sid"]): int(s["hero_id"])
                                for s in skins if "sid" in s and "hero_id" in s
                            }
                except Exception as e:
                    logger.warning(f"加载 all_skins_data.json 换装映射失败: {e}")

    def normalize_board_item(self, item_id):
        """
        将看板娘 ID（自机机体卡号、换装 ID 等）归一化为代表该修正者的唯一身份 ID (档案 ID)：
        1. 换装 ID 处理：
           - 游戏换装为 6 位数字（如 109404, 108501, 109503, 119401）；
           - 角色可以穿戴某换装成为看板娘而不携带本体，换装即隶属于该角色本身；
           - 优先从 skin_map 查找其所属 hero_id；若未命中，按规范截取前 4 位 int(str(item_id)[:4])；
        2. 机体卡号到角色档案归一化：
           - 修正者不同机体（如海拉 1194/1094，薇儿丹蒂 1084/1184/1284，大国主 1066/1166）均归属于同一角色；
           - 通过 HERO_TO_ARCHIVE 映射为角色基础档案 ID（如 1194 -> 1094, 1094 -> 1094）；
           - 若在 HERO_TO_ARCHIVE 中未收录（如 10066 宁希达 / 1029 奥丁），尝试从 ARCHIVE_MAP 映射，兜底为其自身 ID。
        """
        try:
            iid = int(item_id)
        except (ValueError, TypeError):
            return 0
        if iid <= 0:
            return 0

        # 1. 换装 ID 归一化为机体卡号
        hero_id = iid
        if iid >= 100000:
            if self._skin_to_hero and iid in self._skin_to_hero:
                hero_id = self._skin_to_hero[iid]
            else:
                try:
                    hero_id = int(str(iid)[:4])
                except Exception:
                    hero_id = iid

        # 2. 机体卡号归一化为角色唯一档案 ID
        archive_id = HERO_TO_ARCHIVE.get(hero_id)
        if not archive_id:
            archive_id = ARCHIVE_MAP.get(hero_id, hero_id)
        return archive_id

    # =========================================================================
    #  一、表情语义转译与标签处理
    # =========================================================================

    def translate_stickers(self, text, hero_name=""):
        """解析正文中的表情标签，如 [sticker:138] 或自然情绪描述，转换为标准表现文本"""
        if not text:
            return ""
        clean_text = text.strip()
        return clean_text

    def get_hero_representative_sticker(self, hero_name, emotion_tag="开心"):
        """获取指定角色匹配指定情绪标签的推荐表情"""
        if not self._stickers:
            return None
        candidates = []
        for sid, info in self._stickers.items():
            if info.get("hero_name") == hero_name and info.get("emotion_tag") == emotion_tag:
                candidates.append(info)
        if candidates:
            return random.choice(candidates)
        fallback = [info for info in self._stickers.values() if info.get("hero_name") == hero_name]
        return random.choice(fallback) if fallback else None

    # =========================================================================
    #  二、候选角色解析与特殊路由（双轨分流架构）
    # =========================================================================

    def resolve_candidates(self, uid, db, event_info):
        """
        解析有资格参与发送本次事件祝福的角色列表（双轨分流架构）：
        1. 轨一：hero_birthday（角色生日·寿星邀约专属轨）：
           - 有且仅有寿星本人（1 人），不看面板配置，不走看板娘，绝不追加总览信；
        2. 轨二：user_birthday / player_birthday / holiday / solar_term（庆生与节日轨）：
           - 面板配置优先：若玩家在 users.extra["ai_greeting_settings"] 配置了对应角色，严格按面板选拔；
           - 看板娘智能兜底：若玩家未配置（全留空），提取看板娘（board_hero + random_list + show_hero），<=3 全选，>3 选 3 位；全空退化至 1084 薇儿丹蒂；
        3. 特殊路由：1045（赤音·亚莉莎）、1046（血弹·雪儿）统一转交 1084 薇儿丹蒂。
        """
        event_type = event_info.get("type", "holiday")

        # ---------------------------------------------------------------------
        # 轨一：hero_birthday 寿星邀约专属轨（绝对短路，仅寿星 1 人）
        # ---------------------------------------------------------------------
        if event_type == "hero_birthday":
            hero_rec_id = event_info.get("record_id", 0)
            target_char_id = ARCHIVE_MAP.get(hero_rec_id, hero_rec_id)
            if not target_char_id:
                target_char_id = event_info.get("char_id", 0)
            if not target_char_id:
                return []
            # 角色自身生日不设男女限制，联动角色保持原样，不特殊转交薇儿丹蒂
            return [target_char_id]

        # ---------------------------------------------------------------------
        # 轨二：user_birthday / holiday / solar_term
        # ---------------------------------------------------------------------
        extra = _load_user_extra(db, uid)
        user_settings = extra.get("ai_greeting_settings", {})

        candidates = []
        has_explicit_settings = bool(user_settings and isinstance(user_settings, dict))

        # 1. 玩家有显式配置时，按配置精确筛选
        if has_explicit_settings:
            for char_key, mode in user_settings.items():
                try:
                    cid = int(char_key)
                except ValueError:
                    continue
                if mode == "all":
                    candidates.append(cid)
                elif mode == "holiday" and event_type in ("holiday", "solar_term"):
                    candidates.append(cid)
                elif mode == "birthday" and event_type in ("user_birthday", "player_birthday"):
                    candidates.append(cid)

        # 2. 玩家未配置（全留空）时，走看板娘智能兜底（<=3 规约，按修正者角色档案归一化）
        if not has_explicit_settings:
            board_list = []
            try:
                # 1) 主看板娘与大厅展示列表
                g_rows = db.query("SELECT board_hero, show_hero FROM game_user WHERE uid=?", (uid,))
                if g_rows:
                    bh = g_rows[0].get("board_hero")
                    if bh:
                        norm_bh = self.normalize_board_item(bh)
                        if norm_bh > 0:
                            board_list.append(norm_bh)
                    sh_raw = g_rows[0].get("show_hero") or ""
                    if sh_raw:
                        try:
                            sh_arr = json.loads(sh_raw)
                            if isinstance(sh_arr, list):
                                for x in sh_arr:
                                    norm_x = self.normalize_board_item(x)
                                    if norm_x > 0:
                                        board_list.append(norm_x)
                        except Exception:
                            pass

                # 2) 大厅多看板娘轮换列表 (random_list)
                rand_dict = extra.get("random_list", {})
                if isinstance(rand_dict, dict):
                    for r_arr in rand_dict.values():
                        if isinstance(r_arr, list):
                            for x in r_arr:
                                norm_x = self.normalize_board_item(x)
                                if norm_x > 0:
                                    board_list.append(norm_x)
            except Exception as e:
                logger.warning(f"提取用户看板娘列表异常: {e}")

            # 保持顺序去重（主看板娘优先 -> 展示列表 -> 轮播池）
            dedup = list(dict.fromkeys(board_list))
            if not dedup:
                dedup = [1084]  # 终极大兜底：官方看板娘薇儿丹蒂

            if len(dedup) <= 3:
                candidates = dedup
            else:
                candidates = dedup[:3]

        # 3. 防 OOC 拦截与性别过滤：
        # - 仅女性角色会对玩家发起主动问候，男性角色（没有聊天窗）即便在看板也直接抛弃；
        # - 仅已配置非空专属人格提示词的角色才予以保留；未配置人设的角色在内部直接丢弃（drop）！
        valid_candidates = []
        for cid in candidates:
            # 排除已知男性角色及无对话窗口的联动角色
            if cid in (1035, 1081, 1181, 1031, 1045, 1046):
                continue
            persona = ""
            if hasattr(db, "get_character_persona_prompt"):
                persona = db.get_character_persona_prompt(uid, cid) or ""
            elif hasattr(db, "query"):
                c_rows = db.query(
                    "SELECT system_prompt FROM ai_characters "
                    "WHERE char_id=? OR (',' || REPLACE(hero_ids, ' ', '') || ',') LIKE ?",
                    (cid, f"%,{cid},%")
                )
                if c_rows:
                    persona = c_rows[0].get("system_prompt") or ""
            if persona and persona.strip():
                valid_candidates.append(cid)
            else:
                logger.info(f"[AITranslator] 角色 cid={cid} 未配置专属人格提示词，已在问候广播中安全抛弃")

        return list(dict.fromkeys(valid_candidates))

    # =========================================================================
    #  三、独立临时窗口问候信件生成
    # =========================================================================

    def _generate_single_greeting(self, uid, char_id, event_info, db, is_overview=False, is_mascot=False):
        """
        单槽生成问候信件（运行在独立 Worker Slot 中，绝对零污染 ai_chat_memory）：
        1. 获取角色名与人设信息（若 is_overview=True，统一为发件人“深空之眼”）；
        2. 构建一次性独立 Prompt（分流寿星邀约、管理员庆生、时令节日、深空之眼总览）；
        3. 调用大模型（超时或异常时无缝切换至高质量官方静态模板）；
        4. 调配礼物白名单附件（严格防直写背包）；
        5. 调用 MailService.send_mail 发送入库。
        """
        is_overview_mode = bool(is_overview or is_mascot)
        char_name = "深空之眼" if is_overview_mode else "修正者"
        system_prompt = ""

        # 查验角色基础信息与专属人格提示词
        if not is_overview_mode:
            try:
                # 1. 优先读取玩家专属人格提示词
                if hasattr(db, "get_character_persona_prompt"):
                    system_prompt = db.get_character_persona_prompt(uid, char_id) or ""
                
                c_rows = db.query(
                    "SELECT char_name, system_prompt, greeting_msg FROM ai_characters "
                    "WHERE char_id=? OR (',' || REPLACE(hero_ids, ' ', '') || ',') LIKE ?",
                    (char_id, f"%,{char_id},%")
                )
                if c_rows:
                    char_name = c_rows[0].get("char_name") or "修正者"
                    if not system_prompt:
                        system_prompt = c_rows[0].get("system_prompt") or ""
                else:
                    b_info = self._hero_birthdays.get(str(char_id)) or self._hero_birthdays.get(str(HERO_TO_ARCHIVE.get(char_id, 0)))
                    if b_info:
                        char_name = b_info.get("hero_name", "修正者")
            except Exception as e:
                logger.warning(f"查询 char_id={char_id} 角色信息异常: {e}")

        event_name = event_info.get("name", "特别纪念日")
        event_desc = event_info.get("desc", "")
        event_type = event_info.get("type", "holiday")

        # 角色生日双轨判定：
        # 第一判定：是否有角色人格提示词。
        # 若有，走一条不干扰该角色历史对话的新临时沙盒会话，让模型生成第一人称邀约；
        # 若没有，则回退为深空之眼官方通用公函模板，不接大模型。
        is_hero_birthday_fallback = False
        if event_type == "hero_birthday":
            if not system_prompt or not system_prompt.strip():
                is_hero_birthday_fallback = True
                is_overview_mode = True  # 发件人与落款统一为深空之眼

        # 默认标题与正文
        if is_hero_birthday_fallback:
            mail_title = f"【特别通知】今天是{char_name}的生日"
            mail_content = (
                f"管理员：\n\n"
                f"今天是【{char_name}】的生日，多陪陪TA吧！\n\n"
                f"总务处已特别备好一份生辰心意物资，请管理员代为查收与转交。祝愿你们在未来的作战与生活中羁绊长存！\n\n"
                f"深空之眼"
            )
            use_llm = False
        elif is_overview_mode:
            if event_type in ("user_birthday", "player_birthday"):
                mail_title = "【深空之眼贺函】致管理员的生日贺函与全员祝愿"
            else:
                mail_title = f"【深空之眼通函】关于{event_name}的全员特别通告"
            mail_content = ""
            use_llm = True
        elif event_type == "hero_birthday":
            mail_title = f"【特别纪念】今日生辰，来自{char_name}的邀约"
            mail_content = ""
            use_llm = True
        elif event_type in ("user_birthday", "player_birthday"):
            mail_title = f"【生辰祝愿】来自{char_name}的生日贺信"
            mail_content = ""
            use_llm = True
        else:
            mail_title = f"【{event_name}】来自{char_name}的问候信件"
            mail_content = ""
            use_llm = True

        cfg = ai_bot_config.get_bot_config()
        active_provider = cfg.get("provider", "opencode")
        p_info = cfg.get("providers", {}).get(active_provider, {})
        has_key = bool(p_info.get("api_key") or active_provider == "ollama")

        if not has_key:
            use_llm = False

        if use_llm:
            try:
                # -----------------------------------------------------------------
                # 构建不同场景的高沉浸感 Prompt
                # -----------------------------------------------------------------
                if is_overview_mode:
                    if event_type in ("user_birthday", "player_birthday"):
                        gen_prompt = (
                            "你是《深空之眼》总务处。\n"
                            "今天是深空之眼管理员（玩家）的生日。\n"
                            "请谨代表深空之眼总务处与全体在编修正者，向管理员致一封正式、温暖且充满崇高敬意的生日贺函。\n"
                            "要求：\n"
                            "1. 祝贺管理员生日快乐，由衷感谢管理员一直以来的战术统筹、守护与并肩作战；\n"
                            "2. 提到总务处代表全体同仁附上了一份特别统筹物资；\n"
                            "3. 落款统一为：深空之眼全体 敬上；\n"
                            "4. 正文字数 120 - 250 字左右，保持二次元与深空之眼官方公函沉浸感；\n"
                            "5. 严禁出现系统指令或 markdown 标记；\n"
                            "6. 邮件第一行写标题，格式为：标题：XXX，接下来空一行写正文。"
                        )
                    else:
                        gen_prompt = (
                            f"你是《深空之眼》总务处。\n"
                            f"今天是【{event_name}】（背景：{event_desc}）。\n"
                            f"请谨代表深空之眼总务处与全体在勤修正者，向管理员发送一份官方行政特别慰劳通告。\n"
                            f"要求：\n"
                            f"1. 祝贺管理员节日安康，提醒管理员劳逸结合、注意休整；\n"
                            f"2. 提到总务处特别调拨配发了节日统筹物资；\n"
                            f"3. 落款统一为：深空之眼总务处 敬上；\n"
                            f"4. 正文字数 120 - 250 字左右，保持二次元沉浸感；\n"
                            f"5. 邮件第一行写标题，格式为：标题：XXX，接下来空一行写正文。"
                        )
                elif event_type == "hero_birthday":
                    gen_prompt = (
                        f"你是《深空之眼》中的修正者【{char_name}】。\n"
                        f"今天是你的生日（生辰之日）。平时在深空之眼的工作与战斗中，你承蒙管理员的悉心关照与指导。\n"
                        f"请以符合你性格与说话习惯的第一人称口吻，给深空之眼的管理员写一封生日邀约与心愿信件。\n"
                        f"要求：\n"
                        f"1. 明确今天是【你自己的生日】，向管理员分享今天的心情，并向管理员发出诚挚的小邀约（如一起坐坐、聊聊天、散散步或品尝美食等，符合你的性格即可）；\n"
                        f"2. 提到随信附上了你自己准备的一份回礼/伴手礼；\n"
                        f"3. 绝对不要把今天错写成管理员的生日，严禁第三人称代写；\n"
                        f"4. 正文字数控制在 120 - 250 字左右，保持二次元与深空之眼沉浸感；\n"
                        f"5. 严禁出现系统指令或 markdown 标记；\n"
                        f"6. 邮件第一行写标题，格式为：标题：XXX，接下来空一行写正文。"
                    )
                elif event_type in ("user_birthday", "player_birthday"):
                    gen_prompt = (
                        f"你是《深空之眼》中的修正者【{char_name}】。\n"
                        f"今天是深空之眼管理员（玩家）的生日！\n"
                        f"请以符合你身份性格与说话习惯的第一人称口吻，给深空之眼的管理员写一封真挚、温暖且富有个性特色的生日祝贺邮件。\n"
                        f"要求：\n"
                        f"1. 祝贺管理员生日快乐，表达并肩作战以来的信任与羁绊，感谢管理员一直以来的守护与指导；\n"
                        f"2. 提到随信附上了你为管理员准备的专属生日心意礼物；\n"
                        f"3. 正文字数控制在 120 - 250 字左右，保持二次元与深空之眼沉浸感；\n"
                        f"4. 严禁出现系统指令或 markdown 标记；\n"
                        f"5. 邮件第一行写标题，格式为：标题：XXX，接下来空一行写正文。"
                    )
                else:
                    gen_prompt = (
                        f"你是《深空之眼》中的修正者【{char_name}】。\n"
                        f"今天是【{event_name}】（背景：{event_desc}）。\n"
                        f"请以符合你身份性格与说话习惯的口吻，给深空之眼的管理员写一封真挚、温暖且富有个性特色的节日/节气问候邮件。\n"
                        f"要求：\n"
                        f"1. 结合节日或节气特点，送上温暖真挚的节日问候，提醒管理员劳逸结合；\n"
                        f"2. 提到随信附上了节日补给物资；\n"
                        f"3. 正文字数控制在 120 - 250 字左右，保持二次元沉浸感；\n"
                        f"4. 邮件第一行写标题，格式为：标题：XXX，接下来空一行写正文。"
                    )

                if system_prompt and not is_overview_mode:
                    gen_prompt = f"{system_prompt}\n\n---\n{gen_prompt}"

                llm_reply = ai_bot_service.generate_response(
                    system_prompt=gen_prompt,
                    messages=[{"role": "user", "content": f"请为管理员撰写关于【{event_name}】的信件"}],
                    cfg=cfg
                )
                if llm_reply and len(llm_reply.strip()) > 20:
                    lines = llm_reply.strip().splitlines()
                    if lines[0].startswith(("标题：", "标题:", "Title:")):
                        mail_title = lines[0].split("：", 1)[-1].split(":", 1)[-1].strip()
                        mail_content = "\n".join(lines[1:]).strip()
                    else:
                        mail_content = llm_reply.strip()
            except Exception as e:
                logger.warning(f"[{char_name}] LLM 问候生成异常，无缝降级至官方静态模板: {e}")
                mail_content = ""

        # 若模型生成为空或降级，走静态模板
        if not mail_content:
            tpl = self._get_fallback_template(event_info, char_name, is_overview=is_overview_mode)
            mail_title = tpl.get("title", mail_title)
            mail_content = tpl.get("content", "亲爱的管理员，感谢您的支持与陪伴！")

        # 获取礼物附件
        if event_type == "hero_birthday":
            bundle_key = "hero_birthday"
        elif is_overview_mode:
            bundle_key = "deepspace_overview"
        elif event_type in ("user_birthday", "player_birthday"):
            bundle_key = "user_birthday"
        else:
            bundle_key = event_info.get("bundle", "holiday_general")

        attachments = self._get_bundle_attachments(bundle_key)

        # 调配发件人：总览公函统一为“深空之眼”
        sender_name = "深空之眼" if is_overview_mode else char_name

        # 调配发信
        mail_svc = MailService.get_instance(db=db)
        mail_id = mail_svc.send_mail(
            uid=uid,
            title=mail_title,
            content=mail_content,
            attachments=attachments,
            sender=sender_name,
            template_id=0,
            db=db
        )
        if not mail_id:
            raise RuntimeError(f"问候邮件写入失败: uid={uid} sender={sender_name}")
        logger.info(f"成功发送问候信件: uid={uid} sender={sender_name} event={event_name} mail_id={mail_id}")
        return {
            "mail_id": mail_id,
            "char_name": sender_name,
            "sender": sender_name,
            "title": mail_title,
            "event": event_name,
            "is_overview": is_overview_mode,
            "is_mascot": is_overview_mode
        }

    def _get_fallback_template(self, event_info, char_name, is_overview=False, is_mascot=False):
        """获取官方风静态信件模板"""
        is_overview_mode = bool(is_overview or is_mascot)
        etype = event_info.get("type")
        ename = event_info.get("name")

        if is_overview_mode:
            if etype in ("user_birthday", "player_birthday"):
                return self._templates.get("deepspace_overview", {})
            t = self._templates.get("deepspace_holiday_overview") or self._templates.get("mascot_communique", {})
            return {
                "title": t.get("title", f"【深空之眼通函】关于{ename}的特别通告"),
                "content": t.get("content", "").replace("{event_name}", ename or "特别节日")
            }

        if etype in ("user_birthday", "player_birthday"):
            return self._templates.get("user_birthday") or self._templates.get("player_birthday", {})

        if etype == "hero_birthday":
            return self._templates.get("hero_birthday", {})

        if etype == "solar_term":
            t = self._templates.get("solar_terms_default", {})
            return {
                "title": t.get("title", "【节气问候】顺时调摄").replace("{solar_term}", ename or ""),
                "content": t.get("content", "").replace("{solar_term}", ename or "")
            }

        holidays = self._templates.get("holidays", {})
        if ename in holidays:
            return holidays[ename]

        # 通用兜底
        return {
            "title": f"【节日致意】关于{ename}的特别信件",
            "content": f"亲爱的管理员：\n今日是【{ename}】。在这难得的美好时节里，修正者伙伴们为您送上真挚的祝福与节日补给，愿您万事顺遂！"
        }

    def _get_bundle_attachments(self, bundle_key):
        """根据礼包类型从白名单调配附件"""
        preset_bundles = self._gifts.get("preset_bundles", {})
        if bundle_key in preset_bundles:
            return preset_bundles[bundle_key]
        if bundle_key == "user_birthday" and "player_birthday" in preset_bundles:
            return preset_bundles["player_birthday"]
        if bundle_key == "deepspace_overview" and "mascot_communique" in preset_bundles:
            return preset_bundles["mascot_communique"]
        return preset_bundles.get(bundle_key, [{"id": 2, "number": 20000}, {"id": 20002, "number": 1}])

    # =========================================================================
    #  四、3 线程并发池问候分发中枢 (Worker Pool)
    # =========================================================================

    def check_daily_greetings(self, uid, db=None, cur_date=None, force=False):
        """同一进程内只允许一个问候批次运行，避免并发请求绕过防重检查。"""
        if not self._dispatch_lock.acquire(blocking=False):
            return {
                "status": "busy",
                "dispatched_count": 0,
                "dispatched": [],
                "error": "问候调度任务正在执行，请稍后重试",
            }
        try:
            return self._check_daily_greetings(uid, db=db, cur_date=cur_date, force=force)
        finally:
            self._dispatch_lock.release()

    def _check_daily_greetings(self, uid, db=None, cur_date=None, force=False):
        """
        每日问候核心入口（支持登录洪流挂接与定时器触发）：
        1. 获取今日事件（对接 ai_calendar）；
        2. 严格 3 线程并发池调度生成；
        3. 角色生日：有且仅有寿星 1 人（寿星邀约），绝不追加总览信；
        4. 管理员生日与重大节日：面板配置角色（或看板娘）发送祝贺信后，追加一封发件人为“深空之眼”的统筹贺函；
        5. 每日幂等防重守卫与年度防重锁；
        6. 全流程记录与回执。
        """
        _db = self._get_db(db)
        today_date = cur_date if cur_date is not None else datetime.date.today()
        if isinstance(today_date, datetime.datetime): today_date = today_date.date()
        cal_data = get_calendar_events(today_date, uid=uid, db=_db)
        events = cal_data.get("events", [])
        if not events:
            return {
                "status": "no_events",
                "dispatched_count": 0,
                "dispatched": [],
                "state_saved": True,
            }

        extra = _load_user_extra(_db, uid)
        sent_history = extra.setdefault("ai_greeting_sent", {})
        cur_year = today_date.year
        date_str = today_date.isoformat()

        all_dispatched = []

        for ev in events:
            ev_type = ev.get("type", "holiday")
            ev_name = ev.get("name", "特别日")

            # 1. 防重 key 精确构建
            if ev_type == "hero_birthday":
                char_tag = ev.get("record_id") or ev.get("char_id") or ev_name
                ev_key = f"{date_str}_hero_birthday_{ev_name}_{char_tag}"
            elif ev_type in ("user_birthday", "player_birthday"):
                ev_key = f"{date_str}_user_birthday"
                # 玩家生日年度防重锁
                last_b_year = extra.get("last_birthday_mail_year", 0)
                if not force and last_b_year == cur_year:
                    continue
            else:
                ev_key = f"{date_str}_{ev_type}_{ev_name}"

            # 2. 每日防重检查
            if not force and ev_key in sent_history.get(date_str, []):
                continue

            # 3. 筛选候选角色（仅已配置人格提示词的女性角色）
            candidates = self.resolve_candidates(uid, _db, ev)
            results = []
            if candidates:
                # 4. 严格 3 线程并发工作槽 (Worker Pool) 分批推进
                max_workers = min(3, len(candidates))
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_char = {
                        executor.submit(self._generate_single_greeting, uid, cid, ev, _db, False): cid
                        for cid in candidates
                    }
                    for future in concurrent.futures.as_completed(future_to_char):
                        try:
                            res = future.result()
                            if res:
                                results.append(res)
                        except Exception as e:
                            cid = future_to_char[future]
                            logger.error(f"角色 char_id={cid} 问候信件生成失败: {e}")

            event_dispatched = list(results)

            # 5. 管理员生日（user_birthday）或重大节日（holiday_major）时，追加深空之眼统筹公函
            # 注意：hero_birthday 绝不追加统筹信！
            need_overview = (
                ev_type in ("user_birthday", "player_birthday") or
                (ev_type == "holiday" and ev.get("bundle") == "holiday_major")
            )
            if need_overview:
                try:
                    overview_res = self._generate_single_greeting(uid, 0, ev, _db, is_overview=True)
                    if overview_res:
                        event_dispatched.append(overview_res)
                except Exception as me:
                    logger.error(f"深空之眼统筹公函发送异常: {me}")

            all_dispatched.extend(event_dispatched)

            # 只有至少一封邮件成功落库后才记录防重，失败事件允许稍后重试
            if event_dispatched:
                sent_history.setdefault(date_str, []).append(ev_key)
                if ev_type in ("user_birthday", "player_birthday"):
                    extra["last_birthday_mail_year"] = cur_year

        state_saved = _save_user_extra(_db, uid, extra)
        return {
            "status": "ok" if state_saved else "partial",
            "date": date_str,
            "dispatched_count": len(all_dispatched),
            "dispatched": all_dispatched,
            "state_saved": state_saved,
        }

    def trigger_login_greeting(self, uid, db=None):
        """登录钩子：静默触发今日问候检查（零阻塞）"""
        def _worker():
            try:
                return self.check_daily_greetings(uid, db=db, force=False)
            except Exception as e:
                logger.warning(f"登录触发 AI 问候信件异常 (uid={uid}): {e}")
                return None

        return _login_greeting_executor.submit(_worker)

    def set_character_greeting_setting(self, uid, char_id, mode, db=None):
        """保存玩家对某角色的问候偏好设置 (all / holiday / birthday / none)"""
        return self.set_character_greeting_settings(uid, {char_id: mode}, db=db)

    def set_character_greeting_settings(self, uid, updates, db=None):
        """一次性校验并持久化多角色问候设置，避免批量请求部分成功。"""
        _db = self._get_db(db)
        if not _db:
            raise RuntimeError("数据库连接未就绪")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("问候设置不能为空")
        allowed_modes = {"all", "holiday", "birthday", "none"}
        normalized = {}
        for char_id, mode in updates.items():
            try:
                cid = int(char_id)
            except (TypeError, ValueError):
                raise ValueError(f"角色编号非法: {char_id}")
            mode_str = str(mode)
            if mode_str not in allowed_modes:
                raise ValueError(f"问候模式非法: {mode_str}")
            normalized[str(cid)] = mode_str

        extra = _load_user_extra(_db, uid)
        settings = extra.setdefault("ai_greeting_settings", {})
        settings.update(normalized)
        if not _save_user_extra(_db, uid, extra):
            raise RuntimeError(f"保存玩家 uid={uid} 问候设置失败")
        return settings

    def get_character_greeting_settings(self, uid, db=None):
        """获取玩家全量角色的问候偏好设置"""
        _db = self._get_db(db)
        extra = _load_user_extra(_db, uid)
        return extra.get("ai_greeting_settings", {})
