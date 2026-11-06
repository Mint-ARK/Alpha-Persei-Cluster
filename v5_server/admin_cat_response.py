"""Prepare audited exploration state and asset frames before committing SQL."""


def frames(ctx,uid,sc,result):
    from admin_cat_explore_service import AdminCatExploreService
    from middleware import DownFrame
    from operations import _refresh_frames
    raw=ctx.codec_encode('sc_'+str(sc),result)
    if not isinstance(raw,(bytes,bytearray,memoryview)) or not raw:
        raise ValueError('管理喵必要确认帧为空: '+str(sc))
    state=[]
    if result['result']==0:
        data=AdminCatExploreService.get_instance().generate_sc_67001_dict(ctx.db,uid)
        payload=ctx.codec_encode('sc_67001',data)
        if not isinstance(payload,(bytes,bytearray,memoryview)) or not payload:
            raise ValueError('管理喵必要状态帧为空')
        state=[DownFrame(67001,bytes(payload))]
    # Skill/unlock callbacks increment locally; finish consumes the old region.
    # Their full snapshot follows ACK, while actual assets must precede it.
    return _refresh_frames(ctx,uid,strict=True)+[DownFrame(sc,bytes(raw))]+state
