"""Raw payload file storage (DATA-MODEL.md D2): gzip files, path = source/yyyy/mm/dd/sha256.ext.gz.

- LocalRawStore: backend/data/raw (development, tests).
- SupabaseRawStore: private Supabase Storage bucket (production).
Files are never overwritten: the same content always has the same path.
"""

from __future__ import annotations

import gzip
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Protocol

import httpx

from app.core.settings import BACKEND_DIR, Settings
from app.domain.enums import SourceCode


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def object_path(source: SourceCode, fetched_at: datetime, digest: str, ext: str) -> str:
    return f"{source.value.lower()}/{fetched_at:%Y/%m/%d}/{digest}.{ext}.gz"


class RawStore(Protocol):
    def put(self, path: str, body: bytes) -> None: ...
    def get(self, path: str) -> bytes: ...


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, path: str, body: bytes) -> None:
        target = self.root / path
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(gzip.compress(body))
        tmp.replace(target)

    def get(self, path: str) -> bytes:
        return gzip.decompress((self.root / path).read_bytes())


class SupabaseRawStore:
    def __init__(self, url: str, service_key: str, bucket: str = "raw-payloads") -> None:
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.client = httpx.Client(timeout=60.0, headers={"Authorization": f"Bearer {service_key}"})

    def ensure_bucket(self) -> None:
        resp = self.client.post(f"{self.base}/bucket", json={"id": self.bucket, "name": self.bucket, "public": False})
        if resp.status_code not in (200, 201, 400, 409):  # 400/409: already exists
            resp.raise_for_status()

    def put(self, path: str, body: bytes) -> None:
        resp = self.client.post(
            f"{self.base}/object/{self.bucket}/{path}",
            content=gzip.compress(body),
            headers={"Content-Type": "application/gzip", "x-upsert": "false"},
        )
        if resp.status_code in (400, 409) and "exists" in resp.text.lower():
            return  # same content already stored
        resp.raise_for_status()

    def get(self, path: str) -> bytes:
        resp = self.client.get(f"{self.base}/object/{self.bucket}/{path}")
        resp.raise_for_status()
        return gzip.decompress(resp.content)


def store_from_settings(settings: Settings) -> RawStore:
    """The raw store chosen by RAW_STORE ("local" or "supabase")."""
    if settings.raw_store == "supabase":
        store = SupabaseRawStore(settings.supabase_url, settings.supabase_service_role_key.get_secret_value())
        store.ensure_bucket()
        return store
    return LocalRawStore(BACKEND_DIR / settings.raw_store_dir)
