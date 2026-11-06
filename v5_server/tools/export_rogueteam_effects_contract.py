"""Reproduce the bounded contract from read-only native Lua tables.

This exports facts, not a Lua evaluator. Unsupported nested effect params are
retained only as scalar tokens; the runtime executes flat, reviewed actions.
The client does not publish server drop-pool membership or draw weights.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANAGED = (1310002, 1310003, 1310015, 1310016, 1310022)


def export():
    source = {}
    tables = {}
    for kind in ('item', 'effect'):
        relative = f'decompiled_v2/x64/game/config/rogueteam/rogueteam{kind}cfg.lua'
        raw = (ROOT / relative).read_bytes()
        source[relative] = hashlib.sha256(raw).hexdigest()
        tables[kind] = {int(m[1]): m[2] for m in re.finditer(
            r'^\t\[(\d+)\] = \{\n(.*?)^\t\},?$', raw.decode('utf-8-sig').replace('\r\n', '\n'), re.M | re.S)}

    def scalar(block, field):
        return int(re.search(r'\b' + field + r' = (-?\d+)', block)[1])

    def numbers(block, field):
        return list(map(int, re.findall(r'-?\d+', re.search(field + r' = \{(.*?)\}', block, re.S)[1])))

    items = {}
    for iid, block in tables['item'].items():
        if scalar(block, 'type') != 3 or scalar(block, 'sub_type') != 1:
            continue
        items[str(iid)] = {field: re.search(field + r' = "(.*?)"', block)[1] for field in ('name', 'desc')}
        items[str(iid)]['effect_id_list'] = numbers(block, 'effect_id_list')
    effects = {}
    for item in items.values():
        for eid in item['effect_id_list']:
            block = tables['effect'][eid]
            effects[str(eid)] = {field: scalar(block, field) for field in ('id', 'moment', 'gap', 'action', 'value_type')}
            effects[str(eid)]['params'] = numbers(block, 'params')
    return dict(source=source, items={str(i): items[str(i)] for i in MANAGED},
                effects={str(e): effects[str(e)] for i in MANAGED for e in items[str(i)]['effect_id_list']},
                acquisition_items=items, acquisition_effects=effects,
                pool301_policy='Local uniform draw from native type=3/sub_type=1, excluding owned IDs. '
                               'Native server pool membership and weights are unavailable.')


if __name__ == '__main__':
    print(json.dumps(export(), ensure_ascii=False, indent=2))
