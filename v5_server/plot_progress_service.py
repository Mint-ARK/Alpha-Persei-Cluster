"""Chapter-specific completion counters use persisted clears, without awarding items."""
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
