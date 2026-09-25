# -*- coding: utf-8 -*-
"""AI 会话历史管理器。

客户端 ``chat/<uid>/friend/<char_id>.txt`` 是用户可见会话的首选事实源；
SQLite 仅作为面板消息、缺口补全和离线兜底。该模块严格只读客户端目录，
并且只接受 user/assistant 文本消息，任何 system/tool/internal 内容都不会进入历史。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Iterable, Optional


_ALLOWED_ROLES = {"user", "assistant"}
_MAX_LINE_CHARS = 65536
_MAX_CLIENT_FILE_BYTES = 32 * 1024 * 1024
_MAX_TIMESTAMP = 4102444800  # 2100-01-01，拒绝异常时间值污染排序与上下文计算。
_EXCLUDED_SYSTEM_PHRASES = {
    "当前角色没有对应的人格提示词",
}


def _default_chat_root() -> Path:
    configured = os.environ.get("AETHERGAZER_CHAT_ROOT", "").strip()
    if configured:
        return Path(configured)
    user_profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(user_profile) / "AppData" / "LocalLow" / "yongshi" / "AetherGazer" / "chat"


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _is_true(value) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}


def _synthetic_id(uid: int, char_id: int, role: str, content: str, timestamp: int) -> int:
    raw = f"{uid}|{char_id}|{role}|{timestamp}|{content}".encode("utf-8", errors="replace")
    # 负数可明确区分缺少原始 msgID 的 SQLite 记录，同时仍满足前端 number 类型。
    return -int.from_bytes(hashlib.blake2b(raw, digest_size=6).digest(), "big")


class AIHistoryManager:
    """读取、净化并合并客户端与 SQLite 会话历史。"""

    def __init__(self, chat_root: Optional[os.PathLike] = None):
        self.chat_root = Path(chat_root) if chat_root is not None else _default_chat_root()

    def _friend_dir(self, uid: int) -> Path:
        return self.chat_root / str(int(uid)) / "friend"

    def _candidate_files(self, uid: int, char_id: Optional[int]) -> list[Path]:
        friend_dir = self._friend_dir(uid)
        if char_id is not None:
            return [friend_dir / f"{int(char_id)}.txt"]
        try:
            return sorted(
                (path for path in friend_dir.glob("*.txt") if path.stem.isdigit()),
                key=lambda path: int(path.stem),
            )
        except OSError:
            return []

    def read_client_history(self, uid: int, char_id: Optional[int] = None) -> dict:
        """只读客户端 JSONL；异常行、撤回消息和非文本消息会被跳过。"""
        uid_int = int(uid)
        requested_char = int(char_id) if char_id is not None else None
        messages = []
        warnings = []
        files_seen = 0

        for path in self._candidate_files(uid_int, requested_char):
            try:
                if not path.is_file():
                    continue
                files_seen += 1
                if path.stat().st_size > _MAX_CLIENT_FILE_BYTES:
                    warnings.append(f"{path.name}: 文件超过读取上限")
                    continue
                file_char_id = _safe_int(path.stem)
                if file_char_id <= 0:
                    continue
                with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
                    for line_number, raw_line in enumerate(stream, 1):
                        line = raw_line.strip()
                        if not line:
                            continue
                        if len(line) > _MAX_LINE_CHARS:
                            warnings.append(f"{path.name}:{line_number}: 行过长")
                            continue
                        try:
                            record = json.loads(line)
                        except (json.JSONDecodeError, TypeError):
                            warnings.append(f"{path.name}:{line_number}: JSON 无效")
                            continue
                        if not isinstance(record, dict):
                            continue
                        if _is_true(record.get("recall", False)):
                            continue
                        if _safe_int(record.get("contentType"), 1) != 1:
                            continue

                        record_char = _safe_int(record.get("friendID"), file_char_id)
                        if record_char != file_char_id:
                            warnings.append(f"{path.name}:{line_number}: friendID 不匹配")
                            continue
                        sender_id = _safe_int(record.get("senderID"))
                        if sender_id == uid_int:
                            role = "user"
                        elif sender_id == record_char:
                            role = "assistant"
                        else:
                            continue

                        content = record.get("content")
                        timestamp = _safe_int(record.get("timestamp"))
                        if not isinstance(content, str) or not content.strip() or not (0 < timestamp <= _MAX_TIMESTAMP):
                            continue
                        if content.strip() in _EXCLUDED_SYSTEM_PHRASES:
                            continue
                        msg_id = _safe_int(record.get("msgID"))
                        if msg_id <= 0:
                            msg_id = abs(_synthetic_id(uid_int, record_char, role, content, timestamp))
                        messages.append({
                            "id": msg_id,
                            "char_id": record_char,
                            "role": role,
                            "content": content,
                            "timestamp": timestamp,
                            "source": "client",
                        })
            except (OSError, UnicodeError) as exc:
                warnings.append(f"{path.name}: 读取失败 {type(exc).__name__}")

        messages.sort(key=lambda item: (item["timestamp"], item["id"]))
        return {
            "messages": messages,
            "files_seen": files_seen,
            "warnings": warnings,
        }

    def _read_sqlite_history(self, uid: int, char_id: Optional[int], limit: int, db=None) -> list[dict]:
        if db is None:
            return []
        uid_int = int(uid)
        try:
            if hasattr(db, "query"):
                if char_id is None:
                    rows = db.query(
                        "SELECT id, char_id, role, content, timestamp FROM ai_chat_memory "
                        "WHERE uid=? ORDER BY id DESC LIMIT ?",
                        (uid_int, int(limit)),
                    )
                else:
                    rows = db.query(
                        "SELECT id, char_id, role, content, timestamp FROM ai_chat_memory "
                        "WHERE uid=? AND char_id=? ORDER BY id DESC LIMIT ?",
                        (uid_int, int(char_id), int(limit)),
                    )
                return list(reversed(rows))
            if char_id is not None and hasattr(db, "get_ai_chat_history"):
                return db.get_ai_chat_history(uid_int, int(char_id), limit=int(limit))
        except Exception:
            return []
        return []

    def _normalize_sqlite_messages(self, uid: int, rows: Iterable[dict]) -> list[dict]:
        normalized = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role", ""))
            content = row.get("content")
            char_id = _safe_int(row.get("char_id"))
            timestamp = _safe_int(row.get("timestamp"))
            if role not in _ALLOWED_ROLES or not isinstance(content, str) or not content.strip():
                continue
            if content.strip() in _EXCLUDED_SYSTEM_PHRASES:
                continue
            if char_id <= 0 or not (0 < timestamp <= _MAX_TIMESTAMP):
                continue
            msg_id = _safe_int(row.get("id"))
            if msg_id <= 0:
                msg_id = _synthetic_id(int(uid), char_id, role, content, timestamp)
            normalized.append({
                "id": msg_id,
                "char_id": char_id,
                "role": role,
                "content": content,
                "timestamp": timestamp,
                "source": "sqlite",
            })
        normalized.sort(key=lambda item: (item["timestamp"], item["id"]))
        return normalized

    @staticmethod
    def _dedupe_key(message: dict) -> tuple:
        return (
            int(message["char_id"]),
            message["role"],
            int(message["timestamp"]),
            message["content"],
        )

    def get_history(
        self,
        uid: int,
        char_id: Optional[int] = None,
        limit: int = 50,
        *,
        db=None,
        sqlite_messages: Optional[Iterable[dict]] = None,
    ) -> dict:
        """返回按时间正序排列的合并历史；客户端重复项拥有更高优先级。"""
        limit_int = max(1, min(500, int(limit)))
        client_result = self.read_client_history(uid, char_id)
        client_messages = client_result["messages"]
        if sqlite_messages is None:
            sqlite_messages = self._read_sqlite_history(uid, char_id, max(limit_int * 2, 100), db=db)
        sqlite_normalized = self._normalize_sqlite_messages(uid, sqlite_messages)

        # 检查是否配置了截断时间戳 clear_ts（清空历史后，截断时间戳及之前的所有记录彻底屏蔽）
        clear_ts = 0
        if db is not None and hasattr(db, "get_ai_chat_clear_ts"):
            try:
                clear_ts = db.get_ai_chat_clear_ts(uid, char_id)
            except Exception:
                clear_ts = 0

        if clear_ts > 0:
            client_messages = [m for m in client_messages if m.get("timestamp", 0) > clear_ts]
            sqlite_normalized = [m for m in sqlite_normalized if m.get("timestamp", 0) > clear_ts]

        merged_by_key = {self._dedupe_key(message): message for message in sqlite_normalized}
        # 后写入客户端记录，让权威源覆盖同一条 SQLite 镜像。
        for message in client_messages:
            merged_by_key[self._dedupe_key(message)] = message

        merged = sorted(merged_by_key.values(), key=lambda item: (item["timestamp"], item["id"]))
        available_total = len(merged)
        messages = merged[-limit_int:]
        client_keys = {self._dedupe_key(message) for message in client_messages}
        sqlite_only = any(self._dedupe_key(message) not in client_keys for message in sqlite_normalized)
        if client_messages and sqlite_only:
            source = "merged"
        elif client_messages:
            source = "client"
        elif sqlite_normalized:
            source = "sqlite"
        else:
            source = "none"

        # 对外只返回业务消息；source 字段用于诊断，不参与下一轮 LLM 上下文。
        return {
            "messages": messages,
            "total": len(messages),
            "available_total": available_total,
            "source": source,
            "client_available": client_result["files_seen"] > 0,
            "client_count": len(client_messages),
            "sqlite_count": len(sqlite_normalized),
            "warnings": client_result["warnings"],
        }


_manager = None
_manager_lock = threading.Lock()


def get_history_manager() -> AIHistoryManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = AIHistoryManager()
        return _manager
