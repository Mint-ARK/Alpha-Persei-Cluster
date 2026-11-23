"""回归测试的 UTC+8 周四时钟、关卡身份和旧档周期兼容。"""

from boss_challenge_cycle import LEGACY_ANCHOR, WEEK, bounds

FIRST_WEEK = bounds(LEGACY_ANCHOR)[1]
ROTATIONS = 16
# 原生同一首领的地图变体：奥利海姆、苍梅、弥诺陶洛斯雕像。
BOSS_STAGE_ALIASES = {3091010: 3091001, 3091014: 3091004, 3091015: 3091006}


def period(now_ts=None):
    start, end, _ = bounds(now_ts)
    return start, end, (start - FIRST_WEEK) // WEEK % ROTATIONS + 1


def weekly_rows(week):
    """新记录按绝对周保存，旧轮换号仅在时间确属本周时读入。"""
    if week <= ROTATIONS:
        return "cycle=?", (week,)
    _, end, rotation = period(week)
    return "(cycle=? OR (cycle=? AND update_ts>=? AND update_ts<?))", (week, rotation, week, end)


def boss_key(stage_id):
    return BOSS_STAGE_ALIASES.get(stage_id, stage_id)


def is_regression_battle(stage_type, dest):
    from weekly_challenge_service import get_core_verification_catalog

    if stage_type == 67:
        return True
    if 10101 <= dest <= 16208:
        return str(dest) in get_core_verification_catalog().get('cycle_info', {})
    return 3090001 <= dest <= 3091999


class IterationPeriodError(ValueError):
    pass


def battle_stage(dest, rotation):
    from weekly_challenge_service import get_core_verification_stages

    alpha, beta = get_core_verification_stages(rotation)
    for row in alpha + beta:
        if row['id'] == dest:
            return row
    # 旧地图号形式只有首个难度有确定含义；原生入口始终发送难度记录 ID。
    candidates = [row for row in alpha + beta if row.get('stage_id') == dest and row['difficult'] == 1]
    if len(candidates) == 1:
        return candidates[0]
    raise IterationPeriodError('回归测试关卡不属于当前轮换')
