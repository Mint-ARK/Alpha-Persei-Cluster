"""Classified kills from the client's registered battle-record counters."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def config():
    return json.loads(Path(__file__).with_name('enemy_kill_cfg.json').read_text('utf-8'))


def classified_kills(info):
    ids=info.get('record_data_ids', [])
    counts=info.get('record_data_numbers', [])
    if not isinstance(ids,list) or not isinstance(counts,list) or len(ids)!=len(counts):
        raise ValueError('Battle record IDs/counts are not parallel lists')
    cfg=config();seen=set();result=[]
    for rid,count in zip(ids,counts):
        if type(rid) is not int:
            raise ValueError('Invalid battle record ID')
        mid=cfg['record_to_monster'].get(str(rid))
        if mid is None:
            continue  # Other counters are damage, modifiers, achievements, etc.
        if rid in seen or type(count) is not int or not 0<=count<=0x7fffffff:
            raise ValueError('Invalid or duplicate monster kill counter')
        seen.add(rid)
        if count:
            monster=cfg['monsters'][str(mid)]
            result.append(dict(monster_id=mid,count=count,race=monster['race'],monster_type=monster['type']))
    return result


@lru_cache(maxsize=1)
def sweep_config():
    return json.loads((Path(__file__).parent / 'data/enemy_sweep_cfg.json').read_text('utf8'))


def statistics(db, uid):
    return db.get_enemy_statistics(uid, {int(mid) for mid in config()['monsters']})


def battle_counts(info, *, sweep=False, stage_id=0, times=1):
    """Real reports are already per-battle; only configured sweeps use a multiplier."""
    malformed = missing = unmapped = 0
    unmapped_monsters = {}
    if sweep:
        if type(times) is not int or not 0 < times <= 0x7fffffff:
            raise ValueError('Invalid sweep count')
        cfg = sweep_config()
        mid = cfg['stages'].get(str(stage_id))
        row = cfg['maps'].get(str(mid))
        if row is None:
            total, kills, unmapped = 0, [], times
        else:
            total = row['total'] * times
            kills = []
            for monster_id, count in row['counts'].items():
                monster = config()['monsters'].get(monster_id)
                if monster:
                    kills.append(dict(monster_id=int(monster_id), count=count * times,
                        race=monster['race'], monster_type=monster['type']))
                else:
                    unmapped_monsters[monster_id] = count * times
        shortfall = 0
    else:
        missing = int(not info)
        try:
            kills = classified_kills(info)
        except ValueError:
            kills, malformed = [], 1
        reported = info.get('enemy_dead_num', 0)
        if type(reported) is not int or not 0 <= reported <= 0x7fffffff:
            reported, malformed = 0, 1
        classified = sum(k['count'] for k in kills)
        total = max(reported, classified)
        shortfall = max(classified - reported, 0) if 'enemy_dead_num' in info else 0
    classified = sum(k['count'] for k in kills)
    return dict(total=total, classified=classified, unclassified=total - classified, kills=kills,
        sweep=bool(sweep), stage_id=int(stage_id), malformed_reports=malformed,
        missing_reports=missing, unmapped_sweeps=unmapped, aggregate_shortfall=shortfall,
        unmapped_monsters=unmapped_monsters)


def record_battle(ctx, uid, battle_id, result, stage_id, info=None, *, sweep=False, times=1):
    """Persist first, then notify task/UI consumers without counting twice."""
    event = battle_counts(info or {}, sweep=sweep, stage_id=stage_id, times=times)
    event['result'] = int(result)
    diffs = ctx.db.record_enemy_statistics(uid, battle_id, event, {int(mid) for mid in config()['monsters']})
    if diffs is None:
        return []
    from event_bus import bus, Events
    from operation_notifications import take_pending_frames
    for killed, diff in zip(event['kills'], diffs):
        bus.emit(Events.MONSTER_KILL, ctx, uid, battle_id=battle_id, stage_id=stage_id,
                 recorded_enemy_info=diff, **killed)
    if getattr(ctx, 'log', None):
        ctx.log(f"[enemy] bid={battle_id} sweep={sweep} total={event['total']} classified={event['classified']} unclassified={event['unclassified']}")
        if event['unmapped_sweeps']:
            ctx.log(f'[enemy] stage={stage_id} has no configured sweep enemy roster', 'WARN')
    return take_pending_frames(ctx)
