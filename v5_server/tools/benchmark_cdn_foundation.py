"""Bounded offline throughput/cache probe. Keeps 128 MiB of fixture files."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
import uuid

sys.dont_write_bytecode = True
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import cdn_integrity as core
import cdn_proxy as cdn

root = SERVER.parent / 'archive/cdn_foundation_repair_20261027' / ('performance_' + uuid.uuid4().hex)
root.mkdir(parents=True)
block = bytes(range(256)) * 2048
blocks = 128
size = len(block) * blocks
digest = hashlib.md5()
for _ in range(blocks): digest.update(block)
spec = core.ObjectSpec(digest.hexdigest() + '.ys', size)

class Conn:
    def close(self): pass

class MemoryResponse:
    status = 200
    def __init__(self): self.remaining = blocks
    def getheader(self, name): return str(size) if name.lower() == 'content-length' else None
    def read(self, n):
        if not self.remaining: return b''
        self.remaining -= 1
        return block

started = time.perf_counter()
with open(root / 'baseline.bin', 'xb') as handle:
    for _ in range(blocks): handle.write(block)
    handle.flush()
    os.fsync(handle.fileno())
baseline_write = time.perf_counter() - started
started = time.perf_counter()
target = core.ensure_file(spec, root, lambda *_: (Conn(), MemoryResponse()))
verified_write = time.perf_counter() - started
checks = []
for _ in range(300):
    started = time.perf_counter()
    assert core.valid_file(target, core.ObjectSpec(spec.filename))
    checks.append((time.perf_counter() - started) * 1000)

def consume_bare():
    count = 0
    with open(target, 'rb') as handle:
        while True:
            data = handle.read(cdn.LOCAL_CHUNK_SIZE)
            if not data: return count
            count += len(data)

def consume_cdn():
    response = cdn.serve_local_stream(target)
    try: return sum(len(chunk) for chunk in response.response)
    finally: response.close()

bare, served = [], []
for _ in range(3):
    started = time.perf_counter(); assert consume_bare() == size
    bare.append(time.perf_counter() - started)
    started = time.perf_counter(); assert consume_cdn() == size
    served.append(time.perf_counter() - started)
result = dict(fixture_mib=size / 1024 ** 2, baseline_write_fsync_seconds=baseline_write,
    verified_receive_fsync_disk_hash_seconds=verified_write,
    cold_extra_seconds=verified_write - baseline_write,
    cold_verified_mib_s=size / 1024 ** 2 / verified_write,
    warm_validation_median_ms=statistics.median(checks), warm_validation_max_ms=max(checks),
    bare_stream_mib_s=size / 1024 ** 2 / statistics.median(bare),
    warm_cdn_stream_mib_s=size / 1024 ** 2 / statistics.median(served),
    stream_median_overhead_ms=(statistics.median(served) - statistics.median(bare)) * 1000,
    boundary='Local memory/disk only; no real CDN network, no end-to-end speed claim')
(root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
print(root)
