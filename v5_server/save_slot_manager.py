# -*- coding: utf-8 -*-
"""
save_slot_manager.py - 多存档槽位与游戏内选服状态机管理器

功能职责：
1. 槽位划分与端口编排：
   - 最大支持 32 个区服项 (Slot 1..32)
   - Slot 1 (8102): ➕【新建存档】
   - Slot 2 (8103): 🗑️【删除存档】 / ↩️【退出删除模式】(状态机切换跳板)
   - Slot 3..32 (8104..8134，自动跳过 game_port=8105): 最多 30 个真实存档业务槽 (Slot 01 ~ 30)
2. 动态扫描与元数据提取：
   - 扫描与 account.db 同级目录下的所有 *.db 存档
   - 智能校验 users 表数据并过滤临时/测试库
   - 轻量提取 uid, nick_name, user_level, mtime
   - 识别当前挂载的 Active DB
3. 服务端状态机控制 (State Machine)：
   - STATE_NORMAL: 正常游玩模式
   - STATE_DELETE_MODE: 删除选择模式
   - 60s 无操作自动超时复位
   - 删除前快照备份，Windows 回收站 / 其他系统 Trash 归档
4. 动态组装 /gateway/get?action=server 的下行响应
5. 全局活跃数据库动态热切换 (Hot-Switching)
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import ipaddress
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

BASE_GW_PORT = 8102
DEFAULT_GAME_PORT = 8105
TOTAL_SLOTS = 32
MAX_SAVE_SLOTS = 30
SLOT_INDEX_NEW = 1          # 8102
SLOT_INDEX_DELETE_TOGGLE = 2 # 8103
SLOT_INDEX_SAVES_START = 3   # 8104 .. 8134 (跳过 8105)

STATE_NORMAL = "NORMAL"
STATE_DELETE_MODE = "DELETE_MODE"
STATE_CREATE_MODE = "CREATE_MODE"
STATE_TIMEOUT_SECONDS = 60.0
# Remote SDK follow-ups allow time for the WAN gateway connection. Local
# queries use the short window so a retained SDK session can reach the gateway.
REGION_REFRESH_WINDOW_SECONDS = 0.35
REGION_REFRESH_FOLLOWUP_GAP_SECONDS = 3.0
REGION_REFRESH_FOLLOWUP_WINDOW_SECONDS = 3.0
REGION_REFRESH_TIMEOUT_SECONDS = 12.0
_DATABASE_SWITCH_LOCK = threading.RLock()
logger = logging.getLogger('save_slot_manager')


def _recycle_save_files(paths):
    """Windows recycle-bin only: never fall back to permanent deletion."""
    import ctypes
    from ctypes import wintypes as win
    from uuid import UUID

    def guid(value):
        return (ctypes.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)

    def check(result):
        if result < 0:
            raise OSError(f'移入Windows回收站失败 (0x{result & 0xffffffff:08X})')

    def method(obj, index, result, *types):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *types)(vtable[index])

    ole = ctypes.windll.ole32
    shell = ctypes.windll.shell32
    operation = ctypes.c_void_p()
    clsid = guid('3ad05575-8857-4850-9277-11b85bdb8e09')  # FileOperation
    operation_iid = guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')  # IFileOperation
    item_iid = guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')  # IShellItem
    check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    try:
        check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(operation_iid), ctypes.byref(operation)))
        # Recycle only, fail early and suppress Shell dialogs after panel confirmation.
        flags = 0x00080000 | 0x00100000 | 0x0400 | 0x0010 | 0x0004
        check(method(operation, 5, win.LONG, win.DWORD)(operation, flags))  # SetOperationFlags
        for path in paths:
            item = ctypes.c_void_p()
            check(shell.SHCreateItemFromParsingName(win.LPCWSTR(os.path.abspath(path)), None, ctypes.byref(item_iid), ctypes.byref(item)))
            try:
                check(method(operation, 18, win.LONG, ctypes.c_void_p, ctypes.c_void_p)(operation, item, None))  # DeleteItem
            finally:
                method(item, 2, win.ULONG)(item)  # Release
        check(method(operation, 21, win.LONG)(operation))  # PerformOperations
        aborted = win.BOOL()
        check(method(operation, 22, win.LONG, ctypes.POINTER(win.BOOL))(operation, ctypes.byref(aborted)))
        if aborted.value or any(path.exists() for path in paths):
            raise OSError('移入Windows回收站失败或被取消')
    finally:
        if operation:
            method(operation, 2, win.ULONG)(operation)
        ole.CoUninitialize()


def slot_id_to_port(slot_id: int, base_port: int = BASE_GW_PORT, game_port: int = DEFAULT_GAME_PORT) -> int:
    """计算槽位对应的 Gateway 端口，自动规避与 game_port (如 8105) 冲突"""
    port = base_port + (slot_id - 1)
    if base_port <= game_port and port >= game_port:
        port += 1
    return port


def port_to_slot_id(port: int, base_port: int = BASE_GW_PORT, game_port: int = DEFAULT_GAME_PORT) -> int:
    """根据 Gateway 端口反查槽位 ID (1..32)"""
    if base_port <= game_port and port > game_port:
        return port - base_port
    return port - base_port + 1


def is_valid_save_db(db_path: str) -> bool:
    """轻量检查是否为包含有效 users 数据的 SQLite 数据库"""
    if not os.path.isfile(db_path):
        return False
    try:
        if os.path.getsize(db_path) == 0:
            return False
        uri = f"file:{os.path.abspath(db_path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'")
            if not cur.fetchone():
                return False
            cur.execute("SELECT count(*) FROM users")
            row = cur.fetchone()
            return bool(row and row[0] > 0)
        finally:
            conn.close()
    except Exception:
        return False


@dataclass
class SaveSlotInfo:
    slot_id: int               # 1..32
    port: int                  # 8102..8134
    db_path: Optional[str]     # 物理路径
    save_name: str             # 存档名/标识
    uid: int
    nick_name: str
    user_level: int
    mtime: float
    is_active: bool
    is_action_item: bool = False
    action_type: str = ""      # "new", "delete_toggle", "save"


def extract_save_metadata(db_path: str) -> Tuple[int, str, int]:
    """轻量读取指定 SQLite 库中的 (uid, nick_name, user_level)"""
    uid = 2174928301
    nick_name = "管理员"
    user_level = 1

    if not os.path.isfile(db_path):
        return uid, nick_name, user_level

    try:
        uri = f"file:{os.path.abspath(db_path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            cursor = conn.cursor()
            # 1. 尝试从 server_global_config 获取 save.owner_uid
            try:
                cursor.execute("SELECT value FROM server_global_config WHERE key = 'save.owner_uid' LIMIT 1")
                row = cursor.fetchone()
                if row and row[0]:
                    uid = int(str(row[0]).strip())
            except Exception:
                pass

            # 2. 从 users 表获取昵称和等级 (支持 nick/nick_name 与 level/user_level 字段兼容)
            try:
                cursor.execute("PRAGMA table_info(users)")
                cols = [c[1] for c in cursor.fetchall()]
                nick_col = "nick" if "nick" in cols else ("nick_name" if "nick_name" in cols else None)
                level_col = "level" if "level" in cols else ("user_level" if "user_level" in cols else None)

                select_clause = f"uid, {nick_col or 'uid'}, {level_col or '1'}"
                cursor.execute(f"SELECT {select_clause} FROM users WHERE uid = ? LIMIT 1", (uid,))
                row = cursor.fetchone()
                if not row:
                    cursor.execute(f"SELECT {select_clause} FROM users LIMIT 1")
                    row = cursor.fetchone()
                if row:
                    uid = int(row[0])
                    nick_name = str(row[1]) if row[1] else "管理员"
                    user_level = int(row[2]) if row[2] else 1
            except Exception:
                pass
        finally:
            conn.close()
    except Exception:
        pass

    return uid, nick_name, user_level


def _inspect_save_owner(path):
    """Read-only preflight before AccountDB can migrate or seed a target file."""
    import generator
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    class Reader:
        def get_system_config(self, key):
            try:
                row = conn.execute('SELECT value FROM server_global_config WHERE key=?', (key,)).fetchone()
                return row['value'] if row else ''
            except sqlite3.OperationalError as exc:
                if 'no such table' not in str(exc):
                    raise
                return ''
        def query(self, sql, args=()):
            return [dict(row) for row in conn.execute(sql, args)]
    try:
        owner = generator.resolve_save_owner_uid(Reader())
        if not conn.execute('SELECT 1 FROM users WHERE uid=?', (owner,)).fetchone():
            raise ValueError('存档缺少默认账号或有效的单账号归属标记')
        return owner
    finally:
        conn.close()


def switch_active_database(target_db_path: str, core: Any = None, game_server: Any = None) -> Tuple[bool, str]:
    """Prepare and validate a save before publishing its runtime bindings."""
    target_db_path = os.path.abspath(target_db_path)
    import account_db
    import gm_reader
    import generator as _gen
    with _DATABASE_SWITCH_LOCK:
        if not os.path.isfile(target_db_path):
            return False, f"数据库文件不存在: {target_db_path}"
        new_db = None
        previous = None
        try:
            owner = _inspect_save_owner(target_db_path)
            # init_schema can write even during an ordinary mount.
            account_db.AccountDB.backup_save_file(target_db_path)
            new_db = account_db.AccountDB(target_db_path)
            if _gen.resolve_save_owner_uid(new_db) != owner:
                raise ValueError('存档初始化后归属发生变化')
            manager = get_slot_manager()
            reader = gm_reader.GMReader(db_path=target_db_path)
            previous = dict(path=account_db.DEFAULT_DB, db=account_db._db,
                reader_path=gm_reader.DEFAULT_DB, reader=gm_reader._reader_instance,
                uid=_gen.DEFAULT_UID, active=manager.get_active_db(),
                core_db=core.db if core is not None else None,
                game_db=game_server.db if game_server is not None else None)
            account_db.DEFAULT_DB = target_db_path
            account_db._db = new_db
            if core is not None:
                core.db = new_db
            if game_server is not None:
                game_server.db = new_db
            _gen.DEFAULT_UID = owner
            with gm_reader._reader_lock:
                gm_reader.DEFAULT_DB = target_db_path
                gm_reader._reader_instance = reader
            manager.set_active_db(target_db_path)
            return True, "数据库切换成功"
        except Exception as exc:
            if previous is not None:
                account_db.DEFAULT_DB = previous['path']
                account_db._db = previous['db']
                _gen.DEFAULT_UID = previous['uid']
                with gm_reader._reader_lock:
                    gm_reader.DEFAULT_DB = previous['reader_path']
                    gm_reader._reader_instance = previous['reader']
                # Complete all restoration steps even if one injected setter fails.
                for restore in (
                    lambda: setattr(core, 'db', previous['core_db']) if core is not None else None,
                    lambda: setattr(game_server, 'db', previous['game_db']) if game_server is not None else None,
                    lambda: manager.set_active_db(previous['active']),
                ):
                    try:
                        restore()
                    except Exception:
                        pass
            if new_db is not None:
                new_db.close()
            return False, f"切换数据库异常: {exc}"


class SaveSlotManager:
    """管理 32 个区服槽位与游戏内选服状态机"""

    def __init__(self, server_dir: Optional[str] = None, base_port: int = BASE_GW_PORT, game_port: int = DEFAULT_GAME_PORT):
        self.server_dir = Path(server_dir or os.path.dirname(os.path.abspath(__file__)))
        # 存档与主数据库保持同级目录
        self.trash_dir = self.server_dir / "Trash"
        self.registry_file = self.server_dir / "data" / "slot_registry.json"
        self.base_port = base_port
        self.game_port = game_port

        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._refresh_condition = threading.Condition()
        self._refresh_clients: Dict[str, Dict[str, Any]] = {}
        # 会话状态：session_key (通常为 client_ip) -> {"state": str, "timestamp": float}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._host_ip = ""
        # 当前全局/主活动数据库路径
        self._active_db_path: Optional[str] = None
        self._panel_requests: Dict[str, int] = {}

    def _load_registry(self) -> Dict[int, str]:
        """从 slot_registry.json 读取持久化槽位映射 {slot_id: filename}"""
        if not self.registry_file.is_file():
            return {}
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            reg: Dict[int, str] = {}
            for k, v in data.items():
                if str(k).isdigit() and isinstance(v, str):
                    s_id = int(k)
                    if SLOT_INDEX_SAVES_START <= s_id <= TOTAL_SLOTS:
                        reg[s_id] = Path(v).name
            return reg
        except Exception:
            return {}

    def _save_registry(self, reg: Dict[int, str], *, strict=False) -> None:
        """原子写入 slot_registry.json"""
        try:
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.registry_file.with_suffix(".tmp")
            data = {str(k): reg[k] for k in sorted(reg.keys())}
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.registry_file)
        except Exception:
            if strict:
                raise

    def sync_slot_registry(self) -> Dict[int, str]:
        """
        同步固定槽位注册表（Fixed Slot Registry）：
        1. 读取 slot_registry.json。
        2. 清理 registry 中文件已不存在或损坏的无效槽位（使该槽位恢复为空闲）。
        3. 确保 Slot 3 永久绑定主库 account.db（如果存在）。
        4. 扫描目录下的所有有效真实存档 (*.db)。
        5. 对于尚未登记的新存档，按空闲槽位升序（固定队列/数组机制）依次分配槽位号（Slot 3..32），并永久固化入 registry。
        6. 已分配槽位的存档位置绝对不变，不重排、不漂移，确保客户端 PlayerPrefs("RegionServerId") 稳定对齐。
        """
        with self._lock:
            reg = self._load_registry()

            # 1. 清理已在磁盘上消失或损坏的无效文件
            for slot_id, fname in list(reg.items()):
                p = self.server_dir / fname
                if not p.is_file() or not is_valid_save_db(str(p)):
                    del reg[slot_id]

            # 2. 确保主库 account.db 始终固定在 Slot 3 (SLOT_INDEX_SAVES_START)
            acc_db = self.server_dir / "account.db"
            if acc_db.is_file() and is_valid_save_db(str(acc_db)):
                # 如果 account.db 之前被记录在其他槽位，移除旧位置
                for sid, fn in list(reg.items()):
                    if fn.lower() == "account.db":
                        del reg[sid]
                reg[SLOT_INDEX_SAVES_START] = "account.db"

            # 3. 扫描磁盘上所有有效存档
            disk_saves: List[str] = []
            for p in self.server_dir.glob("*.db"):
                name = p.name.lower()
                if name.startswith(("_", ".")) or name.startswith("test_"):
                    continue
                if "_bak_" in name or name.endswith(".bak") or "_backup_" in name:
                    continue
                if not is_valid_save_db(str(p)):
                    continue
                disk_saves.append(p.name)

            # 4. 为未登记的新存档分配空闲槽位 (固定数组机制，分配在首个未占用的 slot_id)
            occupied_slots = set(reg.keys())
            registered_filenames = set(reg.values())

            for fname in sorted(disk_saves):
                if fname in registered_filenames:
                    continue
                # 寻找首个空闲的槽位 (3..32)
                for cand_slot in range(SLOT_INDEX_SAVES_START, TOTAL_SLOTS + 1):
                    if cand_slot not in occupied_slots:
                        reg[cand_slot] = fname
                        occupied_slots.add(cand_slot)
                        registered_filenames.add(fname)
                        break

            # 5. 持久化并返回
            self._save_registry(reg)
            return reg

    def register_new_save(self, db_filename: str) -> Optional[int]:
        """为新生成的存档主动分配并固定一个可用槽位编号 (Slot 3..32)"""
        with self._lock:
            fname = Path(db_filename).name
            reg = self.sync_slot_registry()
            for sid, fn in reg.items():
                if fn == fname:
                    return sid
            occupied = set(reg.keys())
            for cand_slot in range(SLOT_INDEX_SAVES_START, TOTAL_SLOTS + 1):
                if cand_slot not in occupied:
                    reg[cand_slot] = fname
                    self._save_registry(reg)
                    return cand_slot
            return None

    def set_active_db(self, path: Optional[str]) -> None:
        with self._lock:
            if path:
                self._active_db_path = os.path.abspath(path)
            else:
                self._active_db_path = None

    def get_active_db(self) -> str:
        with self._lock:
            if self._active_db_path and os.path.isfile(self._active_db_path):
                return self._active_db_path
            default_acc = self.server_dir / "account.db"
            if default_acc.is_file():
                return str(default_acc.resolve())
            return ""

    def hold_panel_save(self, path):
        with self._lock:
            key = os.path.normcase(str(path))
            self._panel_requests[key] = self._panel_requests.get(key, 0) + 1

    def release_panel_save(self, path):
        with self._lock:
            key = os.path.normcase(str(path))
            self._panel_requests[key] -= 1
            if not self._panel_requests[key]:
                del self._panel_requests[key]

    def configure_host(self, host_ip: str) -> None:
        """Use the explicit listener host to identify this machine's two paths."""
        # Readers use this immutable scalar without the registry lock too.
        # Reconfiguring the same host must not queue behind a slow deletion.
        self._host_ip = str(host_ip or "").strip()

    def _session_key(self, client_ip: str) -> str:
        raw = str(client_ip or "default").strip()
        try:
            address = ipaddress.ip_address(raw)
            address = getattr(address, "ipv4_mapped", None) or address
            host = ipaddress.ip_address(self._host_ip) if self._host_ip else None
            host = (getattr(host, "ipv4_mapped", None) or host) if host else None
            if address.is_loopback or (host and address == host):
                return "__local_client__"
            return str(address)
        except ValueError:
            return raw

    def get_session_state(self, client_ip: str) -> str:
        """获取指定客户端 IP 的当前状态机状态，带 60s 自动超时复位"""
        key = self._session_key(client_ip)
        with self._lock:
            sess = self._sessions.get(key)
            if not sess:
                return STATE_NORMAL
            if sess.get("state") in (STATE_DELETE_MODE, STATE_CREATE_MODE):
                elapsed = time.time() - sess.get("timestamp", 0)
                if elapsed > STATE_TIMEOUT_SECONDS:
                    sess["state"] = STATE_NORMAL
                    sess["timestamp"] = time.time()
                    return STATE_NORMAL
                return sess['state']
            return STATE_NORMAL

    def _refresh_client(self, key: str) -> Dict[str, Any]:
        """Called with the refresh condition held; retain only recent clients."""
        now = time.monotonic()
        expired = [k for k, value in self._refresh_clients.items()
                   if not value['pending'] and now - value['touched'] > 60.0]
        for old_key in expired:
            self._refresh_clients.pop(old_key)
        entry = self._refresh_clients.setdefault(key, {'pending': 0, 'revision': 0})
        entry['touched'] = now
        return entry

    @contextmanager
    def gateway_action(self, client_ip: str):
        """Wake pending SDK refreshes after a gateway action, including failures.

        This condition deliberately does not share the registry lock: deletion
        may hold that lock while making its backup or calling the recycle bin.
        """
        key = self._session_key(client_ip)
        with self._refresh_condition:
            entry = self._refresh_client(key)
            entry['pending'] += 1
            self._refresh_condition.notify_all()
        try:
            yield
        finally:
            with self._refresh_condition:
                entry['pending'] -= 1
                entry['revision'] += 1
                entry['touched'] = time.monotonic()
                self._refresh_condition.notify_all()

    def wait_for_gateway_refresh(self, client_ip: str, *,
                                 window: Optional[float] = None,
                                 timeout: float = REGION_REFRESH_TIMEOUT_SECONDS) -> str:
        """Observe a following gateway action; never run one from a list GET.

        Returns idle/completed/timeout. Local and initial queries use the short
        window; a rapid remote follow-up after an idle reply allows WAN latency.
        Once a gateway is observed, wait for all actions
        for that client, up to the total deadline. No retry or background poll.
        """
        key = self._session_key(client_ip)
        started = time.monotonic()
        deadline = started + max(0.0, timeout)
        with self._refresh_condition:
            entry = self._refresh_client(key)
            if window is None:
                last_reply = entry.get('last_list_reply')
                followup = (key != '__local_client__' and last_reply is not None
                            and entry.get('last_list_result') == 'idle'
                            and 0.0 <= started - last_reply <= REGION_REFRESH_FOLLOWUP_GAP_SECONDS)
                window = (REGION_REFRESH_FOLLOWUP_WINDOW_SECONDS if followup
                          else REGION_REFRESH_WINDOW_SECONDS)
            observe_until = min(deadline, started + max(0.0, window))
            def finish(result):
                entry['last_list_reply'] = time.monotonic()
                entry['last_list_result'] = result
                return result
            revision = entry['revision']
            while True:
                now = time.monotonic()
                if not entry['pending']:
                    if entry['revision'] != revision:
                        return finish('completed')
                    if now >= observe_until:
                        return finish('idle')
                    remaining = observe_until - now
                else:
                    if now >= deadline:
                        return finish('timeout')
                    remaining = deadline - now
                self._refresh_condition.wait(remaining)

    def toggle_delete_mode(self, client_ip: str) -> Tuple[str, str]:
        """切换删除模式 (NORMAL <-> DELETE_MODE)，返回 (新状态, 提示消息)"""
        key = self._session_key(client_ip)
        with self._lock:
            current = self.get_session_state(key)
            if current == STATE_CREATE_MODE:
                return current, '请先返回存档列表，再进入删除模式。'
            if current == STATE_NORMAL:
                self._sessions[key] = {
                    "state": STATE_DELETE_MODE,
                    "timestamp": time.time(),
                }
                msg = (
                    "⚠️ 已进入【删除模式】！\n"
                    "模式有效期为60秒；重新获取区服列表后，可选择要删除的存档。\n"
                    "若要取消，请在列表中点击【退出删除模式】。"
                )
                return STATE_DELETE_MODE, msg
            else:
                self._sessions[key] = {
                    "state": STATE_NORMAL,
                    "timestamp": time.time(),
                }
                msg = "已安全退出删除模式。重新获取区服列表后可显示普通菜单。"
                return STATE_NORMAL, msg

    def reset_session(self, client_ip: str) -> None:
        key = self._session_key(client_ip)
        with self._lock:
            if key in self._sessions:
                self._sessions[key] = {
                    "state": STATE_NORMAL,
                    "timestamp": time.time(),
                }

    def toggle_create_mode(self, client_ip: str) -> Tuple[str, str]:
        key = self._session_key(client_ip)
        with self._lock:
            state = self.get_session_state(key)
            if state == STATE_DELETE_MODE:
                return state, '当前处于删除模式，请先退出删除模式。'
            if state == STATE_CREATE_MODE:
                self.reset_session(key)
                return STATE_NORMAL, '已返回存档列表。请重新获取区服列表。'
            if sum(not item.is_action_item for item in self.get_slot_mapping().values()) >= MAX_SAVE_SLOTS:
                return STATE_NORMAL, '存档位置已满（最多30个），请先整理存档。'
            self._sessions[key] = dict(state=STATE_CREATE_MODE, timestamp=time.time())
            return STATE_CREATE_MODE, ('已进入【新建存档】。\n重新获取区服列表后选择预设方案。\n'
                                       '60秒内有效；可点击【返回存档列表】取消。')

    def _create_menu_mapping(self):
        from save_presets import PRESET_SLOTS
        mapping = {1: SaveSlotInfo(1, slot_id_to_port(1, self.base_port, self.game_port), None,
                    '↩️【返回存档列表】', 10000, '返回', 1, time.time(), False, True, 'create_exit')}
        for slot_id, preset in PRESET_SLOTS.items():
            mapping[slot_id] = SaveSlotInfo(slot_id, slot_id_to_port(slot_id, self.base_port, self.game_port),
                None, preset.label, 10000 + slot_id, preset.title, preset.options.level,
                time.time(), False, True, 'create_preset')
        return mapping

    def _next_preset_name(self):
        numbers = [int(m[1]) for path in self.server_dir.iterdir()
                   if path.suffix.lower() == '.db' and (m := re.fullmatch(r'新存档_([0-9]+)', path.stem))]
        number = max(numbers, default=0) + 1
        while True:
            name = f'新存档_{number:02d}'
            path = self.server_dir / (name + '.db')
            if not (path.exists() or path.is_symlink()
                    or any(path.with_name(path.name + ext).exists() for ext in ('-wal','-shm'))):
                return name
            number += 1

    def create_preset_save(self, slot_id: int, client_ip: str) -> Tuple[bool, str]:
        """Create a backed-up cold save; never mount it into the running game."""
        from save_presets import PRESET_SLOTS
        import save_generator
        key = self._session_key(client_ip)
        with self._lock:
            if self.get_session_state(key) != STATE_CREATE_MODE:
                return False, '新建模式已结束，请重新选择【新建存档】。'
            preset = PRESET_SLOTS.get(slot_id)
            if preset is None:
                return False, '无效的预设方案，请重新获取区服列表。'
            path = None
            try:
                if self.server_dir.resolve() != Path(save_generator.SLOTS).resolve():
                    raise ValueError('存档目录配置不一致')
                if self.registry_file.is_file():
                    backups = self.server_dir / 'archive' / 'slot_registry'
                    backups.mkdir(parents=True, exist_ok=True)
                    with self.registry_file.open('rb') as src, (backups / f'before_create_{time.time_ns()}.json').open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                if len(self.sync_slot_registry()) >= MAX_SAVE_SLOTS:
                    self.reset_session(key)
                    return False, '存档位置已满（最多30个），请先整理存档。'
                name = self._next_preset_name()
                path = self.server_dir / (name + '.db')
                report = save_generator.create_save(name, preset.options)
                assigned = self.register_new_save(path.name)
                if assigned is None or self._load_registry().get(assigned) != path.name:
                    raise RuntimeError('存档已生成，槽位登记未完成')
                self.reset_session(key)
                logger.info('预设存档创建完成 preset=%s name=%s slot=%s backup=%s',
                            preset.key, name, assigned, report.get('backup'))
                return True, (f'已创建【{name}】\n方案：{preset.title}，{preset.options.level}级。\n'
                               '重新获取区服列表后，选择该存档进入游戏。')
            except Exception as exc:
                logger.exception('预设存档创建失败 preset=%s', preset.key)
                if path is not None and path.exists():
                    self.reset_session(key)
                    return False, '存档文件已保留。请重新获取区服列表后检查。'
                return False, '创建未完成，请稍后重新选择方案；若仍失败，请检查服务端日志。'

    def scan_save_files(self) -> List[Tuple[str, float]]:
        """基于注册表扫描并返回当前已分配槽位的可用存档列表 [(path, mtime), ...]"""
        reg = self.sync_slot_registry()
        res: List[Tuple[str, float]] = []
        for sid in sorted(reg.keys()):
            fname = reg[sid]
            p = self.server_dir / fname
            if p.is_file():
                try:
                    res.append((str(p.resolve()), p.stat().st_mtime))
                except Exception:
                    pass
        return res

    def get_slot_mapping(self) -> Dict[int, SaveSlotInfo]:
        """构建当前的 32 槽位信息字典 (Slot 1..32)，位置完全由注册表固定，绝不动态重排"""
        mapping: Dict[int, SaveSlotInfo] = {}
        active_path = self.get_active_db()

        # Slot 1: 新建存档
        mapping[SLOT_INDEX_NEW] = SaveSlotInfo(
            slot_id=SLOT_INDEX_NEW,
            port=slot_id_to_port(SLOT_INDEX_NEW, self.base_port, self.game_port),
            db_path=None,
            save_name="➕【新建存档】",
            uid=0,
            nick_name="新起点",
            user_level=1,
            mtime=time.time(),
            is_active=False,
            is_action_item=True,
            action_type="new",
        )

        # Slot 2: 删除存档 / 退出删除跳板
        mapping[SLOT_INDEX_DELETE_TOGGLE] = SaveSlotInfo(
            slot_id=SLOT_INDEX_DELETE_TOGGLE,
            port=slot_id_to_port(SLOT_INDEX_DELETE_TOGGLE, self.base_port, self.game_port),
            db_path=None,
            save_name="🗑️【删除存档】",
            uid=0,
            nick_name="功能",
            user_level=99,
            mtime=time.time(),
            is_active=False,
            is_action_item=True,
            action_type="delete_toggle",
        )

        # Slot 3..32: 真实存档槽位（严格按固定槽位注册表映射）
        reg = self.sync_slot_registry()
        for slot_num in range(SLOT_INDEX_SAVES_START, TOTAL_SLOTS + 1):
            if slot_num not in reg:
                continue
            fname = reg[slot_num]
            f_path_obj = self.server_dir / fname
            if not f_path_obj.is_file():
                continue
            f_path = str(f_path_obj.resolve())
            stem = f_path_obj.stem
            is_act = False
            if active_path and os.path.exists(f_path) and os.path.exists(active_path):
                try:
                    is_act = os.path.samefile(f_path, active_path)
                except Exception:
                    is_act = (f_path == active_path)

            uid, nick, lv = extract_save_metadata(f_path)
            try:
                mtime = f_path_obj.stat().st_mtime
            except Exception:
                mtime = time.time()

            mapping[slot_num] = SaveSlotInfo(
                slot_id=slot_num,
                port=slot_id_to_port(slot_num, self.base_port, self.game_port),
                db_path=f_path,
                save_name=stem,
                uid=uid,
                nick_name=nick,
                user_level=lv,
                mtime=mtime,
                is_active=is_act,
                is_action_item=False,
                action_type="save",
            )

        return mapping

    def delete_slot_save(self, slot_id: int, client_ip: str) -> Tuple[bool, str]:
        """仅在删除模式中备份并回收非活跃存档，完成后释放其固定槽位。"""
        key = self._session_key(client_ip)
        with _DATABASE_SWITCH_LOCK, self._lock:
            if self.get_session_state(key) != STATE_DELETE_MODE:
                return False, "删除模式未开启或已超时，请重新选择删除入口。"
            mapping = self.get_slot_mapping()
            slot_info = mapping.get(slot_id)
            if not slot_info or not slot_info.db_path or slot_info.is_action_item:
                return False, "❌ 所选槽位无效或非真实存档！"
            try:
                destination = self.delete_save(Path(slot_info.db_path), slot_info.uid)
            except Exception as e:
                return False, f"❌ 删除存档失败: {e}"
            self.reset_session(key)
            return True, f"✅ 存档【{slot_info.save_name}】已备份并移入{destination}！\n重新获取区服列表后可查看剩余存档。"

    def delete_save(self, path: Path, uid: int) -> str:
        """Back up and recycle a save under the same locks used by the gateway."""
        from account_db import AccountDB
        from server_net import run_while_save_offline
        from uuid import uuid4

        with _DATABASE_SWITCH_LOCK, self._lock:
            if path.is_symlink() or not path.is_file():
                raise ValueError('所选存档不存在，请刷新存档列表')
            target = path.resolve()
            if target.parent != self.server_dir.resolve() or target.suffix.lower() != '.db':
                raise ValueError('所选存档不在允许的存档目录')
            if target.name.lower() == 'account.db':
                raise ValueError('默认主存档不可删除')
            active = self.get_active_db()
            if active and os.path.samefile(target, active):
                raise ValueError('存档当前已挂载，请在游戏选区切换到其他存档后再删除')
            if self._panel_requests.get(os.path.normcase(str(target))):
                raise ValueError('控制面板正在读取或修改该存档，请稍后重试')
            if not is_valid_save_db(str(target)) or extract_save_metadata(str(target))[0] != uid:
                raise ValueError('存档已变更或不可用，请刷新存档列表')

            def recycle():
                AccountDB.backup_save_file(target)
                paths = [target.with_name(target.name + ext) for ext in ('-wal', '-shm', '-journal')]
                paths = [p for p in paths if p.exists() or p.is_symlink()]
                if any(p.is_symlink() or not p.is_file() for p in paths):
                    raise ValueError('存档附属文件类型无效')
                paths.append(target)
                registry = self._load_registry()
                remaining = {slot: name for slot, name in registry.items() if name != target.name}
                if remaining != registry:
                    backup = self.server_dir / 'archive' / 'slot_registry'
                    backup.mkdir(parents=True, exist_ok=True)
                    with self.registry_file.open('rb') as src, (backup / f'{uuid4().hex}.json').open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                folder = self.trash_dir / uuid4().hex
                if not folder.resolve().is_relative_to(self.server_dir.resolve()):
                    raise ValueError('存档回收目录不在服务器目录内')
                folder.mkdir()
                moved = []
                try:
                    for source in paths:
                        source.rename(folder / source.name)
                        moved.append(source)
                    if os.name == 'nt':
                        _recycle_save_files([folder])
                except OSError:
                    for source in reversed(moved):
                        staged = folder / source.name
                        if staged.exists():
                            staged.rename(source)
                    raise
                destination = 'Windows 回收站' if os.name == 'nt' else 'Trash 归档目录'
                if remaining != registry:
                    try:
                        self._save_registry(remaining, strict=True)
                    except OSError:
                        logger.exception('存档已回收，槽位注册表将在下次选区扫描时同步')
                return destination

            return run_while_save_offline(target, uid, recycle, busy_message='请先退出该存档的游戏账号，再删除存档')

    def build_zones_response(self, client_ip: str, host_ip: str, game_port: int = DEFAULT_GAME_PORT) -> List[Dict[str, Any]]:
        """为 /gateway/get?action=server 生成自适应的区服列表下行数据"""
        self.game_port = game_port
        self.configure_host(host_ip)
        state = self.get_session_state(client_ip)
        mapping = self._create_menu_mapping() if state == STATE_CREATE_MODE else self.get_slot_mapping()
        zones: List[Dict[str, Any]] = []

        is_delete_mode = (state == STATE_DELETE_MODE)

        for slot_num in range(1, TOTAL_SLOTS + 1):
            item = mapping.get(slot_num)
            if not item:
                continue

            s_id = str(slot_num)
            gw_p = item.port

            if item.action_type in ('create_exit', 'create_preset'):
                name = item.save_name
                maint, reason = False, ''
                new_flag = 1 if item.action_type == 'create_exit' else 0
                user_list = [{'uid': str(item.uid), 'currentLevel': item.user_level,
                    'nickName': item.nick_name, 'lastLoginTime': time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(item.mtime)),
                    'mixId': str(item.uid)}]
            elif item.action_type == "new":
                # Slot 1: 新建存档
                if is_delete_mode:
                    continue
                name = "➕【新建存档】"
                maint = False
                reason = ""
                new_flag = 0  # 避免首次进入游戏默认选中新建项
                user_list = [{
                    "uid": "10000",
                    "currentLevel": 1,
                    "nickName": "新起点",
                    "lastLoginTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.mtime)),
                    "mixId": "10000",
                }]

            elif item.action_type == "delete_toggle":
                # Slot 2: 模式切换跳板
                if is_delete_mode:
                    name = "↩️【退出删除模式】(点击安全返回)"
                    new_flag = 1
                else:
                    name = "🗑️【删除存档】(点击进入删除模式)"
                    new_flag = 0
                maint = False
                reason = ""
                user_list = [{
                    "uid": "10001",
                    "currentLevel": 99,
                    "nickName": "功能",
                    "lastLoginTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.mtime)),
                    "mixId": "10001",
                }]

            else:
                # Slot 3..32: 真实存档
                save_display_name = item.save_name
                if is_delete_mode:
                    name = f"❌【确认删除】{save_display_name}"
                    new_flag = 0
                    maint = False
                    reason = ""
                else:
                    if item.is_active:
                        name = f"【当前】{save_display_name}"
                        new_flag = 1
                    else:
                        name = f"【存档{item.slot_id - 2:02d}】{save_display_name}"
                        new_flag = 0
                    maint = False
                    reason = ""

                user_list = [{
                    "uid": str(item.uid),
                    "currentLevel": item.user_level,
                    "nickName": item.nick_name,
                    "lastLoginTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.mtime)),
                    "mixId": str(item.uid),
                }]

            zones.append({
                "serverId": s_id,
                "serverName": name,
                "env": "prod",
                "ip": host_ip,
                "port": gw_p,
                "newServerFlag": new_flag,
                "maintain": maint,
                "maintainReason": reason,
                "config": [
                    {"key": "OFFICIAL_URL", "value": "https://skzy.ys4fun.com/m/"},
                    {"key": "USE_SDK_HOT_FIX", "value": "true"},
                    {"key": "REST_URL", "value": "https://prod-api-activity.ys4fun.com/skzy-activity"},
                    {"key": "PC_FEEDBACK", "value": "https://open.ys4fun.com/web/agreement.html?tab=support"},
                ],
                "gameUserInfoList": user_list,
            })

        return zones


# 全局单例管理器
_SLOT_MANAGER: Optional[SaveSlotManager] = None


def get_slot_manager(server_dir: Optional[str] = None, base_port: int = BASE_GW_PORT, game_port: int = DEFAULT_GAME_PORT) -> SaveSlotManager:
    global _SLOT_MANAGER
    if _SLOT_MANAGER is None:
        _SLOT_MANAGER = SaveSlotManager(server_dir, base_port=base_port, game_port=game_port)
    return _SLOT_MANAGER
