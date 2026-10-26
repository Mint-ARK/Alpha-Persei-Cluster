"""Default-open worldview directory and safe client projection."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def valid_ids():
    data = json.loads((Path(__file__).resolve().parent / 'data/worldview_cfg.json').read_text('utf8'))
    ids = frozenset(int(k) for k in data['words'])
    if not ids:
        raise ValueError('Empty worldview directory')
    return ids


def ensure_entries(db, uid):
    """Fill missing/locked defaults, preserving read history and all other kinds."""
    with db.transaction():
        rows = {int(r['item_id']): r for r in db.query(
            "SELECT item_id,is_view,complete_flag FROM illustrated WHERE uid=? AND kind='affix'", (uid,))}
        missing = valid_ids() - rows.keys()
        inactive = [wid for wid in valid_ids() & rows.keys() if rows[wid]['complete_flag'] == 0]
        if missing:
            db.executemany("INSERT INTO illustrated (uid,kind,item_id,is_view,complete_flag) VALUES (?,'affix',?,0,1)",
                           [(uid, wid) for wid in sorted(missing)])
        if inactive:
            db.executemany("UPDATE illustrated SET complete_flag=1 WHERE uid=? AND kind='affix' AND item_id=?",
                           [(uid, wid) for wid in inactive])
        return {'worldview_added': len(missing), 'worldview_unlocked': len(inactive)}


def snapshot(db, uid):
    """Unknown legacy IDs stay in storage but cannot index a missing client row."""
    return [{'id': int(r['item_id']), 'is_view': 1 if r['is_view'] else 0}
            for r in db.query("SELECT item_id,is_view FROM illustrated WHERE uid=? AND kind='affix' ORDER BY item_id", (uid,))
            if int(r['item_id']) in valid_ids()]
