"""Prepare durable Rogue snapshots before the login-complete barrier."""
from types import SimpleNamespace

from codec import encode
from rogueteam_battle import required_frame


OWNED_COMMANDS = frozenset((88001, 88013, 88019, 88015, 88023, 88027, 88211, 88217, 88221, 88315))


def prepare_login_frames(frames, db, uid, generator, policy):
    from rogueteam_service import RogueTeamService
    skip_tutorial = (db.get_system_config('save.rogueteam_tutorial') == 'skip'
                     and db.get_system_config('save.owner_uid') == str(uid))
    owned = OWNED_COMMANDS | {88029} if skip_tutorial else OWNED_COMMANDS
    result = [r for r in frames if r[1] not in owned]
    payloads = {}
    if skip_tutorial:
        # This only sets UpdateGuideState's boolean; it grants no access,
        # stages, clear history, assets or other feature/tutorial completion.
        payloads[88029] = required_frame(SimpleNamespace(codec_encode=encode), 88029,
                                        dict(template_id=100001, finish_guide=1)).payload
    if policy.allows(88315):
        raw = generator.gen_payload(88315, uid=uid, db=db)
        if not isinstance(raw, bytes) or not raw:
            raise ValueError('推演羁绊登录状态缺失')
        payloads[88315] = raw
    if policy.allows(88001):
        svc = RogueTeamService.get(db=db)
        with db.transaction():
            data = svc.get_session_data(uid)
            from rogueteam_flow import row
            from rogueteam_tactics import state_packet
            if policy.allows(88211):
                payloads[88211] = required_frame(SimpleNamespace(codec_encode=encode), 88211,
                                               state_packet(row(svc, uid))).payload
            elif state_packet(row(svc, uid))['id']:
                raise ValueError('推演登录策略缺少必要战术状态')
            if data and data.get('in_game'):
                pending = svc.pending_frames(uid)
                # 88221 only sets the flag. PathInitData in 88001 chooses the map
                # using that flag, so cold recovery must deliver the flag first.
                packets = [*(item for item in pending if item[0] == 88221),
                           (88001, data), (88013, dict(attr_list=data.get('attr_list', []))),
                           (88019, dict(hero_list=data.get('hero_list', []))),
                           *(item for item in pending if item[0] != 88221)]
                for cmd, obj in packets:
                    # One active-run snapshot must not omit its necessary tail.
                    if not policy.allows(cmd):
                        raise ValueError('推演登录策略缺少必要状态: ' + str(cmd))
                    payloads[cmd] = required_frame(SimpleNamespace(codec_encode=encode), cmd, obj).payload
    marker = next((i for i, r in enumerate(result) if r[1] == 10201), len(result))
    result[marker:marker] = [(0, cmd, generator.build_frame(cmd, b'')[:11], raw, 0, None)
                             for cmd, raw in payloads.items()]
    return result, payloads
