"""Per-save paid rights, weekly battlepass and missed card rewards.

The existing ops_common table stores policy/cursors; no schema migration is needed.
All mutations go through AccountDB and share the caller's transaction/savepoint.
"""
import json
import random
import time
from collections import Counter
from pathlib import Path

from account_db import savepoint_transaction
from lazy_timer import get_daily_5am_ts, get_weekly_mon_5am_ts

DAY = 86400
KIND = 'paid_entitlements_v1'
KEY = 'state'
SMALL_REWARD = 90
WEEKLY_MAIL_EXP = 25000
BIG_POOL = {1: (40103, 3, 5), 2: (40203, 3, 5), 3: (40803, 4, 5),
            4: (40701, 1, 5), 5: (40504, 5, 5), 6: (40603, 2, 5)}
BIG_MILESTONES = {
    2: {201: (1, 38, 10), 202: (7, 1, 200), 203: (14, 19, 10), 204: (21, 1, 300)},
    1: {101: (1, 1, 200), 102: (7, 38, 10), 103: (14, 1, 300), 104: (21, 19, 10)},
}
_BP_CFG = None


def _now(value=None):
    return int(time.time() if value is None else value)


def _row(db, table, uid):
    rows = db.query(f'SELECT * FROM {table} WHERE uid=?', (uid,))
    return rows[0] if rows else {}


def _json(value, fallback):
    if value is None or value == '':
        return fallback
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, type(fallback)):
        raise ValueError('付费权益持久状态格式错误')
    return parsed


def read_state(db, uid):
    rows = db.query('SELECT value_json FROM ops_common WHERE uid=? AND kind=? AND item_id=?',
                    (uid, KIND, KEY))
    return _json(rows[0]['value_json'], {}) if rows else {}


def _save(db, uid, state):
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO UPDATE SET value_json=excluded.value_json',
               (uid, KIND, KEY, json.dumps(state, ensure_ascii=False)))


def _mail(db, uid, title, items, content):
    from mail_service import MailService
    attachments = [{'id': int(i), 'num': int(n)} for i, n in items if n > 0]
    mid = MailService(db=db).send_mail(uid, title, content, attachments=attachments, db=db)
    if not mid:
        raise ValueError('权益补给邮件创建失败')
    return mid


def _bp_id():
    from res_version_manager import get_version_config
    return int(get_version_config()['battlepass_list_id'])


def _premium_gift(db, uid, state, bp):
    global _BP_CFG
    if int(bp.get('pay_level') or 0) != 202 or state.get('bp_gift_issued'):
        return 0
    if _BP_CFG is None:
        _BP_CFG = json.loads(Path(__file__).with_name('battlepass_cfg.json').read_text('utf-8'))
    entry = _BP_CFG['lists'][str(bp['battlepass_list_id'])]
    mid = _mail(db, uid, '深度合约本期赠礼', entry['extra_reward_02'],
                '本期深度合约额外奖励已送达。同一期切换档位不会重复发放。')
    state['bp_gift_issued'] = True
    state['bp_gift_mail'] = mid
    return mid


def _ensure_bp(db, uid, state, now):
    week = get_weekly_mon_5am_ts(now)
    bp = _row(db, 'battlepass', uid)
    legacy = 'bp_cycle' not in state
    reset = not legacy and (int(state['bp_cycle']) < week or state.get('bp_list') != _bp_id())
    if not bp:
        db.get_or_create_battlepass(uid)
        bp = _row(db, 'battlepass', uid)
    if legacy:
        # Old depth purchases already granted their extra bundle directly.
        state['bp_gift_issued'] = int(bp.get('pay_level') or 0) == 202
    elif reset:
        db.set_currency(uid, 14, 0, ts=now)
        db.execute("UPDATE battlepass SET receive_info='[]',weekly_gain_exp=0 WHERE uid=?", (uid,))
        state['bp_gift_issued'] = False
        state.pop('bp_gift_mail', None)
    if legacy or reset:
        state['bp_cycle'], state['bp_list'] = week, _bp_id()
        db.execute('UPDATE battlepass SET battlepass_list_id=?,start_timestamp=?,end_timestamp=?, '
                   'next_refresh_timestamp=?,is_start=1,update_ts=? WHERE uid=?',
                   (_bp_id(), week, week + 7*DAY, week + 7*DAY, now, uid))
        bp = _row(db, 'battlepass', uid)
    if state.get('bp_weekly_mail_cycle') != week:
        state['bp_weekly_mail'] = _mail(db, uid, '每周战令经验补给', [(14, WEEKLY_MAIL_EXP)],
            '本周战令经验补给25,000。经验在领取时计入当前战令；离线期间不逐周追发。')
        state['bp_weekly_mail_cycle'] = week
    _premium_gift(db, uid, state, bp)
    return reset


def ensure_battlepass(db, uid, now_ts=None):
    """Called before adding exp too: never erase a just-claimed reward at rollover."""
    now = _now(now_ts)
    with savepoint_transaction(db):
        state = read_state(db, uid)
        before = dict(state)
        reset = _ensure_bp(db, uid, state, now)
        if state != before:
            _save(db, uid, state)
    return reset


def _small_active(row, state, now):
    return bool(state.get('small_long')) or int(row.get('monthly_card_timestamp') or 0) > now


def _init_small(row, state, now):
    if 'small_cursor' in state:
        return
    today = get_daily_5am_ts(now)
    state['small_cursor'] = today - DAY
    # Do not invent missing purchase/claim history for existing saves.
    state['small_since'] = now if _small_active(row, state, now) else 0
    if row.get('is_sign') and get_daily_5am_ts(int(row.get('update_ts') or 0)) == today:
        state['small_claim_day'] = today
        state['small_cursor'] = today


def _settle_small(db, uid, state, now):
    row = _row(db, 'month_card', uid)
    _init_small(row, state, now)
    start = int(state.get('small_since') or 0)
    if not start:
        return
    until = now if state.get('small_long') else int(row.get('monthly_card_timestamp') or 0)
    if until <= start:
        return
    today = get_daily_5am_ts(now)
    first = max(get_daily_5am_ts(start), int(state['small_cursor']) + DAY)
    last = min(today - DAY, get_daily_5am_ts(until - 1))
    if last >= first:
        count = (last - first)//DAY + 1
        _mail(db, uid, '恒定观测奖励补发', [(1, count * SMALL_REWARD)],
              f'补发有效权益期间遗漏的{count}天恒定观测奖励，每日90移转之辉。')
        state['small_cursor'] = last


def _big_rewards(row, count):
    """Advance a period's unclaimed days; a full fresh period has an exact pool."""
    record = {int(r['index']): int(r['times']) for r in _json(row.get('daily_record'), [])}
    received = [int(x) for x in _json(row.get('total_sign_receive_list'), [])]
    total = int(row.get('total_sign_times') or 0)
    count = min(max(0, count), max(0, 30 - total))
    rewards = Counter()
    if count == 30 and not record and total == 0:
        for index, (iid, num, limit) in BIG_POOL.items():
            rewards[iid] += num * limit
            record[index] = limit
    else:
        for _ in range(count):
            available = [i for i, (_, _, limit) in BIG_POOL.items() if record.get(i, 0) < limit]
            if not available:
                break
            chosen = random.choice(available)
            iid, num, _ = BIG_POOL[chosen]
            rewards[iid] += num
            record[chosen] = record.get(chosen, 0) + 1
    rewards[1] += 100 * count  # Existing private-server daily supplement is retained.
    total += count
    for mid, (need, iid, num) in BIG_MILESTONES[int(row.get('template_id') or 2)].items():
        if total >= need and mid not in received:
            rewards[iid] += num
            received.append(mid)
    row.update(total_sign_times=total, total_sign_receive_list=json.dumps(received),
               daily_record=json.dumps([{'index': i, 'times': n} for i, n in sorted(record.items())]))
    return rewards


def _write_big(db, uid, row, now):
    db.upsert('big_month_card', uid, {k: row.get(k, default) for k, default in (
        ('buy_timestamp', 0), ('is_sign', 0), ('total_sign_times', 0),
        ('total_sign_receive_list', '[]'), ('daily_record', '[]'), ('template_id', 2),
        ('is_expire_tip', 0))} | {'update_ts': now})


def _new_big(row, start):
    # Client computes deadline as purchase-day 05:00 + (30+1) days.
    # Project a 30-day period without changing the client's config.
    row.update(buy_timestamp=start-DAY, is_sign=0, total_sign_times=0,
               total_sign_receive_list='[]', daily_record='[]', is_expire_tip=0)
    row.setdefault('template_id', 2)


def _init_big(row, state, now):
    if 'big_cursor' in state:
        return
    today = get_daily_5am_ts(now)
    buy = int(row.get('buy_timestamp') or 0)
    end = get_daily_5am_ts(buy) + 31*DAY if buy else 0
    state['big_start'] = min(today, end - 30*DAY) if end else 0
    state['big_end'] = end
    state['big_cursor'] = today - DAY
    if row.get('is_sign') and get_daily_5am_ts(int(row.get('update_ts') or 0)) == today:
        state['big_claim_day'] = today
        state['big_cursor'] = today


def _settle_big(db, uid, state, now):
    row = _row(db, 'big_month_card', uid)
    _init_big(row, state, now)
    end = int(state.get('big_end') or 0)
    if not end:
        return
    today = get_daily_5am_ts(now)
    rewards = Counter()
    while True:
        start = int(state['big_start'])
        first = max(start, int(state['big_cursor']) + DAY)
        last = min(today - DAY, end - DAY)
        if last >= first:
            rewards.update(_big_rewards(row, (last-first)//DAY + 1))
            state['big_cursor'] = last
        if now < end or not state.get('big_long'):
            break
        # Aggregate fully missed future periods: no unbounded daily/monthly loop.
        start = end
        full_periods = max(0, (today - start)//(30*DAY))
        if full_periods:
            fresh = {'template_id': row.get('template_id') or 2}
            full = _big_rewards(fresh, 30)
            rewards.update({i: n*full_periods for i, n in full.items()})
            start += full_periods * 30*DAY
        end = start + 30*DAY
        state['big_start'], state['big_end'] = start, end
        state['big_cursor'] = start - DAY
        _new_big(row, start)
    if rewards:
        _mail(db, uid, '百宝囊奖励补发', sorted(rewards.items()),
              '补发有效期内遗漏的每日材料、每日补给和已达成的累计奖励。')
    if row and (rewards or int(row.get('buy_timestamp') or 0) != int(_row(db, 'big_month_card', uid).get('buy_timestamp') or 0)):
        row['is_sign'] = int(state.get('big_claim_day') == today)
        _write_big(db, uid, row, now)


def settle(ctx_or_db, uid, now_ts=None, *, notify=True):
    db = getattr(ctx_or_db, 'db', None) or ctx_or_db
    now = _now(now_ts)
    with savepoint_transaction(db):
        state = read_state(db, uid)
        before = json.dumps(state, sort_keys=True)
        mails = int(db.query('SELECT COUNT(*) AS n FROM mail WHERE uid=?', (uid,))[0]['n'])
        reset = _ensure_bp(db, uid, state, now)
        _settle_small(db, uid, state, now)
        _settle_big(db, uid, state, now)
        changed = before != json.dumps(state, sort_keys=True)
        if changed:
            _save(db, uid, state)
        mailed = int(db.query('SELECT COUNT(*) AS n FROM mail WHERE uid=?', (uid,))[0]['n']) > mails
    if notify and (changed or mailed):
        queue_status(ctx_or_db, uid, include_mail=mailed, reset_exp=reset)
    return status(db, uid, now)


def status(db, uid, now_ts=None):
    now = _now(now_ts)
    state = read_state(db, uid)
    bp, small, big = (_row(db, t, uid) for t in ('battlepass', 'month_card', 'big_month_card'))
    today = get_daily_5am_ts(now)
    paid_until = int(small.get('monthly_card_timestamp') or 0)
    legacy_small_signed = bool(small.get('is_sign')) and get_daily_5am_ts(int(small.get('update_ts') or 0)) == today
    legacy_big_signed = bool(big.get('is_sign')) and get_daily_5am_ts(int(big.get('update_ts') or 0)) == today
    end = int(state.get('big_end') or (get_daily_5am_ts(int(big['buy_timestamp']))+31*DAY if big.get('buy_timestamp') else 0))
    return {
        'battlepass': {'pay_level': int(bp.get('pay_level') or 0), 'exp_14': db.get_item_num(uid, 14),
                      'cycle_start': int(state.get('bp_cycle') or get_weekly_mon_5am_ts(now)),
                      'cycle_end': int(state.get('bp_cycle') or get_weekly_mon_5am_ts(now))+7*DAY,
                      'premium_gift_issued': bool(state.get('bp_gift_issued', int(bp.get('pay_level') or 0)==202))},
        'small_month_card': {'long_term': bool(state.get('small_long')), 'active': _small_active(small, state, now),
                             'paid_until': paid_until, 'signed_today': state.get('small_claim_day') == today or legacy_small_signed},
        'big_month_card': {'auto_renew': bool(state.get('big_long')), 'active': bool(end > now),
                           'cycle_end': end, 'sign_days': int(big.get('total_sign_times') or 0),
                           'signed_today': state.get('big_claim_day') == today or legacy_big_signed},
    }


def update_rights(db, uid, changes, now_ts=None):
    allowed = {'pay_level', 'small_long_term', 'big_auto_renew'}
    if not changes or set(changes) - allowed:
        raise ValueError('权益参数无效')
    if 'pay_level' in changes and (type(changes['pay_level']) is not int or changes['pay_level'] not in (0, 201, 202)):
        raise ValueError('战令档位必须为0、201或202')
    for k in ('small_long_term', 'big_auto_renew'):
        if k in changes and type(changes[k]) is not bool:
            raise ValueError('月卡开关必须为布尔值')
    now = _now(now_ts)
    with savepoint_transaction(db):
        settle(db, uid, now, notify=False)
        state = read_state(db, uid)
        if 'pay_level' in changes:
            db.set_battlepass_pay_level(uid, changes['pay_level'])
            _premium_gift(db, uid, state, _row(db, 'battlepass', uid))
        if 'small_long_term' in changes:
            row = _row(db, 'month_card', uid)
            if changes['small_long_term'] and not _small_active(row, state, now):
                state['small_since'] = now
                state['small_cursor'] = max(int(state.get('small_cursor') or 0), get_daily_5am_ts(now)-DAY)
            state['small_long'] = changes['small_long_term']
            if not _small_active(row, state, now):
                state['small_since'] = 0
        if 'big_auto_renew' in changes:
            state['big_long'] = changes['big_auto_renew']
            if changes['big_auto_renew'] and int(state.get('big_end') or 0) <= now:
                start = get_daily_5am_ts(now)
                state['big_start'], state['big_end'], state['big_cursor'] = start, start+30*DAY, start-DAY
                row = _row(db, 'big_month_card', uid)
                _new_big(row, start)
                _write_big(db, uid, row, now)
        _save(db, uid, state)
    return status(db, uid, now)


def before_small_purchase(db, uid, now_ts=None):
    now = _now(now_ts)
    settle(db, uid, now, notify=False)
    state = read_state(db, uid)
    if not _small_active(_row(db, 'month_card', uid), state, now):
        state['small_since'] = now
        state['small_cursor'] = max(int(state.get('small_cursor') or 0), get_daily_5am_ts(now)-DAY)
        _save(db, uid, state)


def claim_small(ctx, uid, now_ts=None):
    from inventory_service import InventoryService
    now = _now(now_ts)
    settle(ctx, uid, now)
    db = ctx.db
    state = read_state(db, uid)
    today = get_daily_5am_ts(now)
    if not _small_active(_row(db, 'month_card', uid), state, now):
        raise ValueError('恒定观测未激活或已到期')
    if state.get('small_claim_day') == today:
        return {'already': True, 'reward': None}
    InventoryService.grant_items(ctx, uid, [(1, SMALL_REWARD)], source='month_card')
    state['small_claim_day'] = state['small_cursor'] = today
    _save(db, uid, state)
    row = _row(db, 'month_card', uid)
    db.upsert('month_card', uid, {'monthly_card_num': 1,
        'monthly_card_timestamp': int(row.get('monthly_card_timestamp') or 0), 'is_sign': 1, 'update_ts': now})
    # Keep old calendar/newbie consumers aligned without using purchase update_ts as a claim.
    import generator
    st = generator._sign_state(db, uid, 34024)
    date = time.localtime(now-5*3600)
    days = list(st['sign_list'])
    if date.tm_mday not in days:
        days.append(date.tm_mday)
    db.execute('INSERT INTO sign(uid,activity_id,year,month,day,sign_list,sign_count,last_sign_ts) '
        'VALUES(?,34024,?,?,?,?,?,?) ON CONFLICT(uid,activity_id) DO UPDATE SET '
        'year=excluded.year,month=excluded.month,day=excluded.day,sign_list=excluded.sign_list,'
        'sign_count=excluded.sign_count,last_sign_ts=excluded.last_sign_ts',
        (uid,date.tm_year,date.tm_mon,date.tm_mday,json.dumps(days),len(days),now))
    return {'already': False, 'reward': {'id': 1, 'num': SMALL_REWARD}}


def claim_big(ctx, uid, now_ts=None):
    from inventory_service import InventoryService
    now = _now(now_ts)
    settle(ctx, uid, now)
    db = ctx.db
    state = read_state(db, uid)
    today = get_daily_5am_ts(now)
    row = _row(db, 'big_month_card', uid)
    if int(state.get('big_end') or 0) <= now:
        raise ValueError('百宝囊未激活或已到期')
    already = state.get('big_claim_day') == today
    rewards = _big_rewards(row, 0 if already else 1)
    if rewards:
        InventoryService.grant_items(ctx, uid, sorted(rewards.items()), source='big_month_card')
    state['big_claim_day'] = state['big_cursor'] = today
    _save(db, uid, state)
    row['is_sign'] = 1
    _write_big(db, uid, row, now)
    return {'already': already, 'rewards': [{'id':i,'num':n} for i,n in sorted(rewards.items())],
        'daily_record': _json(row.get('daily_record'), []), 'is_sign': 1,
        'total_sign_times': row['total_sign_times'],
        'total_sign_receive_list': _json(row.get('total_sign_receive_list'), [])}


def buy_big(db, uid, now_ts=None):
    """Retain the native purchase entry; pricing policy is intentionally unchanged."""
    now = _now(now_ts)
    settle(db, uid, now, notify=False)
    state = read_state(db, uid)
    if int(state.get('big_end') or 0) > now:
        raise ValueError('百宝囊本期仍有效')
    start = get_daily_5am_ts(now)
    state['big_start'], state['big_end'], state['big_cursor'] = start, start+30*DAY, start-DAY
    row = _row(db, 'big_month_card', uid)
    _new_big(row, start)
    _write_big(db, uid, row, now)
    _save(db, uid, state)


def queue_status(ctx, uid, *, include_mail=False, reset_exp=False):
    if not callable(getattr(ctx, 'append_frame', None)):
        return
    import generator
    from middleware import DownFrame
    commands = [34031, 34023, 34101]
    if include_mail:
        commands.append(30001)
    for cmd in commands:
        payload = generator.gen_payload(cmd, uid=uid, db=ctx.db)
        if not payload:
            raise ValueError(f'必要权益刷新sc_{cmd}为空')
        ctx.append_frame(DownFrame(cmd, payload))
    if reset_exp:
        payload = generator.gen_payload(17023, uid=uid, db=ctx.db, touched_items={14}, strict=True)
        if not payload:
            raise ValueError('战令重置资产差量为空')
        ctx.append_frame(DownFrame(17023, payload))
