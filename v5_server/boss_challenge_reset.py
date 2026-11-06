"""梦境重置资格和错误后的原生状态恢复。"""


class BossResetError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def ensure_unclaimed(db, uid, reward_type=None):
    db.check_and_lazy_refresh_boss_challenge(uid)
    sql = 'SELECT reward_id FROM boss_challenge_claimed_rewards WHERE uid=?'
    args = (int(uid),)
    if reward_type is not None:
        sql += ' AND reward_type=?'
        args += (int(reward_type),)
    if db.query(sql + ' LIMIT 1', args):
        raise BossResetError(2013, '本期已领取奖励，不能重置梦境')


class BossResetOperation:
    # Lua重置回调不检查result；错误恢复必须排在ACK之后。
    prepare_response_in_transaction = True

    def error_recovery_frames(self, ctx):
        from middleware import DownFrame
        import weekly_challenge_service as weekly
        overview, normal, advance = weekly.get_boss_challenge_data(self.uid, ctx.db, strict=True)
        frames = []
        for sc, data in ((45201, overview), (45001, normal), (45101, advance)):
            payload = ctx.codec_encode('sc_' + str(sc), data)
            if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
                raise ValueError(f'梦境重置恢复帧{sc}为空')
            frames.append(DownFrame(sc, bytes(payload)))
        return frames

    def respond(self, result, data, ctx):
        from middleware import DownFrame
        payload = ctx.codec_encode('sc_' + str(self.sc), dict(result=0))
        if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
            raise ValueError('梦境重置确认帧为空')
        # 成功时Lua已重置对应缓存；保留领史，不重复全量初始化。
        return [DownFrame(self.sc, bytes(payload))]
