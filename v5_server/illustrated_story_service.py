"""Authoritative story-read evidence and required illustration delivery."""
import json


def _list(raw):
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def read_archive_stories(db, uid):
    from archive_service import ArchiveService
    service = ArchiveService.get_instance()
    stories = set()
    for row in db.query('SELECT archive_id,video_list,super_heart_link_list FROM hero_archive WHERE uid=?', (uid,)):
        _, cfg = service.get_archive_config(row['archive_id'])
        if not cfg:
            continue
        normal = set(int(i) for i in cfg.get('plot_ids', []))
        stories.update(i for i in _list(row['video_list']) if type(i) is int and i in normal)
        super_ids = cfg.get('super_plot_ids', [])
        for item in _list(row['super_heart_link_list']):
            if not isinstance(item, dict) or item.get('is_viewed') not in (1, True):
                continue
            index = item.get('index')
            if type(index) is int and 1 <= index <= len(super_ids):
                stories.add(int(super_ids[index-1]))
    return stories


def read_oath_stories(db, uid):
    from oath_service import OATH_HERO_CFG
    levels = {int(r['hero_id']): int(r['oath_level'] or 0) for r in db.query(
        'SELECT hero_id,oath_level FROM hero_oath WHERE uid=? AND oath=1', (uid,))}
    result = set()
    for row in db.query('SELECT hero_id,text_id FROM hero_oath_plot WHERE uid=?', (uid,)):
        hero, story = int(row['hero_id']), int(row['text_id'])
        needed = OATH_HERO_CFG.get(hero, {}).get('plot_levels', {}).get(story)
        if needed is not None and levels.get(hero, 0) >= needed:
            result.add(story)
    return result


def publish_story_unlock(ctx, uid, story_ids):
    """Called by validated readers inside their transaction, including retries."""
    from codec import encode
    from illustrated_listener import trigger_story_unlock, _push_frame
    from middleware import DownFrame
    plots, pictures = [], []
    for story_id in dict.fromkeys(story_ids):
        new_plots, new_pictures = trigger_story_unlock(ctx.db, uid, int(story_id))
        plots.extend(new_plots)
        pictures.extend(new_pictures)
    encoder = getattr(ctx, 'codec_encode', None) or encode
    for cmd, key, rows in ((52027, 'plot_info', plots), (52011, 'inbetweening_info', pictures)):
        if rows:
            payload = encoder(f'sc_{cmd}', {key: rows})
            if not payload:
                raise ValueError(f'剧情阅读的必要图鉴通知 sc_{cmd} 为空')
            _push_frame(ctx, DownFrame(cmd, payload))
