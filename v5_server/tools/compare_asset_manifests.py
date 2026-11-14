"""Compare registered native main manifests without opening CDN asset files."""
import argparse
from itertools import combinations
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cdn_integrity as integrity
import resource_manifests as manifests


def load_main(source):
    filename = source['assethash']
    path = manifests._source_file(source, filename)
    with path.open('rb') as handle:
        raw = handle.read(integrity.MAX_MANIFEST_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError(f'清单摘要与原生登记不一致：{path}')
    data = integrity.decode_manifest(raw, filename)
    if (str(data['appVersion']), str(data['buildCode'])) != (str(source['app_version']), str(source['resource_version'])):
        raise ValueError(f'清单版本与原生登记不一致：{path}')
    paths = integrity.manifest_items(data)
    return paths, set(paths.values())


def compare(platforms, indexes):
    path_maps, object_sets = zip(*(indexes[platform] for platform in platforms))
    shared_paths = set.intersection(*(set(paths) for paths in path_maps))
    identical_paths = sum(all(paths[name] == path_maps[0][name] for paths in path_maps[1:])
                          for name in shared_paths)
    common_objects = set.intersection(*object_sets)
    common_bytes = sum(size for _, size in common_objects)
    union_objects = set.union(*object_sets)
    coverage = {}
    for platform, objects in zip(platforms, object_sets):
        total_bytes = sum(size for _, size in objects)
        coverage[platform] = {
            'objects_pct': round(100 * len(common_objects) / len(objects), 2) if objects else None,
            'bytes_pct': round(100 * common_bytes / total_bytes, 2) if total_bytes else None,
        }
    return {
        'platforms': list(platforms),
        'shared_paths': len(shared_paths),
        'identical_paths': identical_paths,
        'shared_paths_with_different_content': len(shared_paths) - identical_paths,
        'shared_objects': len(common_objects),
        'union_objects': len(union_objects),
        'shared_bytes': common_bytes,
        'coverage': coverage,
    }


def compare_builds(builds):
    bundled = manifests._read_json(manifests.NATIVE_PATH, {'sources': []})['sources']
    registered = manifests._state().get('native_sources', {}).values()
    sources = {row['id']: row for row in (*bundled, *registered)
               if row['provenance'] in ('bundled-official', 'official-download')}
    results = {}
    for build in builds:
        indexes, inputs = {}, {}
        candidates = [row for row in sources.values() if str(row['resource_version']) == build]
        for platform in manifests.PLATFORMS:
            matches = [row for row in candidates if row['platform'] == platform]
            # A catalogue may contain two snapshots with the same main manifest
            # and different voice files. Main content is identical in that case.
            by_digest = {row['sha256']: row for row in matches}
            if len(by_digest) > 1:
                raise ValueError(f'Build {build} {platform} 有多个不同的原生主清单，请先确认来源')
            if not by_digest:
                continue
            source = next(iter(by_digest.values()))
            paths, objects = indexes[platform] = load_main(source)
            inputs[platform] = {
                'profile_id': source['id'], 'filename': source['assethash'], 'sha256': source['sha256'],
                'paths': len(paths), 'objects': len(objects), 'bytes': sum(size for _, size in objects),
            }
        if len(indexes) < 2:
            raise ValueError(f'Build {build} 至少需要两个渠道的原生主清单')
        results[build] = {
            'inputs': inputs,
            'pairs': [compare(pair, indexes) for pair in combinations(indexes, 2)],
            'all_platforms': compare(tuple(indexes), indexes) if len(indexes) > 2 else None,
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='append', help='Resource build to compare (repeatable)')
    parser.add_argument('--output', type=Path, help='Write UTF-8 JSON to a new file instead of stdout')
    args = parser.parse_args()
    try:
        report = compare_builds(args.build or ['229', '321', '332'])
        result = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(result + '\n')
        else:
            print(result)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
