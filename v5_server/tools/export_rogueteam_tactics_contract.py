"""Native tactic camps, six-program threshold and acquisition achievements."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def export():
    sources = {}

    def read(relative):
        raw = (ROOT / relative).read_bytes()
        sources[relative] = hashlib.sha256(raw).hexdigest()
        return raw.decode('utf-8-sig').replace('\r\n', '\n')

    def blocks(text):
        for m in re.finditer(r'^\t\[(\d+)\] = \{\n(.*?)^\t\},?$', text, re.M | re.S):
            yield int(m[1]), m[2], text.count('\n', 0, m.start()) + 1

    def scalar(block, key):
        return int(re.search(r'\b' + key + r' = (-?\d+)', block)[1])

    def numbers(block, key):
        value = re.search(r'\b' + key + r' = \{(.*?)\}', block, re.S)
        return list(map(int, re.findall(r'-?\d+', value[1]))) if value else []

    programs, tactics, achievements = {}, {}, {}
    for iid, block, line in blocks(read('decompiled_v2/x64/game/config/rogueteam/rogueteamitemcfg.lua')):
        kind = scalar(block, 'type')
        if kind not in (4, 5):
            continue
        row = dict(id=iid, type=kind, template=scalar(block, 'item_temp'),
                   camps=numbers(block, 'camp'), line=line)
        if kind == 4:
            programs[str(iid)] = row
        else:
            assert len(row['camps']) == 1
            tactics[str(row['camps'][0])] = row
    for aid, block, line in blocks(read('decompiled_v2/x64/game/config/achievementcfg.lua')):
        if scalar(block, 'condition') == 300009:
            achievements[str(aid)] = dict(id=aid, condition=300009, need=scalar(block, 'need'),
                                          parameter=numbers(block, 'parameter'), line=line)
    constants = read('decompiled_v2/x64/game/const/challengerogueteamconst.lua')
    threshold = scalar(constants, 'SUIT_SKILL_NEED_TREASURE_CNT')
    read('decompiled_v2/x64/game/tools/challengerogueteamtools.lua')
    read('decompiled_v2/x64/game/data/rogueteam/challengerogueteamsuitskilldata.lua')
    read('decompiled_v2/x64/game/action/challengerogueteamaction.lua')
    return dict(source=sources, threshold=threshold, programs=programs,
                tactics=tactics, achievements=achievements)


if __name__ == '__main__':
    path = ROOT / 'v5_server/rogueteam_tactics_contract.json'
    if path.exists():
        raise SystemExit('Existing contract: compare export() instead of overwriting.')
    contract = export()
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(threshold=contract['threshold'], programs=len(contract['programs']),
                         tactics=contract['tactics'], achievements=contract['achievements']), ensure_ascii=True))
