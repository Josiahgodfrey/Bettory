"""Which code produced a record: the git commit of the running code (stored with every frozen prediction)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from app.core.settings import BACKEND_DIR


def file_sha256(path: Path) -> str:
    """SHA-256 of a file (the params file, recorded with every frozen prediction)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_version() -> str | None:
    """Short git commit of the running code, '+dirty' when app/ or config/ has uncommitted changes."""
    try:
        head = subprocess.run(["git", "-C", str(BACKEND_DIR), "rev-parse", "--short=12", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(BACKEND_DIR), "status", "--porcelain", "--", "app", "config"],
                               check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ("+dirty" if dirty else "")
