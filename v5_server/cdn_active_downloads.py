"""Active download policy and adjustable task window; passive CDN is unaffected."""
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


def validate_concurrency(value):
    if type(value) is not int or not MIN_CONCURRENCY <= value <= MAX_CONCURRENCY:
        raise ValueError(f'主动下载并发必须为 {MIN_CONCURRENCY}–{MAX_CONCURRENCY} 之间的整数')
    return value


class DownloadSettings:
    def __init__(self, path=None):
        self.path = os.path.abspath(path or SETTINGS_PATH)
        self._lock = threading.RLock()
        self._limit = DEFAULT_CONCURRENCY
        self._warning = ''
        if os.path.isfile(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8-sig') as handle:
                    data = json.load(handle)
                self._limit = validate_concurrency(data['max_concurrency'])
            except (OSError, ValueError, KeyError, TypeError):
                self._warning = '本地下载配置读取失败，当前使用默认并发'

    def limit(self):
        with self._lock:
            return self._limit

    def status(self):
        with self._lock:
            return dict(max_concurrency=self._limit, default_concurrency=DEFAULT_CONCURRENCY,
                        min_concurrency=MIN_CONCURRENCY, max_allowed=MAX_CONCURRENCY,
                        warning=self._warning)

    def save(self, value):
        value = validate_concurrency(value)
        with self._lock:
            # Persist before making the new setting visible to running batches.
            integrity.publish_json(self.path, {'version': 1, 'max_concurrency': value})
            self._limit = value
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
