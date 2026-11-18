"""Which code produced a record: the git commit of the running code (stored with every frozen prediction)."""

from __future__ import annotations

import subprocess

from app.core.settings import BACKEND_DIR


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
