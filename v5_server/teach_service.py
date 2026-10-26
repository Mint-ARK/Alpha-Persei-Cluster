"""Teaching progress uses the same persisted chapter records as settlement."""

import json
from pathlib import Path

from event_bus import bus, Events


class TeachService:
    def __init__(self):
        cfg = json.loads(Path(__file__).with_name("teach_stage_cfg.json").read_text(encoding="utf-8"))
        self.hero_stages = {int(h): tuple(stages) for h, stages in cfg["hero_stages"].items()}
        self.base_stages = tuple(cfg["base_stages"])
        self.stage_heroes = {sid: hid for hid, stages in self.hero_stages.items() for sid in stages}

    def stage_type(self, stage_id):
        sid = int(stage_id or 0)
        if sid in self.stage_heroes:
            return 13
        if sid in self.base_stages:
            return 12
        return 0

    def payload(self, db, uid, cmd):
        # No captured login state and no shared archive/form ID substitution.
        counts = {int(r["id"]): max(0, int(r["clear_times"] or 0)) for r in db.query(
            "SELECT id, clear_times FROM chapter WHERE uid=?", (uid,))}

        def stage(sid):
            return {"id": sid, "clear_times": counts.get(sid, 0)}

        if cmd == 47001:
            return {"base_stage_list": [stage(sid) for sid in self.base_stages]}
        if cmd == 47003:
            return {"hero_teaching_list": [
                {"hero_id": hid, "stage_list": [stage(sid) for sid in stages]}
                for hid, stages in sorted(self.hero_stages.items())]}
        raise ValueError(f"Unsupported teaching command: {cmd}")

    def pass_stage(self, ctx, uid, stage_id, stage_type_id, win_stars, times=1):
        sid = int(stage_id)
        kind = self.stage_type(sid)
        if not kind or int(stage_type_id or 0) not in (0, kind):
            raise ValueError(f"Teaching stage/type mismatch: {sid}/{stage_type_id}")
        from stage_service import StageService
        count, _, _ = StageService.get_instance(db=ctx.db).pass_stage(
            uid, sid, win_stars=win_stars, times=times)
        # Publish only after persistence, with the live connection context.
        bus.emit(Events.TEACH_STAGE_CLEAR, ctx, uid, stage_id=sid,
                 stage_type_id=kind, hero_id=self.stage_heroes.get(sid, 0), clear_times=count)
        return count


service = TeachService()


@bus.subscribe(Events.TEACH_STAGE_CLEAR)
def on_teach_stage_clear(ctx, uid, stage_id=0, stage_type_id=0, hero_id=0, **kwargs):
    if ctx is None or getattr(ctx, "db", None) is None:
        return
    kind = service.stage_type(stage_id)
    if not kind or kind != int(stage_type_id or 0):
        return
    if kind == 13 and service.stage_heroes[int(stage_id)] != int(hero_id or 0):
        return
    from codec import encode
    from middleware import DownFrame
    cmd = 47003 if kind == 13 else 47001
    frame = DownFrame(cmd, encode(f"sc_{cmd}", service.payload(ctx.db, uid, cmd)))
    ctx.append_frame(frame)
