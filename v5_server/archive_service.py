# -*- coding: utf-8 -*-
"""Archive/heart-link domain service.

The client owns one archive per ontology character, while the hero table owns
individual battle forms.  This service is the single boundary that translates
between those identities and persists first-tier affection, heart-link stories,
super heart-link stories, and anecdotes.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

from event_bus import Events, bus


_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_DIR, "archive_config.json")

MAX_ARCHIVE_EXP = 1000
PREFERRED_GIFT_EXP = 10
NORMAL_GIFT_EXP = 5
NORMAL_PLOT_EXP_GATES = (100, 300, 600, 1000)
HEART_TEXT_EXP_GATES = (0, 100, 300, 600, 1000)
SUPER_PLOT_TRUST_GATES = (2, 5)
ANECDOTE_TRUST_GATE = 5


def _load_json_list(raw):
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


class ArchiveService:
    _instance = None

    def __init__(self, config_file=None):
        path = config_file or _CONFIG_FILE
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        self.archives = {int(k): v for k, v in config.get("archives", {}).items()}
        self.hero_to_archive = {int(k): int(v) for k, v in config.get("hero_to_archive", {}).items()}
        self.anecdotes = {int(k): v for k, v in config.get("anecdotes", {}).items()}
        if len(self.archives) != 63 or len(self.hero_to_archive) != 84:
            raise RuntimeError("archive_config.json is incomplete; regenerate it before starting the server")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_archive_id(self, hero_id_or_archive_id):
        value = int(hero_id_or_archive_id or 0)
        return self.hero_to_archive.get(value, value if value in self.archives else 0)

    def get_archive_config(self, hero_id_or_archive_id):
        archive_id = self.get_archive_id(hero_id_or_archive_id)
        return archive_id, self.archives.get(archive_id)

    def ensure_archive_row(self, db, uid, archive_id):
        archive_id = self.get_archive_id(archive_id)
        if archive_id not in self.archives:
            raise ValueError(f"unknown archive_id: {archive_id}")
        db.execute(
            "INSERT OR IGNORE INTO hero_archive (uid, archive_id) VALUES (?, ?)",
            (uid, archive_id),
        )
        return archive_id

    def owned_archive_ids(self, db, uid):
        rows = db.query("SELECT id FROM hero WHERE uid=? AND unlock=1", (uid,))
        return sorted({self.hero_to_archive[int(row["id"])] for row in rows if int(row["id"]) in self.hero_to_archive})

    def ensure_user_archives(self, db, uid):
        inserted = 0
        for archive_id in self.owned_archive_ids(db, uid):
            cursor = db.execute(
                "INSERT OR IGNORE INTO hero_archive (uid, archive_id) VALUES (?, ?)",
                (uid, archive_id),
            )
            inserted += max(0, int(getattr(cursor, "rowcount", 0) or 0))
        return inserted

    def _require_owned_archive(self, db, uid, archive_id):
        cfg = self.archives.get(archive_id)
        if not cfg:
            raise ValueError(f"unknown archive_id: {archive_id}")
        hero_ids = [int(x) for x in cfg.get("hero_ids") or []]
        placeholders = ",".join("?" for _ in hero_ids)
        rows = db.query(
            f"SELECT 1 FROM hero WHERE uid=? AND unlock=1 AND id IN ({placeholders}) LIMIT 1",
            (uid, *hero_ids),
        )
        if not rows:
            raise ValueError(f"archive {archive_id} has no unlocked hero")
        return cfg

    def add_exp(self, ctx, uid, hero_id_or_archive_id, delta, source, source_hero_id=0):
        archive_id, cfg = self.get_archive_config(hero_id_or_archive_id)
        if not cfg:
            raise ValueError(f"unknown hero/archive id: {hero_id_or_archive_id}")
        self.ensure_archive_row(ctx.db, uid, archive_id)
        rows = ctx.db.query(
            "SELECT exp FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        old_exp = int(rows[0].get("exp") or 0) if rows else 0
        new_exp = min(MAX_ARCHIVE_EXP, max(0, old_exp + int(delta or 0)))
        if new_exp != old_exp:
            ctx.db.execute(
                "UPDATE hero_archive SET exp=? WHERE uid=? AND archive_id=?",
                (new_exp, uid, archive_id),
            )
            bus.emit(
                Events.HERO_ARCHIVE_EXP_CHANGE,
                ctx,
                uid,
                archive_id=archive_id,
                source_hero_id=int(source_hero_id or 0),
                old_exp=old_exp,
                new_exp=new_exp,
                delta=new_exp - old_exp,
                source=source,
            )
        return {"archive_id": archive_id, "old_exp": old_exp, "new_exp": new_exp, "delta": new_exp - old_exp}

    def give_gifts(self, ctx, uid, archive_id, gift_list):
        archive_id, cfg = self.get_archive_config(archive_id)
        if not cfg:
            raise ValueError(f"unknown archive_id: {archive_id}")
        self._require_owned_archive(ctx.db, uid, archive_id)
        gifts = []
        for item in gift_list or []:
            item_id = int(item.get("id") or item.get("item_id") or 0)
            num = int(item.get("num") or item.get("item_num") or 0)
            if item_id <= 0 or num <= 0:
                raise ValueError("gift id and count must be positive")
            gifts.append({"id": item_id, "num": num})
        if not gifts:
            raise ValueError("gift_list is empty")

        self.ensure_archive_row(ctx.db, uid, archive_id)
        rows = ctx.db.query(
            "SELECT exp, gift_list FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        old_exp = int(rows[0].get("exp") or 0)
        if old_exp >= MAX_ARCHIVE_EXP:
            raise ValueError("archive affection is already maxed")

        preferred = {int(x) for x in cfg.get("gift_like_ids") or []}
        requested_exp = sum(
            gift["num"] * (PREFERRED_GIFT_EXP if gift["id"] in preferred else NORMAL_GIFT_EXP)
            for gift in gifts
        )
        from operations import _item_deduct
        for gift in gifts:
            _item_deduct(ctx, uid, gift["id"], gift["num"])

        totals = {}
        for old in _load_json_list(rows[0].get("gift_list")):
            if isinstance(old, dict):
                item_id = int(old.get("id") or 0)
                num = int(old.get("num") or 0)
                if item_id > 0 and num > 0:
                    totals[item_id] = totals.get(item_id, 0) + num
        for gift in gifts:
            totals[gift["id"]] = totals.get(gift["id"], 0) + gift["num"]

        new_exp = min(MAX_ARCHIVE_EXP, old_exp + requested_exp)
        merged_gifts = [{"id": item_id, "num": totals[item_id]} for item_id in sorted(totals)]
        ctx.db.execute(
            "UPDATE hero_archive SET exp=?, gift_list=? WHERE uid=? AND archive_id=?",
            (new_exp, json.dumps(merged_gifts, ensure_ascii=False), uid, archive_id),
        )
        bus.emit(
            Events.HERO_ARCHIVE_EXP_CHANGE,
            ctx,
            uid,
            archive_id=archive_id,
            source_hero_id=0,
            old_exp=old_exp,
            new_exp=new_exp,
            delta=new_exp - old_exp,
            source="gift",
        )
        return {
            "archive_id": archive_id,
            "gifts": gifts,
            "old_exp": old_exp,
            "new_exp": new_exp,
            "exp_added": new_exp - old_exp,
        }

    def read_normal_stories(self, ctx, uid, archive_id, story_ids: Iterable[int]):
        archive_id, cfg = self.get_archive_config(archive_id)
        if not cfg:
            raise ValueError(f"unknown archive_id: {archive_id}")
        self._require_owned_archive(ctx.db, uid, archive_id)
        self.ensure_archive_row(ctx.db, uid, archive_id)
        rows = ctx.db.query(
            "SELECT exp, video_list FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        exp = int(rows[0].get("exp") or 0)
        viewed = {int(x) for x in _load_json_list(rows[0].get("video_list")) if str(x).isdigit()}
        plot_ids = [int(x) for x in cfg.get("plot_ids") or []]
        requested = [int(x) for x in story_ids or []]
        if not requested:
            raise ValueError("video_list is empty")

        new_story_ids = []
        for story_id in sorted(set(requested), key=lambda value: plot_ids.index(value) if value in plot_ids else 10**9):
            if story_id not in plot_ids:
                raise ValueError(f"story {story_id} does not belong to archive {archive_id}")
            index = plot_ids.index(story_id)
            required_exp = NORMAL_PLOT_EXP_GATES[min(index, len(NORMAL_PLOT_EXP_GATES) - 1)]
            if exp < required_exp:
                raise ValueError(f"story {story_id} requires archive exp {required_exp}")
            missing_front = [front for front in plot_ids[:index] if front not in viewed]
            if missing_front:
                raise ValueError(f"previous story is not viewed: {missing_front[0]}")
            if story_id not in viewed:
                viewed.add(story_id)
                new_story_ids.append(story_id)

        ordered = [story_id for story_id in plot_ids if story_id in viewed]
        ctx.db.execute(
            "UPDATE hero_archive SET video_list=? WHERE uid=? AND archive_id=?",
            (json.dumps(ordered), uid, archive_id),
        )
        for story_id in new_story_ids:
            bus.emit(
                Events.STORY_READ,
                ctx,
                uid,
                story_id=story_id,
                archive_id=archive_id,
                story_kind="normal_heart",
                source="hero_read_story",
            )
        return {"archive_id": archive_id, "video_list": requested, "new_story_ids": new_story_ids}

    def read_heart_texts(self, ctx, uid, archive_id, text_indexes: Iterable[int]):
        archive_id, cfg = self.get_archive_config(archive_id)
        if not cfg:
            raise ValueError(f"unknown archive_id: {archive_id}")
        self._require_owned_archive(ctx.db, uid, archive_id)
        self.ensure_archive_row(ctx.db, uid, archive_id)
        rows = ctx.db.query(
            "SELECT exp, text_list FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        exp = int(rows[0].get("exp") or 0)
        viewed = {int(x) for x in _load_json_list(rows[0].get("text_list")) if str(x).isdigit()}
        requested = sorted({int(x) for x in text_indexes or []})
        if not requested:
            raise ValueError("text_list is empty")
        for index in requested:
            if index < 1 or index > len(HEART_TEXT_EXP_GATES):
                raise ValueError(f"invalid heart text index: {index}")
            required_exp = HEART_TEXT_EXP_GATES[index - 1]
            if exp < required_exp:
                raise ValueError(f"heart text {index} requires archive exp {required_exp}")
            viewed.add(index)
        normalized = sorted(index for index in viewed if 1 <= index <= len(HEART_TEXT_EXP_GATES))
        ctx.db.execute(
            "UPDATE hero_archive SET text_list=? WHERE uid=? AND archive_id=?",
            (json.dumps(normalized), uid, archive_id),
        )
        return {"archive_id": archive_id, "text_list": requested}

    def read_super_heart(self, ctx, uid, archive_id, index):
        archive_id, cfg = self.get_archive_config(archive_id)
        if not cfg:
            raise ValueError(f"unknown archive_id: {archive_id}")
        self._require_owned_archive(ctx.db, uid, archive_id)
        super_ids = [int(x) for x in cfg.get("super_plot_ids") or []]
        index = int(index or 0)
        if index < 1 or index > len(super_ids):
            raise ValueError(f"invalid super heart index {index} for archive {archive_id}")

        self.ensure_archive_row(ctx.db, uid, archive_id)
        rows = ctx.db.query(
            "SELECT video_list, super_heart_link_list FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        viewed_normal = {int(x) for x in _load_json_list(rows[0].get("video_list")) if str(x).isdigit()}
        missing_normal = [int(x) for x in cfg.get("plot_ids") or [] if int(x) not in viewed_normal]
        if missing_normal:
            raise ValueError(f"normal heart story is not complete: {missing_normal[0]}")

        viewed_super = {}
        for item in _load_json_list(rows[0].get("super_heart_link_list")):
            if isinstance(item, dict):
                item_index = int(item.get("index") or 0)
                if 1 <= item_index <= len(super_ids):
                    viewed_super[item_index] = bool(item.get("is_viewed"))
        for previous in range(1, index):
            if not viewed_super.get(previous):
                raise ValueError(f"previous super heart story {previous} is not viewed")

        required_trust = SUPER_PLOT_TRUST_GATES[min(index - 1, len(SUPER_PLOT_TRUST_GATES) - 1)]
        hero_ids = [int(x) for x in cfg.get("hero_ids") or []]
        placeholders = ",".join("?" for _ in hero_ids)
        trust_rows = ctx.db.query(
            f"SELECT id, trust_level FROM hero WHERE uid=? AND unlock=1 AND id IN ({placeholders})",
            (uid, *hero_ids),
        )
        if not any(int(row.get("trust_level") or 0) >= required_trust for row in trust_rows):
            raise ValueError(f"super heart story {index} requires trust level {required_trust}")

        first_view = not viewed_super.get(index)
        viewed_super[index] = True
        normalized = [
            {"index": item_index, "is_viewed": 1 if viewed_super.get(item_index) else 0}
            for item_index in range(1, max(viewed_super) + 1)
        ]
        ctx.db.execute(
            "UPDATE hero_archive SET super_heart_link_list=? WHERE uid=? AND archive_id=?",
            (json.dumps(normalized), uid, archive_id),
        )
        story_id = super_ids[index - 1]
        if first_view:
            bus.emit(
                Events.STORY_READ,
                ctx,
                uid,
                story_id=story_id,
                archive_id=archive_id,
                story_kind="super_heart",
                source="hero_read_super_heart",
            )
        return {"archive_id": archive_id, "index": index, "story_id": story_id, "first_view": first_view}

    def read_anecdote(self, ctx, uid, hero_id):
        hero_id = int(hero_id or 0)
        anecdote = self.anecdotes.get(hero_id)
        if not anecdote:
            raise ValueError(f"hero {hero_id} has no archive anecdote")
        rows = ctx.db.query(
            "SELECT unlock, trust_level FROM hero WHERE uid=? AND id=?",
            (uid, hero_id),
        )
        if not rows or int(rows[0].get("unlock") or 0) != 1:
            raise ValueError(f"hero {hero_id} is not unlocked")
        if int(rows[0].get("trust_level") or 0) < ANECDOTE_TRUST_GATE:
            raise ValueError(f"hero {hero_id} anecdote requires trust level {ANECDOTE_TRUST_GATE}")

        archive_id = self.ensure_archive_row(ctx.db, uid, int(anecdote["archive_id"]))
        archive_rows = ctx.db.query(
            "SELECT hero_story_list FROM hero_archive WHERE uid=? AND archive_id=?",
            (uid, archive_id),
        )
        stories = {}
        for item in _load_json_list(archive_rows[0].get("hero_story_list")):
            if isinstance(item, dict):
                item_hero_id = int(item.get("hero_id") or 0)
                if item_hero_id:
                    stories[item_hero_id] = bool(item.get("is_viewed"))
        first_view = not stories.get(hero_id)
        stories[hero_id] = True
        normalized = [
            {"hero_id": item_hero_id, "is_viewed": 1 if stories[item_hero_id] else 0}
            for item_hero_id in sorted(stories)
        ]
        ctx.db.execute(
            "UPDATE hero_archive SET hero_story_list=? WHERE uid=? AND archive_id=?",
            (json.dumps(normalized), uid, archive_id),
        )

        rewards = list(anecdote.get("rewards") or []) if first_view else []
        if rewards:
            from operations import _item_add
            for reward in rewards:
                _item_add(ctx, uid, int(reward["id"]), int(reward["num"]))
        if first_view:
            bus.emit(
                Events.ARCHIVE_ANECDOTE_READ,
                ctx,
                uid,
                archive_id=archive_id,
                hero_id=hero_id,
                rewards=rewards,
            )
        return {
            "archive_id": archive_id,
            "hero_id": hero_id,
            "first_view": first_view,
            "rewards": rewards,
        }


archive_service = ArchiveService.get_instance()
