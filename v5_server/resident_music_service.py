"""Resident music completion facts, native task projection and legacy rewards."""
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import time

from account_db import savepoint_transaction
from core import OperationError
from event_bus import bus, Events
from inventory_service import InventoryService
from operation_notifications import checkpoint_notifications, restore_notifications

CATALOG = json.loads(Path(__file__).with_name('activity_music_catalog.json').read_text('utf-8'))
CONTRACT = json.loads(Path(__file__).with_name('resident_music_contract.json').read_text('utf-8'))


def require(ok, message):
    if not ok:
        raise OperationError(2, message)


@contextmanager
def atomic(ctx):
    checkpoint = checkpoint_notifications(ctx)
    try:
        with savepoint_transaction(ctx.db):
            yield
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise


def song(mid):
    require(type(mid) is int and str(mid) in CATALOG, '未知音乐曲目')
    return CATALOG[str(mid)]


def records(db, uid):
    """Zero records are a payload default, not 166 writes on first login."""
    played = {int(r['music_id']): r for r in db.query(
        'SELECT music_id,score,sign,is_rewarded FROM resident_music_record WHERE uid=?', (uid,))}
    return [played.get(mid, dict(music_id=mid, score=0, sign=0, is_rewarded=0))
            for mid in sorted(map(int, CATALOG))]


def settle(ctx, uid, mid, score, state):
    song(mid)
    require(type(score) is int and 0 <= score <= 0xFFFFFFFF, '音乐分数超出合法范围')
    require(type(state) is int and state in (1, 2, 3), '音乐完成状态不合法')
    with atomic(ctx):
        previous = ctx.db.query('SELECT score,sign FROM resident_music_record WHERE uid=? AND music_id=?', (uid, mid))
        # MusicAction's success callback replaces score AND state only for a
        # new/tied best score. Do not combine two different performances.
        if not previous or score >= int(previous[0]['score'] or 0):
            ctx.db.execute('''INSERT INTO resident_music_record(uid,music_id,score,sign,is_rewarded,update_ts)
                VALUES(?,?,?,?,0,?) ON CONFLICT(uid,music_id) DO UPDATE SET
                score=excluded.score,sign=excluded.sign,update_ts=excluded.update_ts''',
                (uid, mid, score, state, int(time.time())))
        bus.emit_required(Events.MUSIC_COMPLETE, ctx, uid, music_id=mid)
    return dict(result=0)


def project_tasks(db, uid):
    """Condition 50104 identifies the song's activity, not its task number."""
    completed = {int(CATALOG[str(r['music_id'])]['activity_id']) for r in db.query(
        'SELECT music_id FROM resident_music_record WHERE uid=? AND sign BETWEEN 1 AND 3', (uid,))
        if str(r['music_id']) in CATALOG}
    if not completed:
        return []
    changes = []
    now = int(time.time())
    rows = db.query('''SELECT t.task_id,t.progress,c.need,c.additional_parameter
        FROM task t JOIN task_cfg c ON c.task_id=t.task_id
        WHERE t.uid=? AND c.condition=50104
          AND COALESCE(t.complete_flag,0)=0 AND COALESCE(t.claimed_ts,0)=0
          AND (COALESCE(t.expired_ts,0)=0 OR t.expired_ts>?)
          AND NOT EXISTS(SELECT 1 FROM claim_ledger cl WHERE cl.uid=t.uid
              AND cl.key_id=t.task_id AND cl.kind IN ('task','illustrated_task'))''', (uid, now))
    for row in rows:
        try:
            ids = json.loads(row['additional_parameter']) if isinstance(row['additional_parameter'], str) else row['additional_parameter']
        except (ValueError, TypeError):
            continue
        # Current native configs all require one completion and one activity.
        # Do not invent cumulative counts or wildcard rules for unknown configs.
        if (int(row['need'] or 0) != 1 or not isinstance(ids, list) or len(ids) != 1
                or type(ids[0]) is not int or ids[0] not in completed):
            continue
        if int(row['progress'] or 0) < 1:
            db.execute('UPDATE task SET progress=1 WHERE uid=? AND task_id=?', (uid, row['task_id']))
            changes.append(dict(id=int(row['task_id']), progress=1, complete_flag=0))
    return changes


def notify_tasks(ctx, uid):
    changes = project_tasks(ctx.db, uid)
    if changes:
        from middleware import DownFrame
        from codec import encode
        payload = (getattr(ctx, 'codec_encode', None) or encode)('sc_28007', dict(progress_list=changes))
        if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
            raise ValueError('音乐任务差量帧为空')
        ctx.append_frame(DownFrame(28007, bytes(payload)))
    return changes


def restore_for_login(db, uid):
    with savepoint_transaction(db):
        return project_tasks(db, uid)


def claim(ctx, uid, ids):
    require(isinstance(ids, list) and bool(ids), '缺少音乐奖励曲目')
    require(all(type(mid) is int for mid in ids) and len(set(ids)) == len(ids), '音乐奖励曲目重复或不合法')
    with atomic(ctx):
        rewards = Counter()
        for mid in ids:
            cfg = song(mid)
            require(bool(cfg['rewards']), '此曲目没有直接领取奖励，请通过任务领奖')
            rows = ctx.db.query('SELECT score,sign,is_rewarded FROM resident_music_record WHERE uid=? AND music_id=?', (uid, mid))
            require(bool(rows) and int(rows[0]['sign'] or 0) in (1, 2, 3), '音乐曲目尚未完成')
            row = rows[0]
            require(not row['is_rewarded'], '音乐奖励已领取')
            target = int(CONTRACT['targets'][str(mid)])
            require(1 <= target <= len(CONTRACT['grades']), '音乐奖励目标配置不合法')
            require(int(row['score'] or 0) * 100 >= int(cfg['total_score']) * CONTRACT['grades'][target - 1], '音乐分数未达奖励要求')
            for iid, num in cfg['rewards']:
                require(type(iid) is int and type(num) is int and iid > 0 and num > 0, '音乐奖励配置不完整')
                rewards[iid] += num
        # Preflight the entire batch before setting any claim flags or granting.
        for mid in ids:
            ctx.db.execute('UPDATE resident_music_record SET is_rewarded=1,update_ts=? WHERE uid=? AND music_id=?', (int(time.time()), uid, mid))
        items = list(rewards.items())
        summary = InventoryService.grant_items(ctx, uid, items, source='resident_music_reward')
        actual = InventoryService.rewards_after_overflow(items, summary)
        return dict(result=0, reward_list=[dict(id=i, num=n) for i, n in actual])
