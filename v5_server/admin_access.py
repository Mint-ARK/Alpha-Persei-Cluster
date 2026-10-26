"""Optional HTTP Basic authentication for the Linux GM console.

The game SDK and the GM console share one Flask application.  This module
adds a small request guard that protects only the console endpoints, leaving
the SDK and ordinary static assets available to the game client.

Authentication is opt-in.  If ``AG_ADMIN_AUTH_FILE`` is unset, installing the
guard does nothing.  Once the variable is set, the configuration is loaded at
application construction time and every malformed configuration fails closed
by raising :class:`AdminAuthConfigError`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import posixpath
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from flask import Response, request


AUTH_FILE_ENV = "AG_ADMIN_AUTH_FILE"
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_DIGEST_BYTES = 32
_MAX_AUTH_HEADER_CHARS = 8192
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_AUTH_CACHE_KEY = secrets.token_bytes(32)
_AUTH_CACHE_LOCK = threading.Lock()
_LAST_SUCCESS_CACHE: Optional[tuple[object, bytes]] = None


class AdminAuthConfigError(RuntimeError):
    """Raised when enabled admin authentication cannot be configured safely."""


@dataclass(frozen=True)
class _AdminAuthConfig:
    username: str
    iterations: int
    salt: bytes
    digest: bytes

    def verify(self, username: str, password: str) -> bool:
        """Verify one decoded Basic credential pair.

        The username comparison and the derived password digest comparison use
        ``compare_digest``.  PBKDF2 is still computed when the username does
        not match, so the normal wrong-username and wrong-password paths have
        the same expensive password operation.
        """

        username_ok = hmac.compare_digest(
            username.encode("utf-8"), self.username.encode("utf-8")
        )
        candidate_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            self.salt,
            self.iterations,
            dklen=len(self.digest),
        )
        password_ok = hmac.compare_digest(candidate_digest, self.digest)
        return username_ok and password_ok


def make_password_hash(password: str) -> str:
    """Create a ``pbkdf2_sha256$iterations$saltHex$digestHex`` value.

    New hashes always use the fixed 600,000 PBKDF2 iterations required by the
    console contract and a fresh 128-bit random salt.
    """

    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    password_bytes = password.encode("utf-8")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password_bytes, salt, PBKDF2_ITERATIONS, dklen=_DIGEST_BYTES
    )
    return (
        f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _invalid_config(reason: str) -> AdminAuthConfigError:
    # Keep errors structural and credential-free.  In particular, never put
    # the parsed JSON or password hash in a startup log/exception message.
    return AdminAuthConfigError(f"invalid admin authentication configuration: {reason}")


def _parse_password_hash(value: Any) -> tuple[int, bytes, bytes]:
    if not isinstance(value, str):
        raise _invalid_config("password_hash must be a string")

    fields = value.split("$")
    if len(fields) != 4 or fields[0] != PBKDF2_ALGORITHM:
        raise _invalid_config("unsupported password_hash format")

    iterations_text, salt_hex, digest_hex = fields[1:]
    if not re.fullmatch(r"[0-9]+", iterations_text):
        raise _invalid_config("invalid PBKDF2 iteration count")
    iterations = int(iterations_text)
    if iterations != PBKDF2_ITERATIONS:
        raise _invalid_config("PBKDF2 iteration count must be 600000")

    # A short or odd salt is a weak/malformed configuration.  Accept a wider
    # range than the helper emits so manually generated, still-strong salts
    # remain usable while rejecting unexpectedly huge values.
    if (
        len(salt_hex) < 32
        or len(salt_hex) > 256
        or len(salt_hex) % 2
        or not _HEX_RE.fullmatch(salt_hex)
    ):
        raise _invalid_config("invalid salt")
    if len(digest_hex) != _DIGEST_BYTES * 2 or not _HEX_RE.fullmatch(digest_hex):
        raise _invalid_config("invalid digest")

    try:
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except ValueError:
        raise _invalid_config("invalid hexadecimal password_hash field") from None
    return iterations, salt, digest


def _load_config(path_value: str) -> _AdminAuthConfig:
    if not isinstance(path_value, str) or not path_value.strip():
        raise _invalid_config("AG_ADMIN_AUTH_FILE must name a file")

    try:
        path = Path(path_value)
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _invalid_config("authentication file is missing or unreadable") from None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError):
        raise _invalid_config("authentication file is not valid JSON") from None
    if not isinstance(data, dict):
        raise _invalid_config("authentication file must contain an object")

    username = data.get("username")
    if not isinstance(username, str) or not username:
        raise _invalid_config("username must be a non-empty string")
    if ":" in username:
        raise _invalid_config("username must not contain a colon")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in username):
        raise _invalid_config("username contains a control character")

    iterations, salt, digest = _parse_password_hash(data.get("password_hash"))
    return _AdminAuthConfig(username, iterations, salt, digest)


def load_credentials(path: str | os.PathLike[str]) -> _AdminAuthConfig:
    """Load and validate console credentials from a JSON configuration file.

    The file may contain unrelated Linux startup settings; only the required
    ``username`` and ``password_hash`` fields are consumed here.  The parsed
    result is the same internal credential object used by
    :func:`install_admin_guard`, so installer checks and runtime startup share
    one fail-closed validator.
    """

    try:
        path_value = os.fspath(path)
    except TypeError:
        raise _invalid_config("authentication file path is invalid") from None
    return _load_config(path_value)


def _authorization_digest(header: Optional[str]) -> Optional[bytes]:
    if not isinstance(header, str) or len(header) > _MAX_AUTH_HEADER_CHARS:
        return None
    try:
        header_bytes = header.encode("utf-8")
    except UnicodeError:
        return None
    return hmac.new(_AUTH_CACHE_KEY, header_bytes, hashlib.sha256).digest()


def _authorization_cache_hit(owner: object, header: Optional[str]) -> bool:
    digest = _authorization_digest(header)
    if digest is None:
        return False
    with _AUTH_CACHE_LOCK:
        cached = _LAST_SUCCESS_CACHE
        if cached is None or cached[0] is not owner:
            return False
        return hmac.compare_digest(digest, cached[1])


def _remember_authorization(owner: object, header: str) -> None:
    digest = _authorization_digest(header)
    if digest is None:
        return
    global _LAST_SUCCESS_CACHE
    with _AUTH_CACHE_LOCK:
        _LAST_SUCCESS_CACHE = (owner, digest)


def _decode_basic_credentials(header: Optional[str]) -> Optional[tuple[str, str]]:
    if not isinstance(header, str) or not header or len(header) > _MAX_AUTH_HEADER_CHARS:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic" or not parts[1]:
        return None
    try:
        encoded = parts[1].encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
        username_bytes, separator, password_bytes = decoded.partition(b":")
        if not separator:
            return None
        return username_bytes.decode("utf-8"), password_bytes.decode("utf-8")
    except (UnicodeError, ValueError, binascii.Error):
        return None


def _unauthorized_response() -> Response:
    """Build a direct 401 response; callers must not use Flask ``abort``."""

    response = Response("Unauthorized\n", status=401, mimetype="text/plain")
    response.headers["WWW-Authenticate"] = (
        'Basic realm="V5 Admin Console", charset="UTF-8"'
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def is_admin_path(path: str) -> bool:
    """Return whether *path* is a GM API or console entrypoint."""

    if path == "/api/gm" or path.startswith("/api/gm/"):
        return True
    if path in {
        "/",
        # These aliases are redirects to the same console in the current
        # server and must not provide an unauthenticated bypass.
        "/gm_console.html",
        "/control-panel.html",
    }:
        return True

    # The catch-all server serves both prefixes.  Normalize only these web
    # namespaces so repeated slashes and dot segments cannot bypass the
    # console entrypoint while ordinary assets remain public.
    if path == "/web" or path.startswith("/web/"):
        normalized = posixpath.normpath(path)
        if normalized.startswith("//"):
            normalized = "/" + normalized.lstrip("/")
        return _is_normalized_admin_path(normalized)

    if path == "/static_web" or path.startswith("/static_web/"):
        normalized = posixpath.normpath(path)
        if normalized.startswith("//"):
            normalized = "/" + normalized.lstrip("/")
        return _is_normalized_admin_path(normalized)

    return False


def _is_normalized_admin_path(path: str) -> bool:
    """Match a path after web/static namespace normalization."""

    if path in {
        "/",
        "/web",
        "/web/gm_console.html",
        "/static_web/gm_console.html",
        "/gm_console.html",
        "/control-panel.html",
    }:
        return True
    # A traversal-shaped web path that resolves to a GM API remains
    # protected; the normal API prefix check is intentionally unchanged.
    return path == "/api/gm" or path.startswith("/api/gm/")


def install_admin_guard(app: Any) -> Any:
    """Install optional Basic authentication on a Flask application.

    With no ``AG_ADMIN_AUTH_FILE`` environment variable the application is
    returned unchanged.  If the variable exists, even when empty or pointing
    at a missing file, configuration is required and failures raise before
    the application can start serving requests.
    """

    if AUTH_FILE_ENV not in os.environ:
        return app

    config = load_credentials(os.environ[AUTH_FILE_ENV])
    cache_owner = object()

    @app.before_request
    def _admin_access_before_request() -> Optional[Response]:
        if not is_admin_path(request.path):
            return None
        authorization = request.headers.get("Authorization")
        if _authorization_cache_hit(cache_owner, authorization):
            return None
        credentials = _decode_basic_credentials(authorization)
        if credentials is None or not config.verify(*credentials):
            return _unauthorized_response()
        if authorization is not None:
            _remember_authorization(cache_owner, authorization)
        return None

    # A small state marker is useful to callers/tests and contains no secret
    # material.  Do not store the password hash on the Flask app object.
    try:
        app.extensions.setdefault("admin_access", {})["enabled"] = True
    except AttributeError:
        pass
    return app


__all__ = [
    "AUTH_FILE_ENV",
    "AdminAuthConfigError",
    "PBKDF2_ITERATIONS",
    "install_admin_guard",
    "is_admin_path",
    "load_credentials",
    "make_password_hash",
]
