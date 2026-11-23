"""按当前轮换和原生p54 index解析梦境首领，拒绝猜测归属。"""


class BossIdentityError(ValueError):
    pass


def check_kind(stage_type, kind):
    if {0: kind, 10: 1, 100: 2}.get(int(stage_type or 0)) != kind:
        raise BossIdentityError('梦境战斗类型与关卡类别冲突')


def normal_boss_stages(catalog, area_id, group_id):
    groups = catalog.get('stage_groups', {})
    stages = groups.get(str(group_id), groups.get(group_id, {})).get('stages', [])
    areas = catalog.get('normal_areas', {})
    area = areas.get(str(area_id), areas.get(area_id, {}))
    return stages[:area.get('level_amount') or len(stages)]


def resolve(kind, stage_id, index, catalog, normal, advance):
    stage_id, index = int(stage_id), int(index or 0)
    if kind == 1:
        candidates = {int(b['group_id']) for b in normal['boss_challenge_list']
                      if stage_id in normal_boss_stages(catalog, normal.get('area_id'), b['group_id'])}
    else:
        pool = catalog.get('advance_pool') or catalog.get('advance_boss_pool', {})
        selected = {int(b['id']) for b in advance['boss_list']}
        candidates = {bid for bid in selected
                      if int(pool.get(str(bid), {}).get('stage_id') or 0) == stage_id}
        if index:
            if index not in candidates:
                raise BossIdentityError('梦境index未开放或与关卡不符')
            return index
    if len(candidates) != 1:
        raise BossIdentityError(f'梦境首领归属不唯一: stage={stage_id} index={index} candidates={sorted(candidates)}')
    return next(iter(candidates))
