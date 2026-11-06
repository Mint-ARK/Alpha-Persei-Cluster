"""Restore account-owned Polyhedron state and terminal receipt before 10201."""
from types import SimpleNamespace
from codec import encode
from polyhedron_flow import frame, cold_settlement
from weekly_challenge_service import get_polyhedron_data

OWNED_COMMANDS = frozenset((18001, 18003, 18005))


def prepare_login_frames(frames, db, uid, generator, policy, res_version=None):
    result = [row for row in frames if row[1] not in OWNED_COMMANDS]
    payloads = {}
    if policy.allows(18001):
        ctx = SimpleNamespace(db=db,codec_encode=encode)
        with db.transaction():
            packets = [frame(ctx,18001,get_polyhedron_data(uid,db,res_version=res_version))]
            packets += cold_settlement(ctx,uid)
            for packet in packets:
                if not policy.allows(packet.cmd):
                    raise ValueError('多维登录策略缺少必要状态: '+str(packet.cmd))
                payloads[packet.cmd] = packet.payload
    marker = next((i for i,row in enumerate(result) if row[1]==10201),len(result))
    result[marker:marker] = [(0,cmd,generator.build_frame(cmd,b'')[:11],raw,0,None)
                             for cmd,raw in payloads.items()]
    return result,payloads
