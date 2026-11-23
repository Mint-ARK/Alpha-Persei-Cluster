"""Chapter-specific completion counters use persisted clears, without awarding items."""
from functools import lru_cache


@lru_cache(maxsize=1)
def _plot_tasks():
    from new_player_service import config
    return {int(tid): task for tid, task in config()["tasks"].items() if task["type"] == 3}


def _plot_state(db, uid):
    rows = {row["task_id"]: row for row in db.query(
        "SELECT task_id, progress, complete_flag, claimed_ts FROM task WHERE uid=? AND task_type=3",
        (uid,))}
    claimed = {tid for tid, row in rows.items() if row["claimed_ts"] or row["complete_flag"]}
    claimed.update(row["key_id"] for row in db.query(
        "SELECT key_id FROM claim_ledger WHERE uid=? AND kind='task'", (uid,)))
    tasks = _plot_tasks()
    phase = min((task["phase"] for tid, task in tasks.items() if tid not in claimed),
                default=max(task["phase"] for task in tasks.values()))
    return phase, rows, claimed


def plot_phase_for_claim(db, uid, task_ids):
    if any(int(tid) in _plot_tasks() for tid in task_ids):
        return _plot_state(db, uid)[0]
    return None


def visible_plot_tasks(db, uid, rows, phase=None):
    config = _plot_tasks()
    if phase is None:
        if not any(row.get("task_id", row.get("id")) in config for row in rows):
            return rows
        phase = _plot_state(db, uid)[0]
    return [row for row in rows
            if config.get(row.get("task_id", row.get("id")), {}).get("phase", 0) <= phase]


def sync_plot_chapter(db, uid, previous_phase=None):
    """Materialize the current chapter and return its authoritative live changes."""
    phase, rows, claimed = _plot_state(db, uid)
    chapter = {tid: task for tid, task in _plot_tasks().items() if task["phase"] == phase}
    cleared = {row["id"] for row in db.query(
        "SELECT id FROM chapter WHERE uid=? AND clear_times>0", (uid,))}
    collected = sum(tid in claimed for tid, task in chapter.items() if task["condition"] == 401)
    changes = []
    for tid, task in chapter.items():
        old = rows.get(tid)
        progress = int(old["progress"] or 0) if old else 0
        flag = int(tid in claimed)
        if flag:
            progress = max(progress, task["need"])
        elif task["condition"] == 500:
            progress = min(task["need"], collected)
        elif cleared.intersection(task["additional_parameter"]):
            progress = task["need"]
        changed = old is None or progress != old["progress"] or flag != old["complete_flag"]
        if changed:
            db.execute(
                """INSERT INTO task(uid,task_id,task_type,progress,complete_flag) VALUES(?,?,3,?,?)
                   ON CONFLICT(uid,task_id) DO UPDATE SET task_type=3,
                   progress=excluded.progress,complete_flag=excluded.complete_flag""",
                (uid, tid, progress, flag))
        if changed or (previous_phase is not None and phase != previous_phase):
            changes.append(dict(id=tid, progress=progress, complete_flag=flag))
    return phase, changes


def chapter19_tasks():
    from stage_service import StageService
    return StageService.get_instance().meta.get('chapter_progress_tasks', {}).get('10119', {})


def sync_chapter19_tasks(db, uid, stage_id=None):
    tasks = chapter19_tasks()
    if stage_id is not None:
        tasks = {tid: row for tid, row in tasks.items() if row['stage_id'] == int(stage_id)}
    if not tasks or not db or not uid:
        return []
    cleared = {int(row['id']) for row in db.query(
        'SELECT id FROM chapter WHERE uid=? AND clear_times>0', (uid,))}
    existing = {int(row['task_id']): row for row in db.query(
        'SELECT task_id,progress,complete_flag,claimed_ts FROM task WHERE uid=?', (uid,))}
    claimed = {int(row['key_id']) for row in db.query(
        "SELECT key_id FROM claim_ledger WHERE uid=? AND kind IN ('task','illustrated_task')", (uid,))}
    plan = []
    for task_id, cfg in tasks.items():
        tid = int(task_id)
        old = existing.get(tid)
        progress = int(cfg['need']) if cfg['stage_id'] in cleared else 0
        flag = int(bool(tid in claimed or (old and (old['complete_flag'] or old['claimed_ts']))))
        if old is None and not progress:
            continue
        if old and int(old['progress'] or 0) == progress and int(old['complete_flag'] or 0) == flag:
            continue
        plan.append((tid, cfg['task_type'], progress, flag))
    if not plan:
        return []
    from account_db import savepoint_transaction
    with savepoint_transaction(db):
        for tid, task_type, progress, flag in plan:
            db.execute('''INSERT INTO task(uid,task_id,task_type,progress,complete_flag)
                          VALUES(?,?,?,?,?) ON CONFLICT(uid,task_id) DO UPDATE SET
                          progress=excluded.progress,complete_flag=excluded.complete_flag,
                          task_type=excluded.task_type''', (uid, tid, task_type, progress, flag))
    return [dict(id=tid, progress=progress, complete_flag=flag) for tid, _, progress, flag in plan]
