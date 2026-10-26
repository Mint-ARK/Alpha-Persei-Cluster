"""Runtime rules for manual furniture gifts, exported from current client config."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_dorm_gift_config():
    with (Path(__file__).parent / 'dorm_gift_cfg.json').open(encoding='utf-8') as handle:
        data = json.load(handle)
    return {int(fid):int(limit) for fid,limit in data['gifts'].items()}, frozenset(data['private_room_ids'])
