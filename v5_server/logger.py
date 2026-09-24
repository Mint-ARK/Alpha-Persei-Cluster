# -*- coding: utf-8 -*-
"""
logger.py — 统一双轨日志器

职责：
1. 双轨分流 (Dual-Rail Demuxing):
   - 底账文件流：server/logs/middleware_YYYYMMDD.log（按天无损全量落盘，保留底层细节供深度排查）；
   - 控制台实时流：sys.stdout -> server_live.log（经 main.py _TeeLogger 拦截），收束杂音，聚焦玩家动向与业务日志。
2. 规范化格式：
   [{YYYY-MM-DD HH:MM:SS}][{LEVEL}][{MODULE}] {Message}{Context}
3. 线程安全：多连接并发写不串行。
4. 容错防御：任何情况下日志器永不崩溃阻断业务主线程。
"""
import os
import sys
import threading
import time

try:
    import log_sifter
except ImportError:
    from . import log_sifter

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_DEFAULT_LEVEL = os.environ.get("LOG_LEVEL", os.environ.get("MW_LOG_LEVEL", "INFO")).upper()
_LEVEL = _LEVELS.get(_DEFAULT_LEVEL, 20)

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOCK = threading.Lock()
_fh = None
_fh_path = None


def _ensure_file():
    """按天滚动：middleware_YYYYMMDD.log。返回文件句柄。"""
    global _fh, _fh_path
    day = time.strftime("%Y%m%d")
    p = os.path.join(_LOG_DIR, f"middleware_{day}.log")
    if _fh_path != p:
        if _fh is not None:
            try:
                _fh.close()
            except Exception:
                pass
        os.makedirs(_LOG_DIR, exist_ok=True)
        _fh = open(p, "a", encoding="utf-8")
        _fh_path = p
    return _fh


def log(*args, **kwargs):
    """
    通用统一日志写入入口。
    
    支持多种调用形态：
      log("msg", "INFO", cmd=30002, uid=2149778712)
      log("msg", level="INFO", module="NET")
      log(arg1, arg2, arg3)
      log("msg", to_console=False)  # 仅进底账文件，不污染控制台
    """
    global _LEVEL

    # 1. 提取关键字参数
    cmd = kwargs.pop("cmd", None)
    uid = kwargs.pop("uid", None)
    level = kwargs.pop("level", None)
    module = kwargs.pop("module", kwargs.pop("tag", None))
    to_console = kwargs.pop("to_console", None)

    # 2. 提取与清洗位置参数
    if len(args) == 0:
        return
    elif len(args) > 1 and str(args[1]).upper() in ("DEBUG", "INFO", "WARN", "ERROR"):
        level = str(args[1]).upper()
        msg = str(args[0])
        extra_args = " ".join(str(a) for a in args[2:])
        if extra_args:
            msg += " " + extra_args
    else:
        msg = " ".join(str(a) for a in args)

    if not level:
        level = "INFO"
    level = str(level).upper()

    lv_val = _LEVELS.get(level, 20)
    if lv_val < _LEVEL:
        return

    # 3. 智能提取或规范模块标签 (Module Tag)
    clean_msg = msg
    if not module:
        if clean_msg.startswith("[") and "]" in clean_msg:
            first_bracket = clean_msg[1:clean_msg.index("]")]
            if first_bracket in ("DEBUG", "INFO", "WARN", "ERROR"):
                pass
            elif not first_bracket.startswith("202") and not first_bracket.startswith("203"):
                module = first_bracket
                clean_msg = clean_msg[clean_msg.index("]") + 1:].strip()

    if not module:
        module = "SERVER"
    module = str(module).upper().strip()

    # 4. 组装上下文与时间戳
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    ctx = ""
    if cmd is not None and f"cmd={cmd}" not in clean_msg and f"CS_{cmd}" not in clean_msg:
        ctx += f" cmd={cmd}"
    if uid is not None and f"uid={uid}" not in clean_msg:
        ctx += f" uid={uid}"

    # 统一前缀：若原始消息已有时间戳与等级则保持，否则统一标准格式
    if clean_msg.startswith("[202") or clean_msg.startswith("[203"):
        line = f"{clean_msg}{ctx}\n"
    else:
        line = f"[{ts}][{level}][{module}] {clean_msg}{ctx}\n"

    # 5. 【第一轨：全量底账文件流】100% 原始、无损写入每日滚动日志
    try:
        with _LOCK:
            f = _ensure_file()
            f.write(line)
            f.flush()
    except Exception:
        pass

    # 6. 【第二轨：控制台人机流】经过分流器裁决与收束后输出至 stdout -> server_live.log
    if to_console is False:
        return

    # 智能分流裁决：抑制机械帧刷屏与高频网络切帧
    if to_console is None and log_sifter.should_suppress_for_console(clean_msg, module=module, level=level):
        return

    try:
        sys.stdout.write(line)
        sys.stdout.flush()
    except Exception:
        pass


def set_level(level_name):
    """运行时调整全局日志级别（DEBUG/INFO/WARN/ERROR）。"""
    global _LEVEL
    if isinstance(level_name, int):
        _LEVEL = level_name
        return
    lv = _LEVELS.get(str(level_name).upper())
    if lv is not None:
        _LEVEL = lv


def get_level_name() -> str:
    """获取当前生效的全局日志级别名称。"""
    for k, v in _LEVELS.items():
        if v == _LEVEL:
            return k
    return "INFO"


if __name__ == "__main__":
    # 自检：写三种级别 + 检查文件与分流
    set_level("DEBUG")
    log("logger 自检 DEBUG 帧", "DEBUG", module="TEST", cmd=0)
    log("logger 自检 INFO", "INFO", module="TEST", cmd=10042, uid=2149778712)
    log("logger 自检 WARN", "WARN", module="TEST", cmd=1)
    log("logger 自检 ERROR", "ERROR", module="TEST")
    p = os.path.join(_LOG_DIR, f"middleware_{time.strftime('%Y%m%d')}.log")
    print("日志文件路径:", p)
    print("文件存在:", os.path.exists(p))
    with open(p, encoding="utf-8") as f:
        tail = f.readlines()[-4:]
    for t in tail:
        print("  |", t.rstrip())
