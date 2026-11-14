"""Owner-only authentication (ARCHITECTURE.md section 7): every case that must be refused is refused."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import settings_dep
from app.core.settings import Settings
from app.main import create_app

URL = "https://example-ref.supabase.co"
OWNER = "11111111-1111-1111-1111-111111111111"
SECRET = "test-secret-that-is-long-enough-for-hs256-0123456789"
ENDPOINT = "/jobs/competitions"  # needs no database


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"supabase_url": URL, "owner_user_id": OWNER, "supabase_jwt_secret": SECRET,
                              "environment": "local", "auth_local_bypass": False}
    return Settings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


def token(secret: str = SECRET, **claims: Any) -> str:
    now = int(time.time())
    payload = {"sub": OWNER, "aal": "aal2", "aud": "authenticated", "iss": f"{URL}/auth/v1",
               "iat": now, "exp": now + 600, **claims}
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, secret, algorithm="HS256")


@pytest.fixture
def client_for() -> Iterator[Any]:
    def make(s: Settings, host: str = "testclient") -> TestClient:
        app = create_app()
        app.dependency_overrides[settings_dep] = lambda: s
        return TestClient(app, client=(host, 50000))
    yield make


def get(client: TestClient, tok: str | None) -> int:
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.get(ENDPOINT, headers=headers).status_code


def test_owner_with_two_factor_is_accepted(client_for: Any) -> None:
    c = client_for(settings())
    resp = c.get(ENDPOINT, headers={"Authorization": f"Bearer {token()}"})
    assert resp.status_code == 200 and "ENG1" in [x["code"] for x in resp.json()]


@pytest.mark.parametrize(("tok", "expected"), [
    (None, 401),                                           # no token
    ("not-a-jwt", 401),                                    # garbage
    (token(secret="wrong-secret-wrong-secret-wrong-secret-00"), 401),  # bad signature
    (token(exp=int(time.time()) - 3600), 401),             # expired
    (token(aud="anon"), 401),                              # wrong audience
    (token(iss="https://other.supabase.co/auth/v1"), 401),  # other project
    (token(sub=None), 401),                                # no subject
    (token(sub="22222222-2222-2222-2222-222222222222"), 403),  # valid user, not the owner
    (token(aal="aal1"), 403),                              # password only, no two-factor
    (token(aal=None), 403),                                # assurance level missing
])
def test_refused(client_for: Any, tok: str | None, expected: int) -> None:
    assert get(client_for(settings()), tok) == expected


def test_alg_none_refused(client_for: Any) -> None:
    unsigned = jwt.encode({"sub": OWNER, "aal": "aal2"}, key=None, algorithm="none")  # type: ignore[arg-type]
    assert get(client_for(settings()), unsigned) == 401


def test_hs256_refused_without_configured_secret(client_for: Any) -> None:
    assert get(client_for(settings(supabase_jwt_secret="")), token()) == 401


def test_not_configured_fails_closed(client_for: Any) -> None:
    assert get(client_for(settings(owner_user_id="")), token()) == 503


def test_local_bypass_only_from_this_machine(client_for: Any) -> None:
    s = settings(auth_local_bypass=True)
    assert get(client_for(s, host="127.0.0.1"), None) == 200
    assert get(client_for(s, host="192.168.1.20"), None) == 401
    assert get(client_for(settings(), host="127.0.0.1"), None) == 401   # bypass off


def test_local_bypass_rejected_outside_local() -> None:
    with pytest.raises(ValidationError):
        settings(auth_local_bypass=True, environment="production")


def test_health_needs_no_token(client_for: Any) -> None:
    assert client_for(settings()).get("/health").status_code == 200
