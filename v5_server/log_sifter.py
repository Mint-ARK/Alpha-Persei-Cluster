# -*- coding: utf-8 -*-
"""
log_sifter.py — 服务端双轨日志分流与业务动向处理引擎

核心职责：
1. 双轨分流 (Dual-Rail Demuxing):
   - 全量底账轨 (Full File Rail) : 100% 原始、无损写入 logs/middleware_YYYYMMDD.log，保留所有底层协议与网络细节供深度排查；
   - 控制台人机轨 (Console Rail)  : 写入 stdout -> server_live.log（CMD 终端 / 桌面管理客户端 / Web 控制台），收束杂音，高亮动向。
2. 资产流收束 (Asset Stream Shaping):
   - 客户端请求配置/CDN 资源时，底层分块传输细节仅入底账文件，流程完毕时在控制台输出单行：
     [CDN] 资产 [notice_cfg.json] 传递完毕 (200 OK)
3. 玩家操作物流日志 (Logistics Stream):
   - 自动将玩家的核心操作转换为语义化物流动向：
     CS_XXXX -> SC_XXXX [业务名称] | uid=xxx | 奖励/消耗摘要
4. 心跳流节流脉冲 (Heartbeat Throttling):
   - 全量心跳进底账文件；控制台每 30~60 秒合并汇报一次心跳状态，消除机械高频刷屏。
"""
import time
import threading

# ---------------- 高频协议动向释义字典 (Logistics Dictionary) ----------------
# 涵盖核心玩法与高频交互，未收录项自动通过 Operation docstring / class name 智能提取
LOGISTICS_DICT = {
    # 基础与登录体系
    10038: ("网关分配请求", "sc_10039 网关区服分配"),
    10042: ("原生登录握手", "sc_10043 登录凭证下发"),
    10050: ("心跳时钟同步", "sc_10051 心跳脉冲维持"),
    10200: ("全量数据同步", "登录洪流下发 (基础/角色/资产/关卡初始化完成)"),
    10500: ("业务时间同步", "sc_10501 刷新时钟与体力对齐"),
    10700: ("服务器时间同步", "sc_10701 服务器时间戳同步"),
    10600: ("已开放系统同步", "sc_10601 系统开放列表更新"),

    # 签到与福利
    11010: ("每日签到", "sc_11011 每日签到完成，奖励已入库"),
    11081: ("活动签到领取", "sc_11082 活动签到奖励已发放"),

    # 体力与精力
    12002: ("购买体力", "sc_12003 体力购买成功，钻石/道具已扣减"),
    12024: ("精力药剂使用", "sc_12025 精力补充成功"),
    12046: ("每日午晚餐体力领取", "sc_12047 免费体力已领取"),

    # 修正者 (Hero) 养成与进阶
    13012: ("修正者升级", "sc_13013 消耗经验材料，等级提升"),
    13014: ("修正者突破", "sc_13015 消耗突破材料，阶位突破完成"),
    13016: ("修正者超越", "sc_13017 消耗情报碎片，神识超越(星级提升)"),
    13018: ("修正者解锁", "sc_13019 情报合成，新修正者加入队伍"),
    13022: ("修正者立绘切换", "sc_13023 默认立绘更新"),
    13024: ("修正者皮肤更换", "sc_13025 换装完成"),
    13044: ("钥从(神识超越)解锁", "sc_13045 修正者钥从绑定"),

    # 武器 (Weapon) 养成
    14012: ("武器升级", "sc_14013 消耗材料，武器经验强化"),
    14014: ("武器突破", "sc_14015 武器阶位突破"),

    # 刻印 (Equip) 体系
    15012: ("刻印强化", "sc_15013 消耗刻印经验，强化完成"),
    15016: ("刻印赋能", "sc_15017 消耗赋能模块，属性洗炼完成"),
    15020: ("刻印突破", "sc_15021 刻印上限突破"),
    15022: ("刻印神系重构", "sc_15023 重构同调神系完成"),

    # 技能体系
    16010: ("技能升级", "sc_16011 消耗金币与技能点，技能强化"),

    # 道具与背包 (Inventory)
    17012: ("道具消耗使用", "sc_17013 消耗道具，产出已入库"),
    17028: ("批量道具使用", "sc_17029 批量礼盒开启，多项道具入库"),

    # 矩阵多维变量 (Polyhedron)
    18010: ("多维变量开启挑战", "sc_18011 变量轮次启动"),
    18012: ("多维变量选择信标", "sc_18013 强化词条生效"),
    18014: ("多维变量结算", "sc_18015 轮次挑战终结结算"),

    # 抽卡 (Gacha / Draw)
    20012: ("探测召唤(抽卡)", "sc_20013 消耗探测凭证，修正者/钥从已入库"),

    # 活动与探索
    24052: ("探索标记保存", "sc_24053 标记已保存"),
    24054: ("活动解谜通关", "sc_24055 解谜达成，奖励已入库"),

    # 任务系统 (Task)
    28010: ("单任务奖励领取", "sc_28011 任务完成奖励发放"),
    28014: ("每日任务一键全领", "sc_28015 每日任务一键领奖完成，活跃度与道具已入库"),
    28016: ("每周任务一键全领", "sc_28017 每周任务一键领奖完成，周活跃已提升"),

    # 邮件系统 (Mail)
    30002: ("邮件列表拉取", "sc_30003 邮箱目录已同步"),
    30004: ("邮件内容阅读", "sc_30005 邮件标记为已读"),
    30006: ("单封邮件附件领取", "sc_30007 附件道具已提取入库"),
    30008: ("邮件一键全领", "sc_30009 全部未领邮件附件一键提取入库"),

    # 外围偏好与主界面 (Peripheral)
    32012: ("主界面看板娘更换", "sc_32013 大厅主界面展示修正者已更新"),
    32014: ("大厅场景更换", "sc_32015 大厅背景场景已切换"),
    32032: ("名片徽章装配", "sc_32033 个人名片信息已保存"),
    32042: ("音乐唱片播放设置", "sc_32043 大厅背景音乐已切换"),

    # 好友与社交
    34010: ("好友申请/添加", "sc_34011 好友关系更新"),
    34012: ("好友赠送友情点", "sc_34013 友情点已送达"),

    # 战斗与关卡 (Battle / Stage)
    44010: ("常规关卡战斗开始", "sc_44011 扣减体力，下发关卡战斗凭证"),
    44012: ("常规关卡战斗结算", "sc_44013 战斗通关胜利，掉落材料与玩家经验已入库"),
    44014: ("关卡战斗放弃/失败", "sc_44015 返还体力，记录失败结算"),
    44024: ("历战轮回/梦境挑战结算", "sc_44025 挑战积分与徽章奖励已入库"),
    45004: ("黑区净化战斗结算", "sc_45005 净化通关，重构材料与异质结晶入库"),

    # 图鉴与收藏 (Illustrated)
    52004: ("图鉴剧情/立绘奖励领取", "sc_52005 收集奖励移光石已入库"),

    # 成就系统 (Achievement)
    53004: ("成就达成奖励领取", "sc_53005 成就点数增加，成就奖励已入库"),
    53006: ("成就物语阅读", "sc_53007 成就故事解锁"),

    # 游园街与宿舍 (BackHome / Dorm)
    58002: ("游园街宿舍交互", "sc_58003 好感度提升"),
    58050: ("游园街家具摆放保存", "sc_58051 宿舍布局已更新"),
    58100: ("游园街餐厅制作料理", "sc_58101 烹饪完成，菜品已入货架"),
    58114: ("游园街委托订单提交", "sc_58115 订单完成，游园街币已入库"),

    # 活动小游戏 (Minigame)
    60054: ("小游戏阶段奖励领取", "sc_60055 积分档位达成，活动道具入库"),
    83003: ("SP英雄日程规划", "sc_83004 日程计划已登记"),
    83005: ("SP英雄日程积分领取", "sc_83006 日程积分奖励已入库"),
    83009: ("SP英雄委托派遣", "sc_83010 委托已出发"),
    83013: ("SP英雄委托奖励领取", "sc_83014 派遣完成，委托奖励已入库"),
    84334: ("战车配件改装保存", "sc_84335 战车方案已更新"),
    89404: ("弹珠技能配置保存", "sc_89405 技能组合已生效"),

    # 公会 (Guild)
    88004: ("公会每日签到/捐赠", "sc_88005 获得公会声望与代币"),
}


class HeartbeatThrottler:
    """心跳脉冲节流器：底账文件每次如实记录；控制台每隔一段时间聚合汇报一次。"""

    def __init__(self, throttle_interval_sec: float = 30.0):
        self.interval = throttle_interval_sec
        self._last_report_ts = 0.0
        self._count_since_last = 0
        self._lock = threading.Lock()

    def record_pulse(self, uid: int = 0, peer: str = "") -> tuple[bool, str]:
        """
        记录一次心跳脉冲。
        返回 (should_log_console, summary_msg)
        """
        now = time.time()
        with self._lock:
            self._count_since_last += 1
            if now - self._last_report_ts >= self.interval:
                cnt = self._count_since_last
                self._count_since_last = 0
                self._last_report_ts = now
                u_str = f" uid={uid}" if uid else ""
                p_str = f" [{peer}]" if peer else ""
                msg = f"客户端心跳保持中{p_str}{u_str} (过去 {int(self.interval)}s 累计接收 {cnt} 次脉冲，连接正常)"
                return True, msg
            return False, ""


class AssetFlowShaper:
    """资产流收束器：底层 HTTP/CDN 切片不刷屏，单项传递完毕或批量汇总时在控制台输出单行。"""

    def __init__(self):
        self._completed_assets = set()
        self._lock = threading.Lock()

    def format_asset_finished(self, filename: str, size_bytes: int = 0, platform: str = "", status: str = "200 OK") -> str:
        """格式化单项资产传递完毕日志。"""
        size_str = ""
        if size_bytes > 0:
            if size_bytes >= 1024 * 1024:
                size_str = f", {size_bytes / (1024 * 1024):.2f}MB"
            elif size_bytes >= 1024:
                size_str = f", {size_bytes / 1024:.1f}KB"
            else:
                size_str = f", {size_bytes}B"

        plat_str = f" ({platform.upper()})" if platform else ""
        return f"资产 [{filename}] 传递完毕{plat_str} (status={status}{size_str})"


# 单例分流收束引擎实例
heartbeat_throttler = HeartbeatThrottler(throttle_interval_sec=30.0)
asset_flow_shaper = AssetFlowShaper()


def get_operation_name(cmd: int, op_instance=None) -> str:
    """获取操作语义中文名。优先查字典，其次查 Operation docstring，再次查 class name。"""
    if cmd in LOGISTICS_DICT:
        return LOGISTICS_DICT[cmd][0]

    if op_instance is not None:
        # 1. 尝试从 Operation 类属性 name 获取
        name = getattr(op_instance, "name", "")
        if name:
            return name
        # 2. 尝试从 cfg 获取
        cfg = getattr(op_instance, "cfg", None)
        if isinstance(cfg, dict) and cfg.get("name"):
            return cfg["name"]
        # 3. 尝试从 docstring 第一行提取
        doc = getattr(op_instance, "__doc__", "") or ""
        doc = doc.strip().split("\n")[0].strip()
        if doc:
            if "：" in doc:
                doc = doc.split("：")[0].strip()
            elif ":" in doc:
                doc = doc.split(":")[0].strip()
            if doc:
                return doc
        # 4. 从类名提纯
        cls_name = op_instance.__class__.__name__
        if cls_name.endswith("Op"):
            cls_name = cls_name[:-2]
        return cls_name

    return f"操作 CS_{cmd}"


def extract_logistics_details(result, data=None) -> str:
    """从 Operation 执行结果提取精简的物流/变动信息。"""
    if not result:
        return ""

    details = []
    if isinstance(result, dict):
        # 道具变动与奖励
        items = result.get("item_list") or result.get("items") or result.get("reward_list")
        if isinstance(items, list) and items:
            details.append(f"产出道具×{len(items)}种")

        # 任务/成就领取计数
        claimed = result.get("claimed_ids") or result.get("task_ids") or result.get("ids")
        if isinstance(claimed, list) and claimed:
            details.append(f"领取{len(claimed)}项")
        elif result.get("claimed") is True:
            details.append("成功领奖")

        # 抽卡召唤结果
        draw_res = result.get("draw_list") or result.get("card_list") or result.get("hero_list")
        if isinstance(draw_res, list) and draw_res:
            details.append(f"获得角色/钥从×{len(draw_res)}")

        # 体力与货币变动
        if "remain_vitality" in result:
            details.append(f"剩余体力={result['remain_vitality']}")
        if "gold" in result:
            details.append(f"金币={result['gold']}")
        if "diamond" in result:
            details.append(f"移光石={result['diamond']}")

    return f" | [{', '.join(details)}]" if details else ""


def build_logistics_line(cmd: int, sc: int, uid: int, op_name: str, details: str = "", extra: str = "") -> str:
    """组装人类可读的玩家操作物流动向日志。"""
    uid_str = f" | uid={uid}" if uid else ""
    extra_str = f" {extra}".rstrip()
    return f"CS_{cmd} -> SC_{sc} {op_name}完成{uid_str}{details}{extra_str}"


def should_suppress_for_console(line: str, module: str = "", level: str = "INFO") -> bool:
    """
    智能判定一条日志是否应当在控制台 (CMD / live_log) 中抑制。
    底账文件始终全量落盘，不受此函数限制。
    """
    s = line.strip()

    # 1. 抑制网络传输层逐帧打印 (如 [FRAME #123] CS_xxx -> SC_yyy)
    if s.startswith("[FRAME #") or " | Source: [" in s:
        return True

    # 2. 抑制 TCP / WSGI 接收原始 hex / 缓存细节
    if s.startswith("[TCP:") and ("recv " in s or "本次切帧" in s or "buf前=" in s):
        return True

    # 3. 抑制心跳常态逐条刷屏（已由 HeartbeatThrottler 节流聚合）
    if "cs_10050" in s or "sc_10051" in s or "心跳时间同步" in s:
        return True

    # 4. 抑制常规服务器时间请求与时间同步（保持控制台高信噪比）
    if "cs_10700 ->" in s or "cs_10500 ->" in s:
        return True

    # 5. 抑制 CDN / 流式代理的逐 chunk 调试信息
    if "[STREAM_PROXY]" in s and "len=" in s:
        return True

    # 6. 抑制高频空无意义的 core 响应帧日志（物流日志已完整覆盖）
    if s.startswith("[core] cs_") and "操作调度:" in s:
        return True
    if s.startswith("[core] cs_") and " -> 响应 " in s:
        return True

    # 7. 抑制 handler 内部旧版机械重复打印（如 cs_11010 -> 每日签到...，统一由 LOGISTICS 权威单行呈现）
    if module != "LOGISTICS" and (s.startswith("cs_") or s.startswith("CS_")) and ("->" in s or "→" in s):
        return True

    return False
