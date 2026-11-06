"""梦境队伍只排除当前轮换中其他首领锁定的角色。"""

from boss_challenge_reset import BossResetOperation


class BossTeamError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check_team(kind, boss_id, heroes, overview, normal, advance):
    kind, boss_id = int(kind), int(boss_id)
    if kind not in (1, 2) or int(overview['mode']) != kind:
        raise BossTeamError(2, '当前梦境模式不匹配')
    rows = normal['boss_challenge_list'] if kind == 1 else advance['boss_list']
    key = 'group_id' if kind == 1 else 'id'
    if boss_id not in {int(row[key]) for row in rows}:
        raise BossTeamError(2, '梦境首领未开放')
    if (not isinstance(heroes, (list, tuple)) or len(heroes) > 3
            or any(type(h) is not int or h < 0 for h in heroes)):
        raise BossTeamError(2, '梦境队伍参数无效')
    # 0为客户端空槽；不改动玩家队伍顺序，也不将预设视为角色锁。
    selected = {h for h in heroes if h}
    locked = set()
    for row in rows:
        used = row['used_heroes']
        if not isinstance(used, list) or any(type(h) is not int or h < 0 for h in used):
            raise ValueError('梦境锁定记录无效')
        if int(row[key]) != boss_id:
            locked.update(h for h in used if h)
    if selected & locked:
        raise BossTeamError(180021, '修正者已被其他梦境首领锁定')


def ensure_team(db, uid, kind, boss_id, heroes):
    import weekly_challenge_service as weekly
    if db is None:
        raise BossTeamError(2, '梦境存档不可用')
    check_team(kind, boss_id, heroes, *weekly.get_boss_challenge_data(uid, db, strict=True))


class BossTeamSaveOperation(BossResetOperation):
    # 共用严格ACK和失败后的当前状态恢复。Lua发送前已SetCacheHeroTeam，
    # 保存失败也要在ACK之后撤销乐观缓存；成功时无需重推整个梦境。
    def apply(self, data, ctx):
        from core import OperationError
        if ctx.db is None:
            raise OperationError(2, '梦境存档不可用')
        try:
            ctx.db.save_boss_challenge_hero_team(
                self.uid, self.team_kind, int(data.get(self.target_field) or 0), data.get('heroes_cfg') or [])
        except BossTeamError as exc:
            raise OperationError(exc.code, str(exc)) from exc
        return {'result': 0}
