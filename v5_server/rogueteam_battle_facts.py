"""Registered records 80/88, bound to this Rogue battle attempt."""
import json
from pathlib import Path

from hodur_service import require

with Path(__file__).with_name('rogueteam_battle_facts_contract.json').open(encoding='utf-8') as handle:
    CONTRACT = json.load(handle)

KIND = 'rogueteam_battle_facts_v1'
TEMPLATE = 100001


def evidence(db, uid):
    row = db.get('ops_common', uid, 'AND kind=? AND item_id=?', (KIND, str(TEMPLATE))) or {}
    try:
        result = json.loads(row.get('value_json') or '{}')
    except (ValueError, TypeError):
        return 0
    value = result.get('single_cast_max', 0) if isinstance(result, dict) else 0
    return value if type(value) is int and 0 <= value <= 0x7fffffff else 0


def achievement_targets(db, uid):
    cfg = CONTRACT['achievement']
    if evidence(db, uid) >= cfg['parameter'][2]:
        yield cfg


def records(report):
    ids = report.get('record_data_ids', [])
    values = report.get('record_data_numbers', [])
    require(isinstance(ids, list) and isinstance(values, list) and len(ids) == len(values),
            '推演战斗记录列表损坏')
    selected = {}
    for key, value in zip(ids, values):
        require(type(key) is int, '推演战斗记录编号无效')
        if key not in (80, 88):
            continue
        require(key not in selected and type(value) is int and 0 <= value <= 0x7fffffff,
                '推演战术记录重复或数值无效')
        selected[key] = value
    return selected


def publish(db, uid, bid, node, snapshot):
    from rogueteam_flow import row, flow, save_flow
    from rogueteam_service import RogueTeamService
    service = RogueTeamService.get(db=db)
    current = row(service, uid)
    meta = flow(current)
    require(current and current.get('in_game') and current.get('select_node_id') == node
            and meta.get('run_id') == snapshot['rogue_run_id']
            and current['floor_num'] == snapshot['rogue_floor_num']
            and bool(meta.get('hidden')) == snapshot['rogue_hidden'], '推演战报发布时本局已变化')
    from rogueteam_tactics import state_packet
    require(state_packet(current)['id'] == snapshot['rogue_tactic_camp'], '推演入场战术已变化')
    meta['report_attempt'] = dict(bid=bid, token=snapshot['rogue_report_token'], node=node)
    save_flow(service, uid, TEMPLATE, meta)


def apply_report(ctx, uid, session, report):
    from rogueteam_flow import row, flow, save_flow
    from rogueteam_service import RogueTeamService
    from rogueteam_tactics import state_packet
    from rogueteam_battle import required_frame
    selected = records(report)
    if not selected:
        return []
    service = RogueTeamService.get(db=ctx.db)
    current = row(service, uid)
    packet = state_packet(current)
    # A matching active tactic at entry proves that these records belong to
    # a real tactic. An empty-slot fight cannot charge or earn a tactic later.
    camp = session.get('rogue_tactic_camp')
    if camp == 0 and packet['id'] == 0:
        return []
    require(type(camp) is int and 1 <= camp <= 6 and packet['id'] == camp,
            '推演战术记录与入场战术不匹配')
    meta = flow(current)
    owner = meta.get('report_attempt') or {}
    require(owner.get('bid') == session.get('rogue_report_bid')
            and owner.get('token') == session.get('rogue_report_token')
            and bool(owner.get('token')) and owner.get('node') == session.get('node_id'),
            '推演战报已不属于当前尝试')
    frames = []
    if 80 in selected and selected[80] != packet['value']:
        # Record 80 is an absolute remaining value, including an explicit zero.
        meta['tactic_v1']['energy'] = selected[80]
        save_flow(service, uid, TEMPLATE, meta)
        frames.append(required_frame(ctx, 88211, dict(id=camp, value=selected[80])))
    if 88 in selected:
        maximum = max(evidence(ctx.db, uid), selected[88])
        if maximum:
            ctx.db.upsert('ops_common', uid, dict(kind=KIND, item_id=str(TEMPLATE),
                value_json=json.dumps(dict(single_cast_max=maximum))), keys=('uid', 'kind', 'item_id'))
        from rogueteam_achievement import reconcile
        frames.extend(reconcile(ctx.db, uid, ctx=ctx))
    return frames
