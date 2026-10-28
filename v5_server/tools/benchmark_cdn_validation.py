"""Offline validation concurrency sample; parameters are NOT download limits."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import uuid
from unittest.mock import patch

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import cdn_integrity as core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=13, help='Concurrent probe requests')
    parser.add_argument('--size-mib', type=int, default=64, help='Size of the one shared fixture')
    args = parser.parse_args()
    if not 1 <= args.requests <= 128 or not 1 <= args.size_mib <= 256:
        parser.error('Probe budget: 1..128 requests, 1..256 MiB')
    root = SERVER.parent / 'archive/cdn_validation_singleflight_20261027' / ('performance_' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    block = bytes(range(256)) * 2048
    digest = hashlib.md5()
    for _ in range(args.size_mib * 2):
        digest.update(block)
    spec = core.ObjectSpec(digest.hexdigest() + '.ys', args.size_mib * 1024 ** 2)
    path = root / spec.filename
    with path.open('xb') as handle:
        for _ in range(args.size_mib * 2):
            handle.write(block)

    def run(function):
        gate = threading.Barrier(args.requests + 1)
        def request_file():
            gate.wait(10)
            return function()
        with ThreadPoolExecutor(max_workers=args.requests) as pool:
            jobs = [pool.submit(request_file) for _ in range(args.requests)]
            started = time.perf_counter()
            gate.wait(10)
            assert all(job.result(timeout=30) for job in jobs)
            return time.perf_counter() - started

    def independent_hash():
        digest = hashlib.md5()
        with path.open('rb') as handle:
            while True:
                chunk = handle.read(core.CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest() == spec.md5

    reference = run(independent_hash)
    actual = core._validate_uncached_file
    hashes = []
    def counted(*args):
        hashes.append(1)
        return actual(*args)
    with patch.object(core, '_validate_uncached_file', side_effect=counted):
        cold = run(lambda: core.valid_file(path, spec))
        cold_hashes = len(hashes)
        warm = run(lambda: core.valid_file(path, spec))
        warm_hashes = len(hashes) - cold_hashes
    assert cold_hashes == 1 and warm_hashes == 0
    result = dict(requests=args.requests, fixture_mib=args.size_mib,
        independent_reference_seconds=reference,
        cold_shared_validation_seconds=cold, cold_hash_passes=cold_hashes,
        warm_batch_seconds=warm, warm_hash_passes=warm_hashes,
        reference_hash_read_mib=args.requests * args.size_mib,
        shared_hash_read_mib=args.size_mib,
        boundary='Local disk/CPU; independent MD5 reference, not a network or old-server benchmark')
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    print(root)


if __name__ == '__main__':
    main()
