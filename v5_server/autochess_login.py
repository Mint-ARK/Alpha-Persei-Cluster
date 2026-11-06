"""Restore one account-owned PVE snapshot, without auto-playing a battle at login."""
from copy import deepcopy
import hashlib
import json
import time
from types import SimpleNamespace

from account_db import savepoint_transaction
from autochess_service import AutoChessService, AutoChessConst, OperationError
from codec import encode


SNAPSHOT_COMMANDS = frozenset((89201, 89101, 89125))
# Captured replay/action rows must not overwrite a restored account or jump UI.
OWNED_COMMANDS = SNAPSHOT_COMMANDS | frozenset((89109, 89113, 89143))
SETTLE_STATES = frozenset((AutoChessConst.STATE_ROUND_SETTLE,
                           AutoChessConst.STATE_TOTAL_SETTLE_WIN,
                           AutoChessConst.STATE_TOTAL_SETTLE_LOSE))
RECOVERY_KIND = 'autochess_orphan_recovery_v1'


def _cancel_orphaned_round(db, svc, uid, session, reason):
    """Archive the exact abandoned run before applying native cancel semantics.

    Only the login caller's invalid PVE settlement reaches this function. The
    full snapshot retains run-local permanent growth for diagnosis; no outcome,
    stage completion, currency, medal or account asset is inferred from it.
    """
    snapshot = dict(
        session=session,
        stored_session=db.query('SELECT * FROM autochess_session WHERE uid=?', (uid,)),
        stored_runtime=db.query('SELECT * FROM ops_common WHERE uid=? AND kind=? AND item_id=?',
                                (uid, svc.SESSION_KIND, '0')))
    identity = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    recovery_id = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    record = dict(version=1, reason='invalid_durable_round', detail=str(reason),
                  action='cancel_without_settlement', recovered_at=int(time.time()), **snapshot)
    db.execute('INSERT INTO ops_common(uid,kind,item_id,value_json) VALUES(?,?,?,?) '
               'ON CONFLICT(uid,kind,item_id) DO NOTHING',
               (uid, RECOVERY_KIND, recovery_id, json.dumps(record, ensure_ascii=False)))
    # Do not send an unsolicited 89129: its Lua handler is a request callback.
    # Cold login instead gets the same NONE state and an empty preparation table.
    return deepcopy(svc.cancel_game(SimpleNamespace(log=lambda *a: None), uid, 0))


def prepare_login_frames(frames, db, uid, generator, policy, log):
    """Return deduplicated login rows and their already encoded live payloads.

    89109 initializes the battle object consumed by the round-settle view.
    89113 unconditionally starts playback in AutoChessAction, so actions are
    deliberately sent only by launch/replay requests, never the login flood.
    """
    first = next((i for i, row in enumerate(frames) if row[1] in OWNED_COMMANDS), None)
    anchor = sum(row[1] not in OWNED_COMMANDS for row in frames[:first]) if first is not None else None
    result = [row for row in frames if row[1] not in OWNED_COMMANDS]
    if not all(policy.allows(cmd) for cmd in SNAPSHOT_COMMANDS):
        return result, {}

    svc = AutoChessService.get_instance(db)
    recovery_reason = None
    with savepoint_transaction(db):
        session = deepcopy(svc.get_session(uid))
        # Historical multiplayer sessions stay on disk, without enabling them.
        state = int(session.get('state', AutoChessConst.STATE_NONE))
        if int(session.get('game_type', 0)) != 0:
            state = AutoChessConst.STATE_NONE
        replay_info = None
        if state in SETTLE_STATES:
            try:
                svc.pending_round(session)
            except OperationError as exc:
                # An exposed settle state without 89109 crashes the native view
                # before its cancel control is usable. Preserve then abandon it.
                session = _cancel_orphaned_round(db, svc, uid, session, exc)
                state = AutoChessConst.STATE_NONE
                recovery_reason = str(exc)
            else:
                info = session['last_replay']['info']
                if info.get('game_type') != 0 or info.get('user_id1') != uid:
                    raise RuntimeError('AutoChess login replay belongs to another player or mode')
                replay_info = info
        payloads = {89201: svc.build_89201_payload(db, uid)}
        payloads[89101] = (svc._build_empty_89101() if state == AutoChessConst.STATE_NONE
                           else svc._build_session_89101(session))
        if replay_info is not None:
            payloads[89109] = encode('sc_89109', dict(result=0, replay_info=replay_info))
        # PREPARE_END may immediately request 89108 in Lua; prepare data first.
        payloads[89125] = encode('sc_89125', dict(game_type=0, state=state))

        for cmd, payload in payloads.items():
            if not isinstance(payload, bytes) or (not payload and cmd != 89201):
                raise RuntimeError(f'AutoChess live login payload sc_{cmd} is missing')
        rows = [(0, cmd, generator.build_frame(cmd, b'')[:11], payload, 0, None)
                for cmd, payload in payloads.items()]
    if recovery_reason is not None:
        log(f'[autochess] uid={uid} 旧在局记录缺少有效战报，已保留恢复记录并取消本局，未结算或发奖: {recovery_reason}', 'WARN')
    marker = next((i for i, row in enumerate(result) if row[1] == 10201), len(result))
    anchor = min(marker, anchor) if anchor is not None else marker
    result[anchor:anchor] = rows
    return result, payloads
