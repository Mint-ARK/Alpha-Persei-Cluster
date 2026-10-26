"""Committed task-cycle baselines, queued before the current operation starts."""
from contextlib import contextmanager

from account_db import savepoint_transaction
from operation_notifications import checkpoint_notifications, restore_notifications

DAILY_TASK_FILTER = 'task_type IN (6,7,901,1101,3001) OR task_id BETWEEN 6001 AND 6099 OR task_id BETWEEN 71000 AND 71999'
WEEKLY_TASK_FILTER = 'task_type IN (5,8,902) OR task_id BETWEEN 5001 AND 5099 OR task_id BETWEEN 81000 AND 81999'
DAILY_TASK_TYPES = (6, 7, 901, 1101, 3001)
WEEKLY_TASK_TYPES = (5, 8, 902)


def task_expiry_for_type(task_type):
    """只对已有明确日/周重置规则的类型设置当前周期到期时间。"""
    if task_type in DAILY_TASK_TYPES:
        from lazy_timer import get_daily_5am_ts
        return get_daily_5am_ts() + 86400
    if task_type in WEEKLY_TASK_TYPES:
        from lazy_timer import get_weekly_mon_5am_ts
        return get_weekly_mon_5am_ts() + 7 * 86400
    return 0


def queue_cycle_baseline(ctx, uid, task_filter, *, weekly):
    if not (hasattr(ctx, 'append_frame') or hasattr(ctx, 'pending_frames')):
        return
    import generator
    from codec import encode
    from middleware import DownFrame
    db = ctx.db
    encoder = getattr(ctx, 'codec_encode', None) or encode
    tasks = db.query(
        'SELECT task_id,progress,claimed_ts,complete_flag,expired_ts FROM task '
        f'WHERE uid=? AND ({task_filter}) ORDER BY task_id', (uid,))
    packets = []
    if tasks:
        # assignment_info also carries expired_timestamp; TaskModify accepts
        # zero progress/claim flags without replacing plot/newbie phases.
        packets.append((28007, encoder('sc_28007', {'progress_list':[
            dict(id=r['task_id'], progress=int(r.get('progress') or 0),
                 complete_flag=1 if r.get('claimed_ts') else int(r.get('complete_flag') or 0),
                 expired_timestamp=int(r.get('expired_ts') or 0)) for r in tasks]})))
    packets.append((17023, generator.gen_payload(17023, uid=uid, db=db,
        touched_items=[35 if weekly else 22], strict=True)))
    packets.append((28019, generator.gen_28019(None, db, uid)))
    if weekly and db.query('SELECT 1 FROM battlepass WHERE uid=?', (uid,)):
        packets.append((34031, generator.gen_payload(34031, uid=uid, db=db)))
    for cmd, payload in packets:
        # sc_28019 can validly encode an empty repeated pt_list as b''.
        if not isinstance(payload, (bytes, bytearray, memoryview)) or (not payload and cmd != 28019):
            raise ValueError(f'周期任务必要通知 sc_{cmd} 生成失败')
    for cmd, payload in packets:
        frame = DownFrame(cmd, bytes(payload))
        if hasattr(ctx, 'append_frame'):
            ctx.append_frame(frame)
        else:
            ctx.pending_frames.append(frame)


@contextmanager
def task_cycle_reset(ctx, uid, task_filter, *, weekly=False):
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            # 在对应真实周期重置时修复旧缺类型实例，平时不重开领取资格。
            types = WEEKLY_TASK_TYPES if weekly else DAILY_TASK_TYPES
            placeholders = ','.join('?' for _ in types)
            ctx.db.execute(
                'UPDATE task SET task_type=(SELECT c.task_type FROM task_cfg c WHERE c.task_id=task.task_id) '
                'WHERE uid=? AND COALESCE(task_type,0)=0 AND task_id IN '
                f'(SELECT task_id FROM task_cfg WHERE task_type IN ({placeholders}))', (uid, *types))
            yield
            queue_cycle_baseline(ctx, uid, task_filter, weekly=weekly)
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise
