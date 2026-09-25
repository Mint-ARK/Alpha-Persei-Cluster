# -*- coding: utf-8 -*-
"""为 AI 对话构建一次性内部上下文。

本模块只返回内存中的 system prompt 片段；不会写入客户端历史或 SQLite。
模板与选择逻辑分离，便于后续调整亲和表达而不污染用户可见会话。
"""

from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import threading


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FRAGMENTS_PATH = os.path.join(BASE_DIR, "data", "ai_prompt_fragments.json")


def _safe_display_text(value, fallback: str, max_length: int = 32) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()
    return cleaned[:max_length] or fallback


class AIContextBuilder:
    def __init__(self, fragments_path: str = DEFAULT_FRAGMENTS_PATH):
        self.fragments_path = fragments_path
        self._fragments = None
        self._lock = threading.Lock()

    def _load(self) -> dict:
        with self._lock:
            if self._fragments is None:
                try:
                    with open(self.fragments_path, "r", encoding="utf-8") as stream:
                        data = json.load(stream)
                    self._fragments = data if isinstance(data, dict) else {}
                except (OSError, json.JSONDecodeError):
                    self._fragments = {}
            return self._fragments

    @staticmethod
    def _query_one(db, sql: str, args=()) -> dict:
        if db is None or not hasattr(db, "query"):
            return {}
        try:
            rows = db.query(sql, args)
            return rows[0] if rows else {}
        except Exception:
            return {}

    @staticmethod
    def _hero_ids(character: dict) -> list[int]:
        raw = character.get("hero_ids", "") if isinstance(character, dict) else ""
        result = []
        for token in str(raw).split(","):
            try:
                value = int(token.strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                result.append(value)
        return result

    def _get_affinity_tier(self, uid: int, character: dict, db) -> str:
        hero_ids = self._hero_ids(character)
        if not hero_ids:
            return "unknown"
        placeholders = ",".join("?" for _ in hero_ids)
        row = self._query_one(
            db,
            f"SELECT MAX(COALESCE(trust_level, 0)) AS trust_level FROM hero WHERE uid=? AND id IN ({placeholders})",
            tuple([int(uid)] + hero_ids),
        )
        if row.get("trust_level") is None:
            return "unknown"
        try:
            level = int(row.get("trust_level"))
        except (TypeError, ValueError):
            return "unknown"
        if level >= 5:
            return "bonded"
        if level >= 2:
            return "trusted"
        if level >= 0:
            return "acquainted"
        return "unknown"

    @staticmethod
    def _time_key(now: _datetime.datetime) -> str:
        hour = now.hour
        if 5 <= hour < 12:
            return "morning"
        if 12 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 23:
            return "evening"
        return "night"

    def build(self, uid: int, character: dict, db=None, history=None, now=None) -> dict:
        fragments = self._load()
        now = now or _datetime.datetime.now()
        applied = []
        lines = []

        header = fragments.get("context_header")
        if isinstance(header, str) and header.strip():
            lines.append(header.strip())

        user_row = self._query_one(db, "SELECT nick FROM users WHERE uid=?", (int(uid),))
        nickname = _safe_display_text(user_row.get("nick"), "管理员")
        player_template = fragments.get("player")
        if isinstance(player_template, str):
            # JSON 编码昵称，避免用户可编辑字段伪装成新的系统指令行。
            lines.append(player_template.format(nickname=json.dumps(nickname, ensure_ascii=False)))
            applied.append("player")

        time_key = self._time_key(now)
        time_fragment = fragments.get("time_of_day", {}).get(time_key)
        if isinstance(time_fragment, str) and time_fragment.strip():
            lines.append(time_fragment.strip())
            applied.append(f"time_of_day.{time_key}")

        affinity_tier = self._get_affinity_tier(uid, character, db)
        affinity_fragment = fragments.get("affinity", {}).get(affinity_tier)
        if isinstance(affinity_fragment, str) and affinity_fragment.strip():
            lines.append(affinity_fragment.strip())
            applied.append(f"affinity.{affinity_tier}")

        history_rows = [item for item in (history or []) if isinstance(item, dict)]
        timestamps = []
        for item in history_rows:
            try:
                timestamps.append(int(item.get("timestamp", 0)))
            except (TypeError, ValueError):
                continue
        if timestamps:
            try:
                last_at = _datetime.datetime.fromtimestamp(max(timestamps))
            except (OSError, OverflowError, ValueError):
                last_at = None
            if last_at is not None:
                absence_key = "long" if (now - last_at).total_seconds() >= 7 * 86400 else "recent"
                absence_fragment = fragments.get("absence", {}).get(absence_key)
                if isinstance(absence_fragment, str) and absence_fragment.strip():
                    lines.append(absence_fragment.strip())
                    applied.append(f"absence.{absence_key}")

        birthday_row = self._query_one(
            db,
            "SELECT birth_month, birth_day FROM game_user WHERE uid=?",
            (int(uid),),
        )
        if (
            int(birthday_row.get("birth_month") or 0) == now.month
            and int(birthday_row.get("birth_day") or 0) == now.day
        ):
            birthday_fragment = fragments.get("birthday")
            if isinstance(birthday_fragment, str) and birthday_fragment.strip():
                lines.append(birthday_fragment.strip())
                applied.append("birthday")

        return {
            "text": "\n".join(lines).strip(),
            "version": int(fragments.get("version", 0) or 0),
            "applied": applied,
        }


_builder = None
_builder_lock = threading.Lock()


def get_context_builder() -> AIContextBuilder:
    global _builder
    with _builder_lock:
        if _builder is None:
            _builder = AIContextBuilder()
        return _builder
