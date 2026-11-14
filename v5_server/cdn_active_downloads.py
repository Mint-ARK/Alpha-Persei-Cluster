"""Active download policy and adjustable task window; passive CDN is unaffected."""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
import threading
import time

import cdn_integrity as integrity

DEFAULT_CONCURRENCY = 32
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 128
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cdn_download_settings.json')
MAX_BANDWIDTH_KIBPS = 2**31 - 1
_UNSET = object()


def validate_concurrency(value):
    if type(value) is not int or not MIN_CONCURRENCY <= value <= MAX_CONCURRENCY:
        raise ValueError(f'主动下载并发必须为 {MIN_CONCURRENCY}–{MAX_CONCURRENCY} 之间的整数')
    return value


def validate_bandwidth(value):
    if type(value) is not int or not 0 <= value <= MAX_BANDWIDTH_KIBPS:
        raise ValueError(f'主动下载总限速必须为 0–{MAX_BANDWIDTH_KIBPS} KiB/s 的整数')
    return value


class _BandwidthLimiter:
    """Share a short burst budget and grant reads in arrival order."""

    def __init__(self):
        self._lock = threading.Lock()
        self._waiting = deque()
        self._rate = 0
        self._generation = 0
        self._available = 0.0
        self._updated = time.monotonic()

    def _wake_next(self):
        if self._waiting:
            self._waiting[0].set()

    def set_rate(self, rate):
        with self._lock:
            if rate != self._rate:
                self._rate = rate
                self._generation += 1
                self._available = 0.0
                self._updated = time.monotonic()
                self._wake_next()

    def _replenish(self, returned=0):
        now = time.monotonic()
        self._available = min(self._rate // 20,
                              self._available + (now - self._updated) * self._rate + returned)
        self._updated = now

    def reserve(self, maximum, cancel):
        with self._lock:
            if not self._rate:
                return maximum, None
            ready = threading.Event()
            self._waiting.append(ready)
        try:
            while True:
                integrity.checkpoint(cancel)
                delay = 0.1
                with self._lock:
                    if not self._rate:
                        return maximum, None
                    if self._waiting[0] is ready:
                        minimum = min(maximum, self._rate // 100)
                        self._replenish()
                        if self._available >= minimum:
                            size = min(maximum, int(self._available))
                            self._available -= size
                            return size, self._generation
                        delay = min(delay, (minimum - self._available) / self._rate)
                    ready.clear()
                ready.wait(delay)
        finally:
            with self._lock:
                was_first = self._waiting[0] is ready
                self._waiting.remove(ready)
                if was_first:
                    self._wake_next()

    def refund(self, unused, generation):
        if not unused or generation is None:
            return
        with self._lock:
            if generation == self._generation:
                self._replenish(unused)
                self._wake_next()


class DownloadSettings:
    def __init__(self, path=None):
        self.path = os.path.abspath(path or SETTINGS_PATH)
        self._lock = threading.RLock()
        self._limit = DEFAULT_CONCURRENCY
        self._bandwidth_kibps = 0
        self.bandwidth = _BandwidthLimiter()
        self._warning = ''
        if os.path.isfile(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8-sig') as handle:
                    data = json.load(handle)
                concurrency = validate_concurrency(data['max_concurrency'])
                self._bandwidth_kibps = validate_bandwidth(data.get('max_bandwidth_kibps', 0))
                self._limit = concurrency
                self.bandwidth.set_rate(self._bandwidth_kibps * 1024)
            except (OSError, ValueError, KeyError, TypeError):
                self._warning = '本地下载配置读取失败，当前使用默认设置'

    def limit(self):
        with self._lock:
            return self._limit

    def status(self):
        with self._lock:
            return dict(max_concurrency=self._limit, default_concurrency=DEFAULT_CONCURRENCY,
                        min_concurrency=MIN_CONCURRENCY, max_allowed=MAX_CONCURRENCY,
                        max_bandwidth_kibps=self._bandwidth_kibps, warning=self._warning)

    def save(self, max_concurrency=_UNSET, *, max_bandwidth_kibps=_UNSET):
        with self._lock:
            concurrency = self._limit if max_concurrency is _UNSET else validate_concurrency(max_concurrency)
            bandwidth = (self._bandwidth_kibps if max_bandwidth_kibps is _UNSET
                         else validate_bandwidth(max_bandwidth_kibps))
            # Persist before making the new setting visible to running batches.
            integrity.publish_json(self.path, dict(version=1, max_concurrency=concurrency,
                                                   max_bandwidth_kibps=bandwidth))
            self._limit = concurrency
            self._bandwidth_kibps = bandwidth
            self.bandwidth.set_rate(bandwidth * 1024)
            self._warning = ''
            return self.status()


settings = DownloadSettings()


def run_batch(items, worker, completed, *, cancel, deadline, get_limit=None, on_window=None):
    from cdn_transfer_control import gate, bind
    with gate.active(cancel, deadline) as token:
        def bound_worker(item):
            with bind(token):
                return worker(item)
        return _run_batch(items, bound_worker, completed, cancel=token, deadline=deadline,
                          get_limit=get_limit, on_window=on_window)


def _run_batch(items, worker, completed, *, cancel, deadline, get_limit=None, on_window=None):
    """Bounded submission, live resizing, and a full drain on stop/failure.

    Reducing the window never interrupts an existing object: it withholds new
    submissions until in-flight work falls below the new limit. Nothing here
    acquires a global permit for passive requests.
    """
    get_limit = get_limit or settings.limit
    iterator = iter(items)
    pending = {}
    exhausted = False
    last_window = None

    def report():
        nonlocal last_window
        window = (len(pending), validate_concurrency(get_limit()))
        if on_window and window != last_window:
            on_window(*window)
        last_window = window

    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix='cdn-active') as pool:
            try:
                while pending or not exhausted:
                    integrity.checkpoint(cancel, deadline)
                    while not exhausted and len(pending) < validate_concurrency(get_limit()):
                        integrity.checkpoint(cancel, deadline)
                        try:
                            item = next(iterator)
                        except StopIteration:
                            exhausted = True
                            break
                        future = pool.submit(worker, item)
                        pending[future] = item
                    report()
                    if not pending:
                        break
                    done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future)
                        result = future.result()
                        integrity.checkpoint(cancel, deadline)
                        completed(result)
                    report()
            except BaseException:
                # Stop outstanding receives, then wait for the executor to exit.
                # The original exception is preserved for the owning task state.
                cancel.set()
                for future in pending:
                    future.cancel()
                raise
    finally:
        # Report zero only after every running worker has exited.
        if on_window:
            on_window(0, validate_concurrency(get_limit()))
