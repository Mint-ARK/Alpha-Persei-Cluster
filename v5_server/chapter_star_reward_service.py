"""Chapter chest grants, receipt and outbound state share one SQL transaction."""
import json
import time
from pathlib import Path

from inventory_service import InventoryService
from operation_notifications import checkpoint_notifications, restore_notifications


def claim_and_prepare(ctx, uid, data):
    from middleware import DownFrame
    if ctx.db is None or not uid:
        raise ValueError('Missing chapter chest account')
    cid, order = int(data['id']), int(data['treasure_id'])
    cfg = json.loads(Path(__file__).with_name('chapter_star_rewards.json').read_text('utf-8-sig'))
    row = cfg.get(str(cid))
    if not row or order not in (1, 2, 3) or len(row.get('star_need', [])) < order:
        raise ValueError('Unknown chapter chest')
    rewards = row.get(('first_reward', 'second_reward', 'third_reward')[order - 1])
    if (not rewards or not all(isinstance(p, list) and len(p) == 2
                              and int(p[0]) > 0 and int(p[1]) > 0 for p in rewards)):
        raise ValueError('Chapter chest rewards are not configured')
    section_ids = row['section_id_list']
    if not section_ids:
        raise ValueError('Chapter chest stages are not configured')
    checkpoint = checkpoint_notifications(ctx)
    try:
        with ctx.db.transaction():
            if ctx.db.query('SELECT 1 FROM chapter_star_reward WHERE uid=? AND chapter_id=? AND reward_order=?',
                            (uid, cid, order)):
                raise ValueError('Chapter chest already claimed')
            marks = ','.join('?' for _ in section_ids)
            rows = ctx.db.query(f'SELECT star_list FROM chapter WHERE uid=? AND id IN ({marks})', (uid, *section_ids))
            stars = 0
            for stage in rows:
                values = json.loads(stage['star_list'] or '[]')
                if not isinstance(values, list):
                    raise ValueError('Invalid saved chapter stars')
                stars += sum(int(v) == 1 for v in values)
            if stars < int(row['star_need'][order - 1]):
                raise ValueError('Not enough chapter stars')
            # Reserve the unique receipt before granting; any later failure rolls it back.
            ctx.db.execute('''INSERT INTO chapter_star_reward(uid,chapter_id,reward_order,receive_time)
                              VALUES(?,?,?,?)''', (uid, cid, order, int(time.time())))
            summary = InventoryService.grant_items(ctx, uid, rewards, source='chapter_star_reward')
            if not isinstance(summary, dict) or not summary:
                raise ValueError('Chapter chest inventory grant failed')
            generator = getattr(ctx, 'generator', None)
            if generator is None:
                import generator
            frames = []
            touched = {int(p[0]) for p in rewards}
            for cmd, kw in ((15009, {}), (17023, dict(touched_items=touched, strict=True)), (24017, {})):
                payload = generator.gen_payload(cmd, uid=uid, db=ctx.db, **kw)
                if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
                    raise ValueError(f'Missing chapter chest state sc_{cmd}')
                frames.append(DownFrame(cmd, bytes(payload)))
            payload = ctx.codec_encode('sc_24015', dict(result=0,
                reward_list=[dict(id=int(iid), num=int(num)) for iid, num in rewards]))
            if not isinstance(payload, (bytes, bytearray, memoryview)) or not payload:
                raise ValueError('Missing chapter chest acknowledgement')
            frames.append(DownFrame(24015, bytes(payload)))
        return frames
    except BaseException:
        restore_notifications(ctx, checkpoint)
        raise
