"""Extract reviewed achievement fields and item classifications from native Lua."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONDITIONS = frozenset((300005, 300006, 300013, 300014))


def export():
    sources = {}

    def blocks(relative):
        raw = (ROOT / relative).read_bytes()
        sources[relative] = hashlib.sha256(raw).hexdigest()
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
        return [(int(m[1]), m[2], text.count('\n', 0, m.start()) + 1)
                for m in re.finditer(r'^\t\[(\d+)\] = \{\n(.*?)^\t\},?$', text, re.M | re.S)]

    def scalar(block, field):
        return int(re.search(r'\b' + field + r' = (-?\d+)', block)[1])

    achievements = {}
    for aid, block, line in blocks('decompiled_v2/x64/game/config/achievementcfg.lua'):
        condition = scalar(block, 'condition')
        if condition not in CONDITIONS:
            continue
        parameters = re.search(r'\bparameter = \{(.*?)\}', block, re.S)[1]
        achievements[str(aid)] = dict(id=aid, condition=condition, need=scalar(block, 'need'),
            parameter=list(map(int, re.findall(r'-?\d+', parameters))), line=line)
    items = {}
    for iid, block, line in blocks('decompiled_v2/x64/game/config/rogueteam/rogueteamitemcfg.lua'):
        kind = scalar(block, 'type')
        if kind in (3, 4):
            items[str(iid)] = dict(type=kind, sub_type=scalar(block, 'sub_type'), line=line)
    relative = 'decompiled_v2/x64/game/const/challengerogueteamconst.lua'
    raw = (ROOT / relative).read_bytes()
    sources[relative] = hashlib.sha256(raw).hexdigest()
    constants = re.search(r'\bITEM_TYPE = \{(.*?)\}', raw.decode('utf-8-sig'), re.S)[1]
    assert scalar(constants, 'RELIC') == 3 and scalar(constants, 'TREASURE') == 4
    return dict(source=sources, achievements=achievements, items=items)


if __name__ == '__main__':
    target = ROOT / 'v5_server/rogueteam_achievement_contract.json'
    if target.exists():
        raise SystemExit('Existing contract: compare export() without overwriting it.')
    contract = export()
    target.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(achievements=len(contract['achievements']), items=len(contract['items']),
                         source=contract['source']), ensure_ascii=True))
