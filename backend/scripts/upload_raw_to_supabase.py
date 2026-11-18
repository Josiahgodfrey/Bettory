"""Copy the raw payload files referenced by raw.payloads from local disk to the private Supabase Storage bucket.

    venv\\Scripts\\python scripts\\upload_raw_to_supabase.py            (dry run: checks only)
    venv\\Scripts\\python scripts\\upload_raw_to_supabase.py --run      (upload, then read each file back)

Uses BETTORY/.env (database + SUPABASE_SERVICE_ROLE_KEY). Each file is checked against its database row (sha256 and
size of the uncompressed body) before upload and read back from Storage afterwards. Files are never overwritten
(same path = same content). Only files referenced by raw.payloads are copied. Secrets are never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.core.settings import get_settings  # noqa: E402
from app.storage.db import get_engine  # noqa: E402
from app.storage.raw_store import LocalRawStore, SupabaseRawStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="upload (default: dry run)")
    run = ap.parse_args().run

    s = get_settings()
    if not s.supabase_service_role_key.get_secret_value():
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is not set in BETTORY/.env")
    local = LocalRawStore(BACKEND / s.raw_store_dir)
    with get_engine().connect() as conn:
        rows = conn.execute(text("select storage_path, sha256, byte_size from raw.payloads order by fetched_at")).all()

    bad = []
    for path, digest, size in rows:
        body = local.get(path)
        if hashlib.sha256(body).hexdigest() != digest or len(body) != size:
            bad.append(path)
    print(f"{len(rows)} files referenced; {len(rows) - len(bad)} match their database row; {len(bad)} do not")
    if bad:
        for p in bad[:10]:
            print("  mismatch:", p)
        return 1
    if not run:
        print("dry run: nothing uploaded (add --run)")
        return 0

    remote = SupabaseRawStore(s.supabase_url, s.supabase_service_role_key.get_secret_value())
    remote.ensure_bucket()
    for i, (path, _digest, _size) in enumerate(rows, 1):
        remote.put(path, local.get(path))
        if i % 50 == 0:
            print(f"  uploaded {i}/{len(rows)}")
    failed = [p for p, d, _ in rows if hashlib.sha256(remote.get(p)).hexdigest() != d]
    print(f"uploaded {len(rows)}; read back and verified {len(rows) - len(failed)}; failed {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
