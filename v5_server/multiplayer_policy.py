"""Single-player server policy, applied to every save and account.

Protocol ownership: Guild/Cooperation/Friends/Chat/AutoChessMatch actions,
BackHome SILENT_STUB_CMDS and BattleConst.STAGE_TYPE_NEW in the client Lua.
"""
import json
from functools import lru_cache
from pathlib import Path

# Only protocol families dedicated to player-to-player features. AutoChess
# 90050+ includes solo card collection, so its family must not be blanket banned.
BLOCKED_FAMILIES = (
    (19000, 19999),                     # friends and player private messages
    (27000, 27199), (27400, 27699),    # public/guild/player chat
    (31000, 31999), (33000, 33999),    # guild and guild boss
    (36000, 37999), (70000, 70999),    # guild activity and cooperation
    (54100, 54299),                     # cooperation room snapshots
    (65400, 65499), (78000, 78099),    # stronghold and team snowball
    (90000, 90049), (90100, 90199),    # AutoChess matching/online battle
)
BLOCKED_COMMANDS = frozenset((
    57002, 57003, 57004, 57005, 57006, 57007, 57008, 57009,
    58050, 58051, 58058, 58059, 58060, 58061,
    58160, 58161, 58176, 58177, 58178, 58179, 58180, 58181,
    58182, 58183, 58184, 58185,
    *range(58204, 58218),
    60054, 60055, 90080, 90081, 90082, 90083,
))
# Keep the character-chat UI; its player-to-player requests are blocked below.
CLOSED_SYSTEMS = frozenset((12, 703, 2401, 2402, 2403, 2404, 2405, 2406))
CHARACTER_CHAT_COMMANDS = frozenset((19001, 19029, 19030, 19031, 19034, 19035, 19039))
# Do not confuse single-player MULT_HEART_DEMON=82, damage test=1002,
# abyss=1003 or advanced test=1006 with cooperation modes.
BLOCKED_BATTLE_TYPES = frozenset((32, 33, 37, 46, 1001, 1004, 1005, 1007, 1008))
ERROR_FUNCTION_STOP = 17  # TipsCfg.ERROR_FUNCTION_STOP


def blocks_command(cmd):
    cmd = int(cmd)
    if cmd in CHARACTER_CHAT_COMMANDS:
        return False
    return cmd in BLOCKED_COMMANDS or any(lo <= cmd <= hi for lo, hi in BLOCKED_FAMILIES)


def open_systems(values):
    return [value for value in values if int(value) not in CLOSED_SYSTEMS]


def blocks_request(req, ctx):
    if req.cmd in (19034, 32018, 32020):
        data = _request_data(req, ctx)
        target = int(data.get('receive_uid' if req.cmd == 19034 else 'user_id') or 0)
        if req.cmd != 19034 and target in (0, req.uid):
            return False
        # Character conversations and cards are local NPC functionality.
        return not (ctx.db and ctx.db.get_ai_character(target))
    if blocks_command(req.cmd):
        return True
    if req.cmd not in (54030, 54038):
        return False
    # Shared battle entrypoints also serve single-player modes. Read the
    # actual common_info.type; do not disable their entire protocol family.
    data = _request_data(req, ctx)
    info = (data or {}).get('common_info') or {}
    return int(info.get('type') or 0) in BLOCKED_BATTLE_TYPES


def _request_data(req, ctx):
    data = getattr(req, 'body', None)
    if isinstance(data, dict):
        return data
    try:
        return ctx.codec_decode(req.payload, f'cs_{req.cmd}') or {}
    except Exception:
        return {}  # Normal parse validation handles malformed allowed requests.


@lru_cache(maxsize=1)
def _response_routes():
    path = Path(__file__).with_name('skeleton.json')
    return json.loads(path.read_text('utf-8'))['cs_sc']


def reject_request(req, ctx):
    """Return None for allowed requests; otherwise reject before any mutation."""
    if not blocks_request(req, ctx):
        return None
    from middleware import CSResponse, DownFrame
    import codec
    import decode_schema
    codec._ensure_schema()
    route = _response_routes().get(str(req.cmd), {})
    frames = []
    if route.get('reply') != 'no':
        candidates = [row['sc'] for row in route.get('frames', [])]
        if not candidates:
            candidates = [req.cmd + 1]
        # Send only the matching failure acknowledgement, never success-state
        # snapshots or old recorded data from a multi-frame skeleton.
        for cmd in candidates:
            fields = decode_schema.SCHEMA.get(f'sc_{cmd}', {})
            if any(field['name'] == 'result' and field['type'] in ('uint32', 'int32')
                   for field in fields.values()):
                payload = codec.encode(f'sc_{cmd}', {'result': ERROR_FUNCTION_STOP})
                frames.append(DownFrame(cmd, payload))
                break
    if getattr(ctx, 'log', None):
        ctx.log(f'[offline] cs_{req.cmd} multiplayer disabled', 'INFO')
    return CSResponse(frames, reply=bool(frames), src='offline-policy')
