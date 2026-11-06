"""Export registered Rogue energy/single-cast kill records; no client writes."""
import hashlib
import json
from pathlib import Path
import re

from export_enemy_sweep_config import native_tables

ROOT = Path(__file__).resolve().parents[2]


def fields(raw):
    out, pos = {}, 0

    def varint():
        nonlocal pos
        value = shift = 0
        while True:
            byte = raw[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7

    while pos < len(raw):
        tag = varint()
        if tag & 7 == 0:
            out[tag >> 3] = varint()
        else:
            assert tag & 7 == 2
            size = varint()
            out[tag >> 3] = raw[pos:pos + size].decode('utf-8')
            pos += size
    return out


def export():
    native = ROOT / 'archive/enemy_illustration_20261023/native_config.bytes'
    registry = native_tables(native)['battle_record_data_type']
    records = {}
    for key in (80, 88):
        row = fields(registry[key])
        assert row[1] == row[2] == key
        records[str(key)] = dict(id=key, type_id=row[2], name=row[3])
    assert records['80']['name'] == '新多维大招充能继承用'
    assert records['88']['name'] == '1次流派大招击杀数'
    dump = ROOT / 'dump.cs'
    source = dump.read_text(encoding='utf-8-sig')
    block = re.search(r'^public sealed class BattleResultInfo :.*?^}', source, re.M | re.S)[0]
    wire = {key: int(value) for key, value in re.findall(r'public const int (\w+)FieldNumber = (\d+);', block)}
    assert wire['RecordDataID'] == 14 and wire['RecordDataNumber'] == 15
    assert 'RepeatedField<int> RecordDataID' in block and 'RepeatedField<int> RecordDataNumber' in block
    achievement = ROOT / 'decompiled_v2/x64/game/config/achievementcfg.lua'
    text = achievement.read_text(encoding='utf-8-sig')
    block = re.search(r'^\t\[63016\] = \{\n(.*?)^\t\},', text.replace('\r\n', '\n'), re.M | re.S)[1]
    scalar = lambda key: int(re.search(r'\b' + key + r' = (\d+)', block)[1])
    params = list(map(int, re.findall(r'\d+', re.search(r'\bparameter = \{(.*?)\}', block, re.S)[1])))
    assert scalar('condition') == 300012 and params == [0, 0, 5]
    sources = [native, dump, achievement,
        ROOT / 'decompiled_v2/x64/game/battle/template/battlechallengerogueteamtemplate.lua',
        ROOT / 'decompiled_v2/x64/game/action/challengerogueteamaction.lua']
    return dict(records=records, wire=dict(record_ids=wire['RecordDataID'], record_numbers=wire['RecordDataNumber']),
        achievement=dict(id=63016, condition=300012, need=scalar('need'), parameter=params),
        source={str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources})


if __name__ == '__main__':
    path = ROOT / 'v5_server/rogueteam_battle_facts_contract.json'
    if path.exists():
        raise SystemExit('Existing contract: compare export() without overwriting.')
    result = export()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('records', 'wire', 'achievement')}, ensure_ascii=False))
