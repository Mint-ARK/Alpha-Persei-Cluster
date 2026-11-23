"""Version-specific, stock-client activity rotation for Iteration Special Challenge."""

from datetime import datetime
import time
from typing import NamedTuple

from boss_challenge_cycle import WEEK, ZONE


class Group(NamedTuple):
    mode: int
    main: int
    rank: int
    stage: int


ROOT = 111
COMMANDS = {1: 89013, 2: 89025, 3: 89431, 4: 89801}
BATTLE_MODES = {88: 1, 90: 2, 91: 3, 93: 4}
TEMPLATES = {1: 393, 2: 403, 3: 417, 4: 433}
LEGACY = (
    Group(4, 4243301, 4243401, 4243501),
    Group(1, 4339301, 4339401, 4339501),
    Group(4, 4343301, 4343401, 4343501),
)
MODERN = tuple(Group(4, 4443300 + i, 4443400 + i, 4443500 + i) for i in range(1, 5))
NATIVE_TEMPLATES = {base + i for base in TEMPLATES.values() for i in range(3)}
NATIVE_IDS = {aid for group in LEGACY + MODERN for aid in group[1:]}
RANK_HISTORY = {
    4243401: (4140401, 4241801),
    4339401: (4241801, 4243401),
    4343401: (4243401, 4339401),
}
HISTORICAL_RANK_TEMPLATES = {4140401: 404, 4241801: 418}
# Native ConditionCfg 1441301 through 1441303.
DAWN_CLEAR_TASKS = {50201: 3092101, 50202: 3092102, 50203: 3093101}
# First period preserves the currently served Mode 4 group in either baseline.
ANCHOR = int(datetime(2026, 11, 19, 5, tzinfo=ZONE).timestamp())
PERIOD_SECONDS = 4 * WEEK


def period(now_ts=None):
    now = int(time.time() if now_ts is None else now_ts)
    index = max(0, (now - ANCHOR) // PERIOD_SECONDS)
    start = ANCHOR + index * PERIOD_SECONDS
    return start, start + PERIOD_SECONDS, index


def pool(version=None):
    from res_version_manager import get_current_version

    build = str(get_current_version() if version is None else version)
    return MODERN if build.isdigit() and int(build) >= 311 else LEGACY


def active_groups(version=None, now_ts=None):
    _, _, index = period(now_ts)
    groups = pool(version)
    if groups == LEGACY:
        return LEGACY[1], (LEGACY[2], LEGACY[0])[index % 2]
    return (groups[index % len(groups)],)


def activity_rows(version=None, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    start, end, _ = period(now_ts)
    groups = pool(version)
    active = active_groups(version, now_ts)
    historical_ranks = {aid for group in active for aid in RANK_HISTORY.get(group.rank, ())}
    rows = [dict(activity_id=ROOT, start_time=start, stop_time=end, state=1,
                 theme=0, template=74, sub_activity_id_list=[g.main for g in groups])]
    for group in groups:
        template = TEMPLATES[group.mode]
        rows.extend((
            dict(activity_id=group.main, start_time=start, stop_time=end, state=int(group in active),
                 theme=group.main // 100000, template=template, sub_activity_id_list=[group.rank, group.stage]),
            dict(activity_id=group.rank, start_time=start, stop_time=end,
                 state=int(group in active or group.rank in historical_ranks),
                 theme=group.rank // 100000, template=template + 1,
                 sub_activity_id_list=list(RANK_HISTORY.get(group.rank, ()))),
            dict(activity_id=group.stage, start_time=start, stop_time=end, state=int(group in active),
                 theme=group.stage // 100000, template=template + 2, sub_activity_id_list=[]),
        ))
    for aid, template in HISTORICAL_RANK_TEMPLATES.items():
        if aid in historical_ranks:
            rows.append(dict(activity_id=aid, start_time=start, stop_time=end, state=1,
                             theme=aid // 100000, template=template, sub_activity_id_list=[]))
    return rows


def overlay_activities(rows, version=None, now_ts=None):
    # Older stock groups should not be picked by the Lua's open-template fallback.
    rows = [row for row in rows if row['activity_id'] != ROOT
            and row['activity_id'] not in NATIVE_IDS and row['template'] not in NATIVE_TEMPLATES]
    return rows + activity_rows(version, now_ts)


def group_for_activity(activity_id, mode, version=None, now_ts=None):
    from core import OperationError

    for group in active_groups(version, now_ts):
        if group.mode == mode and activity_id in (group.main, group.stage):
            return group
    raise OperationError(2, '迭代校验特殊挑战活动未开放')


def is_challenge_battle(stage_type, dest):
    return stage_type in BATTLE_MODES or 3092001 <= dest <= 3093199


def battle_stage(stage_type, dest, activity_id, version=None, now_ts=None):
    from core_verification_cycle import IterationPeriodError
    from weekly_challenge_service import get_core_verification_cl_catalog

    mode = BATTLE_MODES.get(stage_type)
    stage = get_core_verification_cl_catalog().get(f'mode{mode}', {}).get(str(dest))
    groups = active_groups(version, now_ts)
    if stage:
        for group in groups:
            if (group.mode == mode and stage['activity_id'] == group.stage
                    and activity_id in (group.main, group.stage)):
                return group, stage
    raise IterationPeriodError('特殊挑战关卡不属于当前活动')
