"""Build the panel's item catalogue and WebP icons from an installed Windows client."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re
import warnings

import UnityPy
import UnityPy.config

from extract_autochess_cfg import parse_lua_file
from unified_asset_extractor import setup_ljd_env


CONFIGS = {'itemcfg', 'itemcfg2', 'drawpoolcfg', 'spritepathcfg'}
FRAMES = ('Item_box', 'Item_box', 'Item_green', 'Item_blue', 'Item_purple', 'Item_yellow')


def records(table):
    return {row['id']: row for row in table.values() if isinstance(row, dict) and type(row.get('id')) is int}


def load_bundle(root, entry):
    digest, size = entry
    path = root / digest[0] / digest[1] / (digest + '.ys')
    data = path.read_bytes()
    if len(data) != int(size) or hashlib.md5(data).hexdigest() != digest:
        raise ValueError(f'Bundle failed verification: {path}')
    return UnityPy.load(data[data.index(b'UnityFS', 0, 64):])


def extract_configs(root, index, out):
    if not setup_ljd_env():
        raise RuntimeError('LuaJIT decompiler is unavailable')
    from ljd.tools import process_file

    configs = {}
    for name, obj in load_bundle(root, index['scripts64']).container.items():
        key = Path(name.lower().removesuffix('.bytes')).stem
        if key not in CONFIGS:
            continue
        bytecode = out / (key + '.luac')
        source = out / (key + '.lua')
        bytecode.write_bytes(obj.read().m_Script.encode('utf-8', 'surrogateescape'))
        with redirect_stdout(io.StringIO()):
            process_file(str(bytecode), str(source))
        configs[key] = parse_lua_file(source)
    if configs.keys() != CONFIGS:
        raise ValueError(f'Missing client configs: {CONFIGS - configs.keys()}')
    return configs


def icon_paths(item, paths):
    icon = item.get('icon')
    if not icon or not re.fullmatch(r'[\w@.$-]+', icon):
        return []
    # ItemTools.getItemTexturePath selects the namespace before using ItemCfg.icon.
    kind = item['type']
    texture = {2: 'HeroLittleIcon', 3: 'HeroPiece', 7: 'EquipIcon_l',
               8: 'HeroLittleIcon', 9: 'ServantIcon'}.get(kind, 'ItemIcon')
    if kind == 11 and item.get('sub_type') == 1:
        texture = 'HeroMediumIcon'
    directory = 'TextureConfig/Sticker/Sticker/' if kind == 13 else paths[texture]['path']
    stem = (directory + icon).lower()
    localized = stem + '@zh_cn.ys'
    return [stem + '.ys', localized] if kind == 13 else [localized, stem + '.ys']


def export_icon(root, index, out, logical):
    digest, _ = index[logical]
    target = out / (digest + '.webp')
    try:
        bundle = load_bundle(root, index[logical])
    except FileNotFoundError:
        return ''
    suffix = '/' + logical.removesuffix('.ys')
    candidates = [obj for name, obj in bundle.container.items()
                  if name.lower().rsplit('.', 1)[0].endswith(suffix)]
    if not candidates and len(bundle.container) == 1:
        # Some localized bundles retain the unqualified image name internally.
        stem = suffix.removesuffix('@zh_cn')
        candidates = [obj for name, obj in bundle.container.items()
                      if name.lower().rsplit('.', 1)[0].endswith(stem)]
    for obj in candidates:
        if obj.type.name in ('Sprite', 'Texture2D'):
            image = obj.read().image
            image.thumbnail((192, 192))
            with target.open('xb') as stream:
                image.save(stream, format='WEBP', quality=88)
            return f'extracted_assets/items/native/{target.name}'
    return ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, required=True, help='Windows directory containing AssetHash_Info.bytes')
    parser.add_argument('--out', type=Path, required=True, help='New staging directory')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    icons = args.out / 'native'
    icons.mkdir()
    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.62f3'
    warnings.filterwarnings('ignore', module='UnityPy')
    manifest = json.loads((args.client / 'AssetHash_Info.bytes').read_bytes())
    index = {name.lower(): (digest, size) for name, digest, size in
             (line.split('|') for line in manifest['assetHashList'])}
    configs = extract_configs(args.client, index, args.out)
    first, second = (records(configs[name]) for name in ('itemcfg', 'itemcfg2'))
    if first.keys() & second.keys():
        raise ValueError('Duplicate item IDs in client config')
    items = first | second
    pools = records(configs['drawpoolcfg'])
    standard = pools[10001]['cost_once'][0]
    if pools[10001]['cost_ten_times'] != [standard, 10]:
        raise ValueError('Unexpected regular draw cost')
    paths = {iid: next((path for path in icon_paths(row, configs['spritepathcfg']) if path in index), '')
             for iid, row in items.items()}
    # The same bundle can serve many item IDs. Decode it once.
    logical_by_digest = {index[path][0]: path for path in sorted(set(paths.values()) - {''})}
    with ThreadPoolExecutor(max_workers=4) as workers:
        images = dict(zip(logical_by_digest, workers.map(
            lambda logical: export_icon(args.client, index, icons, logical), logical_by_digest.values())))
    rows = []
    for iid, item in sorted(items.items()):
        rare = item.get('display_rare', item['rare'])
        icon = images[index[paths[iid]][0]] if paths[iid] else ''
        rows.append(dict(id=iid, name=item.get('name') or '', type=item['type'], rare=rare,
                         icon_file=icon, quality_frame=FRAMES[min(5, max(0, rare))] + '.webp',
                         **({'aliases': ['常规探测凭证', '常驻探测凭证']} if iid == standard else {})))
    with (args.out / 'items_catalog.json').open('x', encoding='utf-8') as stream:
        stream.write('[\n' + ',\n'.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in rows) + '\n]\n')
    print(f'Build {manifest["buildCode"]}: {len(rows)} items, {sum(bool(row["icon_file"]) for row in rows)} with icons; regular draw ID {standard}')


if __name__ == '__main__':
    main()
