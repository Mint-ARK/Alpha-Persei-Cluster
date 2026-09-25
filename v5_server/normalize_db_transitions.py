# -*- coding: utf-8 -*-
"""
normalize_db_transitions.py
统一将指定 SQLite 数据库中 hero 表的 exclusive_skill_list 字段
从旧版 List-of-Lists 格式平滑迁移规整为规范的标准 JSON Dict 格式。
"""
import sys
import os
import sqlite3
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hero_codec import normalize_exclusive_skills

def migrate_database(db_path):
    if not os.path.exists(db_path):
        print(f"[ERROR] 数据库文件不存在: {db_path}")
        return False

    print(f"[*] 开始检查并规整数据库跃迁数据: {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT uid, id, exclusive_skill_list FROM hero WHERE exclusive_skill_list IS NOT NULL AND exclusive_skill_list != '' AND exclusive_skill_list != '[]'")
    rows = cur.fetchall()

    migrated_count = 0
    unchanged_count = 0

    for uid, hid, raw_s in rows:
        norm = normalize_exclusive_skills(raw_s)
        canonical_s = json.dumps(norm)
        if canonical_s != raw_s:
            cur.execute("UPDATE hero SET exclusive_skill_list = ? WHERE uid = ? AND id = ?", (canonical_s, uid, hid))
            migrated_count += 1
            print(f"  -> 已规整 Hero uid={uid} id={hid}: {len(norm)} 槽位已标准化")
        else:
            unchanged_count += 1

    conn.commit()
    conn.close()
    print(f"[OK] 迁移完成: 共检查 {len(rows)} 位英雄，规整 {migrated_count} 位，跳过已规范 {unchanged_count} 位。\n")
    return True

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "account_test.db"
    migrate_database(target)
