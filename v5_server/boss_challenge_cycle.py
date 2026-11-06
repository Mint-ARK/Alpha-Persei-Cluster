"""梦境专用时钟：东八区周四05:00，与宿主机时区无关。"""
import datetime
import time

ZONE = datetime.timezone(datetime.timedelta(hours=8))
LEGACY_ANCHOR = 1700000000
WEEK = 604800


def bounds(now_ts=None):
    now = datetime.datetime.fromtimestamp(time.time() if now_ts is None else now_ts, ZONE)
    start = (now - datetime.timedelta(days=(now.weekday() - 3) % 7)).replace(
        hour=5, minute=0, second=0, microsecond=0)
    if start > now:
        start -= datetime.timedelta(days=7)
    daily = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if daily > now:
        daily -= datetime.timedelta(days=1)
    return int(start.timestamp()), int(start.timestamp()) + WEEK, int(daily.timestamp())


def seed_at(now_ts):
    """保留既有随机编号空间；新周期只在规范起点计算一次。"""
    return (int(now_ts) - LEGACY_ANCHOR) // WEEK


class BattlePeriodExpired(ValueError):
    """旧战斗不能写入当前梦境。"""


def battle_kind(stage_type, dest):
    stage_type, dest = int(stage_type or 0), int(dest or 0)
    if stage_type == 100 or 3012201 <= dest <= 3012399:
        return 2
    if stage_type == 10 or 3010101 <= dest <= 3012015:
        return 1
    return 0


def check_battle_period(session, now_ts=None):
    """兼容旧会话ts；有周期快照时也必须与真实开战时间一致。"""
    started = float(session.get('ts') or 0)
    now = float(time.time() if now_ts is None else now_ts)
    if started <= 0 or started > now or bounds(started)[0] != bounds(now)[0]:
        raise BattlePeriodExpired('梦境战斗已跨期或缺少可信开战时间')
    snapshot = session.get('boss_context')
    if snapshot and int(snapshot.get('cycle_start') or 0) != bounds(now)[0]:
        raise BattlePeriodExpired('梦境开战周期与当前周期不一致')
