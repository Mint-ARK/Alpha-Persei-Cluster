"""Normalize the read-only native TextAssets extracted into the evidence folder."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FIELDS=['typeID','status','rotationY','banDir','banDirIsHide','enterParam','exitParam','startMoveParam','endMoveParam',
        'thingTypeID','thingRotationY','thingEnterParam','thingExitParam','thingStartMoveParam','thingEndMoveParam']


def build(source):
    maps={};provenance=json.loads((source/'evidence.json').read_text('utf-8'))
    def row(raw):
        return {k:raw[k] for k in FIELDS if k in raw and (any(raw[k]) if isinstance(raw[k],list) else raw[k]!=0)}
    for path in sorted(source.glob('408*.json')):
        raw=path.read_bytes();data=json.loads(raw)
        assert str(data['id'])==path.stem
        maps[path.stem]=dict(width=data['width'],height=data['height'],
            tiles={str(r['x'])+','+str(r['z']):row(r) for r in data['mapInfo']},
            extendInfo=[row(r) for r in data['extendInfo'] or []],extendThingInfo=[row(r) for r in data['extendThingInfo'] or []],
            source_sha256=hashlib.sha256(raw).hexdigest())
    assert len(maps)==13
    return dict(bundle='combattle/map/chessboard.ys',bundle_hash='d2523a220497431f722433745600837a',
                bundle_sha256=provenance['source_sha256'],resource_versions=[229,311,321],maps=maps)


if __name__=='__main__':
    path=ROOT/'v5_server/data/summer_board_maps.json'
    text=json.dumps(build(ROOT/'archive/summer_board_movement_20261105/native_maps'),ensure_ascii=False,separators=(',',':'))+'\n'
    if path.exists() and path.read_text('utf-8')!=text:raise SystemExit('Back up the previous map contract first')
    path.write_text(text,'utf-8');print('Exported13 native maps with all grid and object triggers')
