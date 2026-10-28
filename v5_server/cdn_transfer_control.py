"""Server-wide passive-priority transfer sessions, including standalone CLIs.

No player data and no filesystem work at import. A shared OS lock admits passive
readers; an exclusive lock admits one active execution group. Held marker locks
advertise waiting passive readers so an active owner can yield across processes.
"""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import socket
import threading
import time
import uuid

import cdn_integrity as integrity

STATE_ROOT = Path(__file__).resolve().parent / 'runtime' / 'cdn_downloads'
_local = threading.local()

if os.name == 'nt':
    import ctypes
    import msvcrt
    from ctypes import wintypes
    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
                    ('Offset',wintypes.DWORD),('OffsetHigh',wintypes.DWORD),('hEvent',wintypes.HANDLE)]
    _kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    _lock_file=_kernel.LockFileEx
    _lock_file.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(_Overlapped)]
    _lock_file.restype=wintypes.BOOL
    _unlock_file=_kernel.UnlockFileEx
    _unlock_file.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(_Overlapped)]
    _unlock_file.restype=wintypes.BOOL


class FileLease:
    def __init__(self, path, shared=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open('a+b')
        if not os.fstat(self.handle.fileno()).st_size:
            self.handle.write(b'0'); self.handle.flush()
        self.shared = shared
        self.locked = False

    def acquire(self):
        try:
            if os.name == 'nt':
                self._overlapped=_Overlapped()
                # CRT LK_NBRLCK does not provide the shared-reader semantics
                # needed here on Windows. Use an actual shared LockFileEx lock.
                if not _lock_file(msvcrt.get_osfhandle(self.handle.fileno()),1 if self.shared else 3,0,1,0,ctypes.byref(self._overlapped)):
                    error=ctypes.get_last_error()
                    if error==33: return False
                    raise ctypes.WinError(error)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            self.locked = True
            return True
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            return False

    def close(self):
        if self.handle.closed:
            return
        try:
            if self.locked:
                if os.name == 'nt':
                    if not _unlock_file(msvcrt.get_osfhandle(self.handle.fileno()),0,1,0,ctypes.byref(self._overlapped)):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.locked = False
            self.handle.close()


class TransferToken:
    def __init__(self, parent=None):
        self.parent = parent
        self.event = threading.Event()
        self.reason = ''
        self._lock = threading.Lock()
        self._closers = {}
        self._gate = None
        self.discard_partials = True
        self.progress = None

    def is_set(self):
        return self.event.is_set() or bool(self.parent and self.parent.is_set())

    def wait(self, timeout=None):
        return self.event.wait(timeout) or self.is_set()

    def set(self):
        self.cancel('cancelled')

    def cancel(self, reason):
        with self._lock:
            if not self.reason:
                self.reason = reason
            self.event.set()
            closers = list(self._closers.values())
        for close in closers:
            try:
                close()
            except (OSError, ValueError):
                pass

    def attach(self, connection, sock=None):
        parent_detach = self.parent.attach(connection, sock) if hasattr(self.parent, 'attach') else None
        ident = uuid.uuid4().hex
        captured = sock or getattr(connection, 'sock', None)
        def abort():
            if captured is not None:
                try:
                    captured.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
        with self._lock:
            self._closers[ident] = abort
            cancelled = self.is_set()
        if cancelled:
            abort()
        def detach():
            with self._lock:
                self._closers.pop(ident, None)
            if parent_detach:
                parent_detach()
        return detach


def current_token():
    return getattr(_local, 'token', None)


@contextmanager
def bind(token):
    old = current_token()
    _local.token = token
    try:
        yield token
    finally:
        _local.token = old


class PriorityGate:
    def __init__(self, root=None):
        self.root = Path(root or STATE_ROOT) / 'coordination'
        self._condition = threading.Condition()
        self._active = None
        self._passive = 0
        self._marker = None
        self.on_acquired = None
        self._marker_path = self.root / ('passive-' + uuid.uuid4().hex + '.lease')

    def passive_pending(self):
        if not self.root.is_dir():
            return False
        for path in self.root.glob('passive-*.lease'):
            marker = FileLease(path)
            try:
                if not marker.acquire():
                    return True
            finally:
                marker.close()
        return False

    def status(self):
        with self._condition:
            return dict(passive_requests=self._passive, passive_pending=self.passive_pending(),
                        active_group=bool(self._active))

    @contextmanager
    def maintenance(self):
        """Nonblocking exclusive boundary for changing download destinations."""
        token = TransferToken()
        lease = None
        with self._condition:
            if self._active is not None or self._passive or self.passive_pending():
                raise ValueError('资源正在传输或建立索引，请暂停任务并等待在途文件结束后再切换目录')
            self._active = token
        try:
            lease = FileLease(self.root / 'transfer.lease')
            if not lease.acquire() or self.passive_pending():
                raise ValueError('其他进程正在传输资源，请稍后切换目录')
            if self.on_acquired:
                self.on_acquired()
            yield token
        finally:
            if lease:
                lease.close()
            with self._condition:
                if self._active is token:
                    self._active = None
                self._condition.notify_all()

    @contextmanager
    def active(self, cancel=None, deadline=None):
        bound = current_token()
        if bound is not None and bound._gate is self:
            integrity.checkpoint(bound, deadline)
            yield bound
            return
        token = TransferToken(cancel)
        lease = None
        monitor_stop = threading.Event()
        monitor = None
        try:
            while True:
                integrity.checkpoint(cancel, deadline)
                with self._condition:
                    if self._active is None and not self._passive and not self.passive_pending():
                        self._active = token
                        break
                    self._condition.wait(.1)
            lease = FileLease(self.root / 'transfer.lease')
            while not lease.acquire():
                integrity.checkpoint(cancel, deadline)
                if self.passive_pending():
                    token.cancel('passive')
                    raise integrity.Cancelled('Passive transfer takes priority')
                time.sleep(.05)
            if self.passive_pending():
                token.cancel('passive')
                raise integrity.Cancelled('Passive transfer takes priority')
            if self.on_acquired:
                self.on_acquired()
            token._gate = self
            def watch():
                while not monitor_stop.wait(.1):
                    if token.parent and token.parent.is_set():
                        token.cancel('cancelled'); return
                    if self.passive_pending():
                        token.cancel('passive'); return
            monitor = threading.Thread(target=watch, name='cdn-priority', daemon=True)
            monitor.start()
            with bind(token):
                yield token
        finally:
            monitor_stop.set()
            if monitor:
                monitor.join(1)
            if lease:
                lease.close()
            token._gate = None
            with self._condition:
                if self._active is token:
                    self._active = None
                self._condition.notify_all()

    @contextmanager
    def passive(self):
        lease = None
        with self._condition:
            if self._passive == 0:
                self._marker = FileLease(self._marker_path)
                marker_deadline=time.monotonic()+2
                while not self._marker.acquire():
                    # Another process may be briefly probing this idle marker.
                    # Its probe is not an owner and must not reject a client.
                    if time.monotonic()>=marker_deadline:
                        self._marker.close(); self._marker = None
                        raise RuntimeError('Passive priority marker unavailable')
                    time.sleep(.001)
            self._passive += 1
            active = self._active
        if active:
            active.cancel('passive')
        try:
            lease = FileLease(self.root / 'transfer.lease', shared=True)
            while not lease.acquire():
                time.sleep(.05)
            if self.on_acquired:
                self.on_acquired()
            token = TransferToken()
            # Passive scopes are deliberately not reusable as active sessions.
            with bind(token):
                yield token
        finally:
            if lease:
                lease.close()
            with self._condition:
                self._passive -= 1
                if self._passive == 0 and self._marker:
                    self._marker.close(); self._marker = None
                self._condition.notify_all()


gate = PriorityGate()


def recycle_partial(path):
    """Move an owned incomplete object out of the cache, never hard-delete."""
    source = Path(path).resolve()
    if not source.name.endswith('.part') or not source.is_file():
        return
    if os.name == 'nt':
        from save_slot_manager import _recycle_save_files
        _recycle_save_files([source])
    else:
        folder = source.parent / '.recycled-downloads' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        os.replace(source, folder / source.name)
