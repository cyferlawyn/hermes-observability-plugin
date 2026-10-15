"""Enforce tool-free execution for user-triggered Analyze follow-up sessions."""
from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)
_MARKER = b"hermes-observability:read-only-analyze:v1\n"
_DIRECTORY = ".hermes-observability/analyze-read-only"


def marker_path(session_id: str, profile_home: str | Path) -> Path:
    """Return a profile-local marker path without exposing the session ID in its filename."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        raise ValueError("session_id must be a non-empty string of at most 256 characters")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(profile_home) / _DIRECTORY / f"{digest}.guard"


def _secure_directory(path: Path) -> None:
    """Create/check private, owned policy directories without following symlinks."""
    for directory in (path.parent, path):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            # The new directory's name lives in its parent; persist that entry.
            _sync_directory(directory.parent)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError("Analyze policy directory is not a private directory owned by this user")
        if info.st_mode & 0o077:
            os.chmod(directory, 0o700)
            info = directory.lstat()
            if info.st_mode & 0o077:
                raise PermissionError("Analyze policy directory permissions are not private")


def _sync_directory(path: Path) -> None:
    """Persist a directory entry update before the caller enables tool execution."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_marker_metadata(info: os.stat_result) -> None:
    """Reject marker files that are not private, owned regular single-link files."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise PermissionError("Analyze policy marker ownership or permissions are invalid")


def mark_read_only_session(session_id: str, profile_home: str | Path) -> bool:
    """Persist a restrictive tool policy before any Analyze prompt is submitted."""
    path = marker_path(session_id, profile_home)
    _secure_directory(path.parent)
    # Persist every directory entry that locates the marker, even when it already
    # exists from a previous call whose final directory fsync may have failed.
    _sync_directory(path.parent.parent.parent)
    _sync_directory(path.parent.parent)
    _sync_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        if is_read_only_session(session_id, profile_home):
            return True
        raise PermissionError("Existing Analyze policy marker is invalid")
    try:
        with os.fdopen(fd, "wb") as marker:
            _validate_marker_metadata(os.fstat(marker.fileno()))
            marker.write(_MARKER)
            marker.flush()
            os.fsync(marker.fileno())
        _sync_directory(path.parent)
    except Exception:
        # Leave a corrupt marker behind if a partial write occurred. The hook treats
        # corruption as a deny, and the UI will not submit a prompt after this error.
        raise
    return True


def is_read_only_session(session_id: str, profile_home: str | Path) -> bool:
    """Check the durable policy marker; malformed markers raise so callers can deny."""
    path = marker_path(session_id, profile_home)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return False
    with os.fdopen(fd, "rb") as marker:
        info = os.fstat(marker.fileno())
        _validate_marker_metadata(info)
        value = marker.read(len(_MARKER) + 1)
    if value != _MARKER:
        raise PermissionError("Analyze policy marker could not be verified")
    return True


def make_pre_tool_call_guard(profile_home: Callable[[], str | Path]):
    """Build a Hermes pre-tool hook that blocks every tool in a marked Analyze session.

    Missing markers leave ordinary sessions untouched. Any other policy lookup error
    fails closed because the hook cannot prove that tool execution is permitted.
    """
    def guard(*, session_id: str = "", tool_name: str = "", **_: Any) -> dict[str, str] | None:
        if not session_id:
            return {
                "action": "block",
                "message": (
                    f"Tool '{tool_name or 'unknown'}' blocked: session identity is unavailable, so the read-only "
                    "Analyze policy cannot be verified. No tool action was run."
                ),
            }
        try:
            if is_read_only_session(session_id, profile_home()):
                return {
                    "action": "block",
                    "message": (
                        f"Tool '{tool_name or 'unknown'}' blocked: this is a read-only Analyze session. "
                        "No tools or remediation actions are permitted here. Start a separate normal session "
                        "if the user explicitly approves making changes."
                    ),
                }
        except Exception:
            log.error("observability: Analyze tool policy lookup failed; blocking tool", exc_info=True)
            return {
                "action": "block",
                "message": (
                    f"Tool '{tool_name or 'unknown'}' blocked: the read-only Analyze session policy could not verify "
                    "the session. No tool action was run."
                ),
            }
        return None

    return guard
