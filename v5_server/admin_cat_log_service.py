"""Native-valid exploration narrative logs and non-destructive legacy projection."""
from functools import lru_cache
import json
from pathlib import Path
import random


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('admin_cat_log_cfg.json').read_text('utf-8'))['events']


def choices():
    rows=catalog()
    address=sorted(int(i) for i,r in rows.items() if r['event_type']==1)
    content=sorted(int(i) for i,r in rows.items() if r['event_type'] in (2,3))
    if not address or not content:raise ValueError('管理喵探索日志目录缺失')
    return address,content


def choose_ids():
    address,content=choices()
    return random.choice(address),random.choice(content)


def visible_events(events,duration):
    address,content=map(set,choices())
    if not isinstance(events,list):return []
    return [dict(row) for row in events if isinstance(row,dict)
            and type(row.get('address_id'))is int and row['address_id'] in address
            and type(row.get('content_id'))is int and row['content_id'] in content
            and type(row.get('time'))is int and 0<=row['time']<=min(duration,0xffffffff)]
