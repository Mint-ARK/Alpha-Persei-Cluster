"""First-activity preparation hooks driven by native buff moment/action/param.

No persistence or packets here: service operations own the Core transaction.
The later-activity 211 sell summon is retained only to recover legacy boards.
"""
import random
from core import OperationError

ACTIVITY = 3740601


def buffs(svc, piece, *, legacy_sell=False):
    cfg = svc.get_chess_cfg(int(piece['chess_id'])) or {}
    if ACTIVITY not in cfg.get('activity_id', []) and not (legacy_sell and cfg.get('id') in (211,311)):
        return []
    levels = cfg.get('level_buffs', [])
    ids = list(levels[min(svc._piece_level(piece), len(levels)) - 1]) if levels else []
    ids += [int(b['buff_id']) for b in piece.get('buff_list', [])]
    return [svc.get_buff_cfg(i) for i in dict.fromkeys(ids) if svc.get_buff_cfg(i)]


def _board(s):
    return sorted(s.get('board_chess', []), key=lambda p: int(p.get('index', 0)))


def _shield(svc, p):
    return any(b.get('moment') == 1 and b.get('action_type') == 7 for b in buffs(svc, p))


def _targets(svc, s, owner, selector):
    own = _board(s)
    others = [p for p in own if p['unique_id'] != owner['unique_id']]
    if selector == 111:
        return [p for p in own if p['unique_id'] == owner['unique_id']]
    if selector == 112:
        return own
    if selector == 114:
        return random.sample(own, min(1, len(own)))
    if selector == 117:
        return random.sample(others, min(1, len(others)))
    if selector == 120:
        return [p for p in others if abs(p['index'] - owner['index']) == 1]
    if selector == 124:
        return own[:1]
    if selector == 151:
        return [p for p in own if _shield(svc, p)]
    raise OperationError(2, '未支持的准备技能目标')


def _effect(svc, s, owner, buff, depth=0):
    if depth > 8:
        raise OperationError(2, '准备技能附属效果超出限制')
    action = int(buff.get('action_type', 0)); param = buff.get('param') or []
    if action == 5:
        for p in _targets(svc, s, owner, int(param[0])):
            p['atk'] = int(p.get('atk', 0)) + int(param[2][0])
            p['hp'] = int(p.get('hp', 0)) + int(param[2][1])
    elif action == 6:
        mode, key, value = map(int, param)
        fields = {1:'gold', 16:'free_refresh', 17:'free_prop'}
        if key not in fields or mode not in (0, 1):
            raise OperationError(2, '未支持的准备经济属性')
        field = fields[key]
        s[field] = value + (int(s.get(field, 0)) if mode == 1 else 0)
    elif action == 4 and int(param[0]) == 2:
        for bid in param[1]:
            child = svc.get_buff_cfg(bid)
            if not child or child.get('moment') != 1:
                raise OperationError(2, '缺少准备经济附属效果')
            _effect(svc, s, owner, child, depth + 1)
    elif action == 24:
        for item in s.get('shop_items', []):
            if item.get('shop_type', 0) in (0, 2):
                item['atk'] = int(item.get('atk', 0)) + int(param[1][0])
                item['hp'] = int(item.get('hp', 0)) + int(param[1][1])
    elif action == 30:
        rarity, count = map(int, param)
        free = [i for i in range(1, 6) if all(p['index'] != i for p in _board(s))]
        if len(free) < count:
            raise OperationError(2, '没有可放置召唤棋子的空位')
        pool = [c for c in svc.cfg['chess'].values() if c.get('type') == 0
                and c.get('star') == rarity and c.get('shop_price', 0) > 0
                and ACTIVITY in c.get('activity_id', [])]
        if not pool:
            raise OperationError(2, '缺少出售技能召唤配置')
        for index in free[:count]:
            c = random.choice(pool)
            p = dict(unique_id=svc._allocate_uid(s), chess_id=c['id'], index=index,
                     exp=svc._base_chess_exp(s), atk=c['body'][0], hp=c['body'][1])
            p['star'] = svc._piece_level(p)
            svc._apply_chess_bonus(s, p)
            s.setdefault('board_chess', []).append(p)
    else:
        raise OperationError(2, '未支持的准备技能效果')


def trigger(svc, session, moment, actor=None):
    owners = _board(session)
    if moment == 5 and actor and all(p['unique_id'] != actor['unique_id'] for p in owners):
        owners.append(actor)
    planned = [(p, b) for p in owners for b in buffs(svc, p, legacy_sell=moment in (3,5))
               if b.get('moment') == moment]
    repeats = 1 + sum(int(b['param'][0]) for _, b in planned
                      if moment == 10 and b.get('action_type') == 14)
    legacy_buy = moment == 3 and actor and any(p['chess_id'] == 311 and p['unique_id'] != actor['unique_id'] for p,_ in planned)
    if legacy_buy:
        session['buy_count_311'] = int(session.get('buy_count_311', 0)) + 1
    for owner, buff in planned:
        if buff.get('action_type') in (13, 14):
            continue
        condition = int(buff.get('enum', 0))
        if condition == 111 and (not actor or owner['unique_id'] != actor['unique_id']):
            continue
        if condition == 113 and (not actor or owner['unique_id'] == actor['unique_id']):
            continue
        if condition not in (0, 1, 2, 111, 113):
            raise OperationError(2, '未支持的准备技能事件')
        if owner['chess_id'] == 311 and int(buff.get('gap', 0)) > 0:
            if int(session.get('buy_count_311', 0)) % int(buff['gap']):
                continue
        for _ in range(repeats):
            _effect(svc, session, owner, buff)


def round_start(svc, session):
    for piece in session.get('board_chess', []) + session.get('bench_chess', []):
        piece.pop('prop_repeat_used', None)
    trigger(svc, session, 9)


def consume_prop_repeats(svc, session):
    """Count extra effect executions, once per paid item, per owner and round."""
    extra = 0
    for owner in _board(session):
        for buff in buffs(svc, owner):
            if buff.get('moment') != 4 or buff.get('action_type') != 13:
                continue
            used = int(owner.get('prop_repeat_used', 0))
            limit = int(buff.get('effective_time', 0))
            if limit and used >= limit:
                continue
            owner['prop_repeat_used'] = used + 1
            extra += int(buff['param'][0])
    return extra
