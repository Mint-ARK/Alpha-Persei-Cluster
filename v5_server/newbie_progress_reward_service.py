"""Ordinary-save cumulative task rewards use native version/phase facts."""
from functools import lru_cache
import json
from pathlib import Path
import time


@lru_cache(maxsize=1)
def contract():
    return json.loads(Path(__file__).with_name('newbie_reward_contract.json').read_text('utf-8'))


def claim(db, uid, index, grant, *, now=None):
    now = int(time.time() if now is None else now)
    rows = db.query('SELECT trigger_time,completed_time,max_phase,version_id FROM newbie_activity WHERE uid=?', (uid,))
    if not rows:
        raise ValueError('新手活动未初始化')
    row = rows[0]
    trigger, completed = int(row.get('trigger_time') or 0), int(row.get('completed_time') or 0)
    if trigger <= 0 or trigger > now or completed < 0 or (completed and completed <= now):
        raise ValueError('新手活动未开启或已结束')
    cfg = contract()
    version = cfg['versions'].get(str(int(row.get('version_id') or 3)))
    if not version:
        raise ValueError('未知的新手活动版本')
    thresholds = version['progress_thresholds']
    if type(index) is not int or not 1 <= index <= len(thresholds):
        raise ValueError('未知的新手累计奖励档位')
    if db.query('SELECT 1 FROM newbie_pt_reward WHERE uid=? AND pt_id=?', (uid, index)):
        raise ValueError('新手累计奖励已领取')
    # Match ordinary59011's published phase, including its established zero->7
    # fallback; this ticket does not invent phase-unlock or initialization policy.
    phase = int(row.get('max_phase') or 7)
    kind = version['task_type']
    eligible = {int(tid) for tid, task in cfg['tasks'].items()
                if task['task_type'] == kind and 1 <= task['phase'] <= phase}
    completed_ids = {int(task['task_id']) for task in db.query(
        'SELECT task_id FROM task WHERE uid=? AND task_type=? AND complete_flag>=1', (uid, kind))}
    if len(eligible & completed_ids) < thresholds[index-1]:
        raise ValueError('新手任务累计完成数不足')
    db.execute('INSERT INTO newbie_pt_reward(uid,pt_id,update_ts) VALUES(?,?,?)', (uid, index, now))
    # Preserve the existing ordinary-save customization, independent of native previews.
    grant(38, 2)
    return [{'id': 38, 'num': 2}]
