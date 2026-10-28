"""Shared CDN object validation and durable publication. No network or DB at import.

Incomplete attempts are retained as unique .part files. A final pathname is only
changed after validating the bytes read back from disk. Retries restart at zero;
unverified partial files are never appended or served.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import weakref

CHUNK_SIZE = 512 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 ** 3
MAX_ATTEMPTS = 3
FREE_RESERVE = 8 * 1024 * 1024
MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
_locks = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()
_verified = OrderedDict()
_verified_guard = threading.Lock()
_validation_flights = {}
_validation_local = threading.local()

# Python 3.10 reports Windows st_ctime as creation time. Use the native change
# timestamp as well, otherwise copy2/utime could preserve size+mtime while
# overwriting a previously verified file.
if os.name == 'nt':
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                    ('write', ctypes.c_longlong), ('change', ctypes.c_longlong),
                    ('attributes', wintypes.DWORD)]

    _file_info = ctypes.WinDLL('kernel32', use_last_error=True).GetFileInformationByHandleEx
    _file_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _file_info.restype = wintypes.BOOL


class IntegrityError(ValueError):
    pass


class Cancelled(InterruptedError):
    pass


@dataclass(frozen=True)
class _InvalidFile:
    # Store only a message, never an exception traceback retaining file handles
    # or large buffers. The entry expires when the file identity changes.
    message: str


def basename(value):
    if (not isinstance(value, str) or not value or len(value) > 200
            or re.search(r'[\\/:\x00-\x1f<>"|?*%]', value)
            or value in ('.', '..') or value.endswith((' ', '.'))
            or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
        raise IntegrityError('Invalid CDN object filename')
    return value


def platform(value):
    if value not in ('pc', 'ios', 'android'):
        raise IntegrityError('Invalid CDN platform')
    return value


def safe_path(root, filename):
    filename = basename(filename)
    root = os.path.realpath(os.fspath(root))
    target = os.path.join(root, filename)
    if os.path.commonpath((root, os.path.realpath(target))) != root:
        raise IntegrityError('CDN object escapes storage root')
    # Do not follow a pre-existing object symlink, even one inside the root.
    if os.path.islink(target):
        raise IntegrityError('CDN object must not be a symlink')
    return target


@dataclass(frozen=True)
class ObjectSpec:
    filename: str
    size: int | None = None
    md5: str | None = None

    def __post_init__(self):
        basename(self.filename)
        if self.size is not None and (type(self.size) is not int or not 0 <= self.size <= MAX_ASSET_BYTES):
            raise IntegrityError('Invalid CDN object size')
        digest = self.md5
        if digest is None:
            match = re.fullmatch(r'([0-9a-fA-F]{32})\.ys', self.filename)
            if match is None:
                match = re.fullmatch(r'assethash_\d+_\d+_([0-9a-fA-F]{32})\.bytes', self.filename)
            if match:
                digest = match[1]
        if digest is not None:
            if not isinstance(digest, str) or not MD5_RE.fullmatch(digest):
                raise IntegrityError('Invalid CDN object digest')
            inferred = re.fullmatch(r'([0-9a-fA-F]{32})\.ys', self.filename)
            if inferred is None:
                inferred = re.fullmatch(r'assethash_\d+_\d+_([0-9a-fA-F]{32})\.bytes', self.filename)
            if inferred and inferred[1].lower() != digest.lower():
                raise IntegrityError('Conflicting CDN object identity')
            object.__setattr__(self, 'md5', digest.lower())
        elif not self.filename.endswith(('.bytes', '.json')):
            raise IntegrityError('Binary CDN object requires a trusted digest')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError('Duplicate JSON key')
        result[key] = value
    return result


def manifest_items(data):
    """The game's actual path|md5|size format, with conflict rejection."""
    if not isinstance(data, dict) or not isinstance(data.get('assetHashList'), list):
        raise IntegrityError('Missing assetHashList')
    by_path, by_hash = {}, {}
    for line in data['assetHashList']:
        if not isinstance(line, str):
            raise IntegrityError('Invalid asset entry')
        parts = line.split('|')
        if len(parts) != 3 or not parts[0] or '\x00' in parts[0] or not re.fullmatch(r'\d+', parts[2]):
            raise IntegrityError('Invalid asset entry fields')
        name, digest, size = parts[0], parts[1].lower(), int(parts[2])
        if not MD5_RE.fullmatch(digest) or size > MAX_ASSET_BYTES:
            raise IntegrityError('Invalid asset identity or size')
        if name in by_path and by_path[name] != (digest, size):
            raise IntegrityError('Conflicting asset path')
        if digest in by_hash and by_hash[digest][1] != size:
            raise IntegrityError('Conflicting asset size')
        by_path[name] = (digest, size)
        by_hash[digest] = (name, size)
    return by_path


def decode_manifest(raw, filename):
    if len(raw) > MAX_MANIFEST_BYTES:
        raise IntegrityError('Manifest exceeds size budget')
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(IntegrityError('Non-finite JSON')))
    except (UnicodeError, ValueError) as error:
        raise IntegrityError('Invalid manifest JSON') from error
    if not isinstance(data, (dict, list)):
        raise IntegrityError('Invalid manifest root')
    if filename.startswith(('assethash_', 'voice_hash_')):
        manifest_items(data)
        asset = re.fullmatch(r'assethash_(\d+)_(\d+)(?:_[0-9a-fA-F]{32})?\.bytes', filename)
        if asset and (str(data.get('appVersion')) != asset[1] or str(data.get('buildCode')) != asset[2]):
            raise IntegrityError('Manifest version does not match filename')
        voice = re.fullmatch(r'voice_hash_(?:ja|zh)_(\d+)\.bytes', filename)
        if voice and str(data.get('version')) != voice[1]:
            raise IntegrityError('Voice version does not match filename')
    elif filename.startswith('voice_package_list_'):
        if not isinstance(data, dict) or not isinstance(data.get('infos'), list):
            raise IntegrityError('Invalid voice package list')
        seen = set()
        package_version = re.fullmatch(r'voice_package_list_(\d+)_(\d+)\.bytes', filename)
        expected_version = str(int(package_version[1] + '00' + package_version[2])) if package_version else None
        for item in data['infos']:
            if (not isinstance(item, dict) or item.get('name') not in ('ja', 'zh')
                    or item['name'] in seen or type(item.get('size')) is not int or item['size'] < 0
                    or not str(item.get('version', '')).isdigit()):
                raise IntegrityError('Invalid voice package entry')
            if expected_version and str(item['version']) != expected_version:
                raise IntegrityError('Voice package version does not match filename')
            seen.add(item['name'])
    return data


def _fingerprint(stat, handle=None):
    change = stat.st_ctime_ns
    if os.name == 'nt' and handle is not None:
        info = _FileBasicInfo()
        native = msvcrt.get_osfhandle(handle.fileno())
        if not _file_info(native, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        change = info.change
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, change)


def _verification_key(path, spec, stat, handle=None):
    # A declared size is checked separately; adding one must not trigger another
    # full hash pass on the same immutable bytes.
    return (os.path.realpath(path), spec.filename, spec.md5, _fingerprint(stat, handle))


def validate_open_file(handle, spec, *, cancel=None):
    checkpoint(cancel)
    before = os.fstat(handle.fileno())
    if spec.size is not None and before.st_size != spec.size:
        raise IntegrityError(f'CDN object length mismatch: expected={spec.size}, actual={before.st_size}')
    if before.st_size > (MAX_ASSET_BYTES if spec.md5 else MAX_MANIFEST_BYTES):
        raise IntegrityError('CDN object exceeds size budget')
    key = _verification_key(handle.name, spec, before, handle)
    with _verified_guard:
        cached = _verified.get(key)
        if cached is not None:
            _verified.move_to_end(key)
            flight, leader = None, False
        else:
            flight = _validation_flights.get(key)
            leader = flight is None
            if leader:
                flight = Future()
                _validation_flights[key] = flight
    if cached is not None:
        if isinstance(cached, _InvalidFile):
            raise IntegrityError(cached.message)
        handle.seek(0)
        return cached
    if not leader:
        if cancel is None:
            actual = flight.result()
        else:
            while True:
                checkpoint(cancel)
                try:
                    actual = flight.result(timeout=.1)
                    break
                except FutureTimeout:
                    continue
        if _fingerprint(os.fstat(handle.fileno()), handle) != key[-1]:
            raise IntegrityError('CDN object changed while waiting for validation')
        handle.seek(0)
        return actual
    previous_cancel = getattr(_validation_local, 'cancel', None)
    _validation_local.cancel = cancel
    try:
        actual = _validate_uncached_file(handle, spec, key)
        with _verified_guard:
            _verified[key] = actual
            if len(_verified) > 16384:
                _verified.popitem(last=False)
        flight.set_result(actual)
        return actual
    except BaseException as error:
        if isinstance(error, IntegrityError):
            with _verified_guard:
                _verified[key] = _InvalidFile(str(error))
                if len(_verified) > 16384:
                    _verified.popitem(last=False)
        # Wake every waiter even on read errors or an unexpected exception.
        # Transient I/O errors are shared with current waiters, not cached.
        flight.set_exception(error)
        raise
    finally:
        _validation_local.cancel = previous_cancel
        with _verified_guard:
            _validation_flights.pop(key, None)


def _validate_uncached_file(handle, spec, key):
    digest = hashlib.md5()
    handle.seek(0)
    while True:
        checkpoint(getattr(_validation_local, 'cancel', None))
        chunk = handle.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
    actual = digest.hexdigest()
    if spec.md5 and actual != spec.md5:
        raise IntegrityError(f'CDN object digest mismatch: expected={spec.md5}, actual={actual}')
    if spec.filename.endswith(('.bytes', '.json')):
        if os.fstat(handle.fileno()).st_size > MAX_MANIFEST_BYTES:
            raise IntegrityError('Manifest exceeds size budget')
        handle.seek(0)
        decode_manifest(handle.read(MAX_MANIFEST_BYTES + 1), spec.filename)
    if _fingerprint(os.fstat(handle.fileno()), handle) != key[-1]:
        raise IntegrityError('CDN object changed during validation')
    handle.seek(0)
    return actual


def valid_file(path, spec):
    try:
        if os.path.islink(path):
            return False
        with open(path, 'rb') as handle:
            validate_open_file(handle, spec)
        return True
    except (OSError, IntegrityError):
        return False


def checkpoint(cancel=None, deadline=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled('CDN task cancelled')
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError('CDN task deadline exceeded')


@contextmanager
def object_lock(path, cancel=None, deadline=None):
    key = os.path.normcase(os.path.realpath(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
    while not lock.acquire(timeout=0.1):
        checkpoint(cancel, deadline)
    handle = None
    acquired = False
    try:
        checkpoint(cancel, deadline)
        os.makedirs(os.path.dirname(key), exist_ok=True)
        handle = open(safe_path(os.path.dirname(key), os.path.basename(key) + '.lock'), 'a+b')
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        while not acquired:
            checkpoint(cancel, deadline)
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if cancel is not None:
                    cancel.wait(0.1)
                else:
                    time.sleep(0.1)
        yield
    finally:
        try:
            if handle:
                try:
                    if acquired:
                        if os.name == 'nt':
                            import msvcrt
                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            lock.release()


def _sync_directory(folder):
    # Windows does not offer directory fsync through os.open. File fsync and
    # atomic replacement still apply; native crash durability needs OS testing.
    if os.name != 'nt':
        fd = os.open(folder, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish(part, destination, spec, cancel, deadline):
    # Check disk contents, not a hash of the network chunks.
    with open(part, 'rb') as handle:
        actual = validate_open_file(handle, spec, cancel=cancel)
        validated_stat = os.fstat(handle.fileno())
        validated_key = _verification_key(part, spec, validated_stat, handle)
    checkpoint(cancel, deadline)
    if os.path.exists(destination):
        backup = destination + '.before_publish_' + uuid.uuid4().hex
        shutil.copy2(destination, backup)
        with open(backup, 'r+b') as handle:
            os.fsync(handle.fileno())
    checkpoint(cancel, deadline)
    os.replace(part, destination)
    _sync_directory(os.path.dirname(destination))
    # Windows path stat may expose a cached creation timestamp after replace;
    # use the same handle-based identity that cache reads use.
    with open(destination, 'rb') as handle:
        final_stat = os.fstat(handle.fileno())
        final_key = _verification_key(destination, spec, final_stat, handle)
    if (final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns) != (
            validated_stat.st_ino, validated_stat.st_size, validated_stat.st_mtime_ns):
        raise IntegrityError('Published object changed unexpectedly')
    # Rename preserves the validated bytes. Adopt the new path/stat identity so
    # the first client does not hash the same large file a second time.
    with _verified_guard:
        _verified.pop(validated_key, None)
        _verified[final_key] = actual
        if len(_verified) > 16384:
            _verified.popitem(last=False)


def _headers(response, spec):
    if response.status != 200:
        if response.status in (408, 425, 429, 500, 502, 503, 504):
            raise OSError(f'Transient upstream HTTP {response.status}')
        raise IntegrityError(f'Full object request returned HTTP {response.status}')
    if response.getheader('Content-Range'):
        raise IntegrityError('Unexpected partial object')
    if (response.getheader('Content-Encoding') or 'identity').strip().lower() != 'identity':
        raise IntegrityError('Unexpected content encoding')
    transfer = response.getheader('Transfer-Encoding')
    length = response.getheader('Content-Length')
    if transfer and (transfer.lower().strip() != 'chunked' or length is not None):
        raise IntegrityError('Ambiguous HTTP framing')
    expected = None
    if length is not None:
        if not re.fullmatch(r'\d+', length):
            raise IntegrityError('Invalid Content-Length')
        expected = int(length)
        if spec.size is not None and expected != spec.size:
            raise IntegrityError('HTTP length differs from blueprint')
    maximum = MAX_ASSET_BYTES if spec.md5 else MAX_MANIFEST_BYTES
    if expected is not None and expected > maximum:
        raise IntegrityError('HTTP object exceeds size budget')
    return expected, maximum


def ensure_file(spec, directory, opener, *, cancel=None, deadline=None,
                max_attempts=MAX_ATTEMPTS, force=False, indexed=False,
                discard_partial=False, progress=None):
    """opener(attempt, timeout) -> (connection, response); caller owns identity.

    Only full identity-encoded GET responses are accepted. A client's Range is
    served locally after verification, never blindly relayed to the upstream.
    """
    deadline = deadline if deadline is not None else time.monotonic() + 7200
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise IntegrityError('Retry limit must be between one and three')
    destination = safe_path(directory, spec.filename)
    with object_lock(destination, cancel, deadline):
        if not indexed and not force and valid_file(destination, spec):
            return destination
        last_error = None
        for attempt in range(max_attempts):
            checkpoint(cancel, deadline)
            conn = None
            detach = None
            part = safe_path(directory, spec.filename + '.' + uuid.uuid4().hex + '.part')
            try:
                conn, response = opener(attempt, max(0.1, min(30, deadline - time.monotonic())))
                if cancel is not None and hasattr(cancel, 'attach'):
                    detach = cancel.attach(conn)
                checkpoint(cancel, deadline)
                expected, maximum = _headers(response, spec)
                needed = spec.size if spec.size is not None else (expected or 0)
                old_size = os.path.getsize(destination) if os.path.isfile(destination) else 0
                if shutil.disk_usage(directory).free < needed + old_size + FREE_RESERVE:
                    raise OSError('Insufficient CDN storage space')
                total = 0
                # read1 returns after one buffered/raw read. A peer trickling
                # bytes cannot hold read(CHUNK_SIZE) open across the job budget.
                read_chunk = getattr(response, 'read1', response.read)
                with open(part, 'xb') as handle:
                    while True:
                        checkpoint(cancel, deadline)
                        chunk = read_chunk(CHUNK_SIZE)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > maximum or (expected is not None and total > expected) or (spec.size is not None and total > spec.size):
                            raise IntegrityError('CDN body exceeds expected length')
                        if handle.write(chunk) != len(chunk):
                            raise OSError('Short CDN file write')
                        if progress:
                            progress(len(chunk))
                        if total % (16 * 1024 * 1024) < len(chunk) and shutil.disk_usage(directory).free < FREE_RESERVE:
                            raise OSError('CDN storage reserve exhausted')
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected is not None and total != expected:
                    raise IntegrityError('Incomplete HTTP body')
                _publish(part, destination, spec, cancel, deadline)
                return destination
            except Cancelled:
                raise
            except (OSError, ValueError, EOFError, http.client.HTTPException) as error:
                checkpoint(cancel, deadline)
                last_error = error
                if isinstance(error, (IntegrityError, TimeoutError)) or (isinstance(error, OSError) and error.errno in (28, 13)):
                    break
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                    upstream_detach = getattr(conn, '_cdn_abort_detach', None)
                    if upstream_detach:
                        upstream_detach()
                if detach:
                    detach()
                if discard_partial and os.path.isfile(part):
                    from cdn_transfer_control import recycle_partial
                    recycle_partial(part)
            if attempt + 1 < max_attempts:
                if cancel is not None:
                    cancel.wait(min(0.25 * (2 ** attempt), max(0, deadline - time.monotonic())))
                else:
                    time.sleep(0.25 * (2 ** attempt))
        raise IntegrityError(f'CDN download failed: {type(last_error).__name__}: {last_error}') from last_error


def copy_verified(source, directory, spec, *, cancel=None, deadline=None):
    """Copy an already available object through the same validation/publication gate."""
    destination = safe_path(directory, spec.filename)
    deadline = deadline if deadline is not None else time.monotonic() + 7200
    with object_lock(destination, cancel, deadline):
        if valid_file(destination, spec):
            return destination
        part = safe_path(directory, spec.filename + '.' + uuid.uuid4().hex + '.part')
        with open(source, 'rb') as reader:
            validate_open_file(reader, spec)
            with open(part, 'xb') as writer:
                while True:
                    checkpoint(cancel, deadline)
                    chunk = reader.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if writer.write(chunk) != len(chunk):
                        raise OSError('Short CDN copy write')
                writer.flush()
                os.fsync(writer.fileno())
        _publish(part, destination, spec, cancel, deadline)
        return destination


def publish_json(path, data):
    """Small local metadata uses the same backed-up atomic commit discipline."""
    spec = ObjectSpec(os.path.basename(path))
    destination = safe_path(os.path.dirname(os.path.abspath(path)), spec.filename)
    raw = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    decode_manifest(raw, spec.filename)
    with object_lock(destination):
        part = safe_path(os.path.dirname(destination), spec.filename + '.' + uuid.uuid4().hex + '.part')
        with open(part, 'xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _publish(part, destination, spec, None, None)


def publish_bytes(directory, spec, raw):
    """Publish a locally generated manifest through the same disk gate."""
    if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
        raise IntegrityError('Invalid generated manifest size')
    destination = safe_path(directory, spec.filename)
    with object_lock(destination):
        part = safe_path(directory, spec.filename + '.' + uuid.uuid4().hex + '.part')
        with open(part, 'xb') as handle:
            if handle.write(raw) != len(raw):
                raise OSError('Short generated manifest write')
            handle.flush()
            os.fsync(handle.fileno())
        _publish(part, destination, spec, None, None)
    return destination


def load_platform_tasks(plat, data_dir, static_dir):
    """Use the selected version, never unqualified shared platform fallbacks."""
    import res_version_manager
    platform(plat)
    config = res_version_manager.get_version_config()
    filenames = [config['assethash'][plat], *config.get('voice_hashes', {}).values()]
    tasks = {}
    for filename in filenames:
        spec = ObjectSpec(filename)
        source = next((safe_path(root, filename) for root in (data_dir, static_dir)
                       if valid_file(safe_path(root, filename), spec)), None)
        if source is None:
            raise IntegrityError(f'[{plat}] Missing verified manifest: {filename}')
        with open(source, 'rb') as handle:
            data = decode_manifest(handle.read(MAX_MANIFEST_BYTES + 1), filename)
        for name, (digest, size) in manifest_items(data).items():
            if digest in tasks and tasks[digest][1] != size:
                raise IntegrityError('Conflicting blueprint object sizes')
            tasks[digest] = (name, size)
    return tasks
