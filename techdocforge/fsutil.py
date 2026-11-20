"""Filesystem helpers: atomic writes and restrictive permissions."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def restrict_permissions(path: Path, mode: int) -> None:
    """Apply POSIX permissions (no-op on Windows, where the per-user profile ACLs already apply)."""
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.chmod(path, mode)


def atomic_write_bytes(path: str | Path, data: bytes, mode: int = 0o600) -> None:
    """Write to a temp file in the same directory, fsync, then atomically replace the target.

    Prevents half-written/corrupted files and avoids following a pre-planted symlink at the
    temp path (mkstemp uses O_EXCL).
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tdf-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        restrict_permissions(Path(tmp), mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path: str | Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode)
