"""Rogue battle boundary: local-run progress never falls through plot rewards."""
import json
import uuid

from hodur_service import require


def reserved(kind, stage):
    return kind == 78 or 4100000 <= stage <= 4199999 or 100 <= stage <= 999


def required_frame(ctx, cmd, data):
    from middleware import DownFrame
    raw = ctx.codec_encode('sc_' + str(cmd), data)
    require(isinstance(raw, (bytes, bytearray, memoryview)), '推演必要帧编码失败: ' + str(cmd))
    # Attribute/hero lists are legitimately empty after some transitions.
    empty = (cmd == 88013 and not data.get('attr_list')) or (cmd == 88019 and not data.get('hero_list'))
    require(bool(raw) or empty, '推演必要帧为空: ' + str(cmd))
    return DownFrame(cmd, bytes(raw))


def entry_snapshot(ctx, uid, info):
    from rogueteam_service import RogueTeamService
    from rogueteam_flow import row, flow
    require(info['type'] in (0, 78) and info['battle_times'] == 1, '推演战斗入口不匹配')
    service = RogueTeamService.get(db=ctx.db)
    # get_session_data establishes a unique run identity for old playable saves.
    data = service.get_session_data(uid)
    state = row(service, uid)
    meta = flow(state)
    require(state and state.get('in_game') and meta.get('phase') == 'active', '推演不在战斗阶段')
    require(meta.get('run_id') and state.get('select_node_id') == info['dest'], '推演尚未选择该战斗节点')
    nodes = json.loads(state.get('map_info_json') or '[]')
    node = next((n for n in nodes if n['node_id'] == info['dest']), None)
    require(node and node.get('state') == 0 and node.get('node_type') in (1, 2, 3, 4, 11), '推演节点不可战斗')
    stage = int(node.get('param') or 0)
    require(4100000 <= stage <= 4199999, '推演战斗关卡未就绪')
    from rogueteam_tactics import state_packet
    return dict(rogue_run_id=meta['run_id'], rogue_floor_num=int(state['floor_num']),
                rogue_hidden=bool(meta.get('hidden')), rogue_real_stage=stage,
                rogue_tactic_camp=state_packet(state)['id'],
                rogue_report_token=uuid.uuid4().hex), required_frame(ctx, 88001, data)


def settle(ctx, uid, session, result, report):
    from rogueteam_service import RogueTeamService
    from rogueteam_flow import check_battle, row, flow
    require(type(result) is int and result in (1, 2, 3), '推演战斗结果无效')
    check_battle(ctx.db, uid, session)
    from rogueteam_battle_facts import apply_report
    report_frames = apply_report(ctx, uid, session, report or {})
    if result != 1:
        # Native failure/quit returns to the ordinary failure view and can retry.
        # Their reported remaining energy and single-cast fact are still real;
        # neither implies stage victory or a normal victory achievement.
        return report_frames
    service = RogueTeamService.get(db=ctx.db)
    info = dict(report or {}, _run_id=session['rogue_run_id'])
    code, drops, popup = service.settle_battle_node(uid, session['node_id'], win=True, battle_info=info)
    require(code == 0 and isinstance(drops, dict), '推演节点结算失败')
    frames = [required_frame(ctx, 88033, drops)]
    frames.extend(report_frames)
    # A terminal boss completes the physical room immediately. Its periodic
    # relic reward/upgrade has no later 88200 claim to publish the delta.
    for field, cmd in (('item_list', 88021), ('update_list', 88205)):
        if popup and popup.get(field):
            frames.append(required_frame(ctx, cmd, {field: popup[field]}))
    if popup and popup.get('_tactic_state'):
        from rogueteam_achievement import reconcile
        frames.append(required_frame(ctx, 88211, popup['_tactic_state']))
        frames.extend(reconcile(ctx.db, uid, ctx=ctx))
    if popup and popup.get('param_list'):
        frames.append(required_frame(ctx, 88015, dict(other_info=service._norm_event(popup))))
    data = service.get_session_data(uid)
    require(bool(data), '推演结算状态缺失')
    frames.append(required_frame(ctx, 88001, data))
    frames.append(required_frame(ctx, 88013, dict(attr_list=data.get('attr_list', []))))
    # 88033 calls ResetHeroList; 88001 has no hero_list field in p88.
    frames.append(required_frame(ctx, 88019, dict(hero_list=data.get('hero_list', []))))
    # Event battles may resume a conversation after their reward queue ends.
    # The snapshot carries map state, not the client's EventUpdate payload.
    pending = service.pending_frames(uid)
    for cmd, obj in pending:
        if cmd == 88023:
            frames.append(required_frame(ctx, cmd, obj))
    final = data.get('floor_state') == 1 and data.get('floor_num', 0) >= 3
    blocked = any(cmd in (88015, 88023, 88027, 88221) for cmd, obj in pending)
    hidden = bool(flow(row(service, uid)).get('hidden'))
    if not hidden and not blocked and not (popup and popup.get('param_list')) and (final or service._is_floor_clear(data.get('map_info', []))):
        frames.append(required_frame(ctx, 88221, dict(sign=1)))
    return frames
