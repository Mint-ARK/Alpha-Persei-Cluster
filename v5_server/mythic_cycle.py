"""失序深区周期：东八区周一/周四05:00，分别为三天和四天。"""
import datetime
import time

ZONE = datetime.timezone(datetime.timedelta(hours=8))


def bounds(now_ts=None):
    now = datetime.datetime.fromtimestamp(time.time() if now_ts is None else now_ts, ZONE)
    previous, following = [], []
    for day in (0, 3):
        candidate = (now - datetime.timedelta(days=(now.weekday() - day) % 7)).replace(
            hour=5, minute=0, second=0, microsecond=0)
        if candidate > now:
            candidate -= datetime.timedelta(days=7)
        previous.append(int(candidate.timestamp()))
        following.append(int((candidate + datetime.timedelta(days=7)).timestamp()))
    return max(previous), min(following)


def is_final(stage_type, dest, activity_id=0):
    stage_type, dest, activity_id = int(stage_type or 0), int(dest or 0), int(activity_id or 0)
    return stage_type == 35 or 3028001 <= dest <= 3028058 or (dest in (1, 2) and (activity_id==105 or 1 <= activity_id <= 30))


class MythicPeriodExpired(ValueError):
    pass


def check_session(session, uid, now_ts=None):
    now = float(time.time() if now_ts is None else now_ts)
    started = float(session.get('ts') or 0)
    if (int(session.get('uid') or 0) != int(uid) or started <= 0 or started > now
            or bounds(started)[0] != bounds(now)[0]):
        raise MythicPeriodExpired('失序深区战斗已跨期或缺少可信开战时间')


def final_payload(progress):
    return dict(difficulty_id_can_choose=progress['can_choose_list'], now_difficulty=progress['now_difficulty'],
                receive_reward=progress['receive_reward'], clear_list=progress['clear_list'],
                is_new_difficulty=progress['is_new_difficulty'], challenge_info=progress['challenge_info'])
