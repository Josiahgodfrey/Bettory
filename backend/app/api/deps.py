"""FastAPI dependencies: owner-only authentication (ARCHITECTURE.md section 7) and shared resources.

Every endpoint except /health depends on `require_owner`, which fails closed:
1. `Authorization: Bearer <Supabase access token>` must be present          -> else 401
2. Signature, expiry, audience "authenticated" and issuer <SUPABASE_URL>/auth/v1 must verify -> else 401
   (asymmetric keys from the project's JWKS; HS256 only if SUPABASE_JWT_SECRET is set)
3. `sub` must equal OWNER_USER_ID                                          -> else 403
4. `aal` must be "aal2" (two-factor completed)                             -> else 403
If OWNER_USER_ID or SUPABASE_URL is not configured, every request is refused (503).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine

from app.core.settings import Settings, get_settings
from app.domain.registry import Registry, load_registry
from app.storage.db import get_engine
from app.storage.raw_store import RawStore, store_from_settings

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")
LOOPBACK = {"127.0.0.1", "::1"}
_bearer = HTTPBearer(auto_error=False, description="Supabase access token of the owner (two-factor session)")


@dataclass(frozen=True)
class Owner:
    user_id: str
    aal: str
    local_bypass: bool = False


# --- shared resources (overridden in tests) ---------------------------------------------------


def settings_dep() -> Settings:
    return get_settings()


def engine_dep() -> Engine:
    return get_engine()


def registry_dep() -> Registry:
    return load_registry()


def raw_store_factory_dep(settings: Annotated[Settings, Depends(settings_dep)]) -> Callable[[], RawStore]:
    """A factory, not a store: the Supabase store makes a network call, so it is created inside the job."""
    return lambda: store_from_settings(settings)


# --- authentication -----------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=10)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def verify_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims. Raises HTTPException(401)."""
    base = settings.supabase_url.rstrip("/")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg in ASYMMETRIC_ALGORITHMS:
            key: Any = _jwks_client(f"{base}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        elif alg == "HS256" and settings.supabase_jwt_secret.get_secret_value():
            key = settings.supabase_jwt_secret.get_secret_value()
        else:
            raise _unauthorized("unsupported token algorithm")
        claims: dict[str, Any] = jwt.decode(
            token, key, algorithms=[alg], audience="authenticated", issuer=f"{base}/auth/v1",
            options={"require": ["exp", "iat", "sub", "aud", "iss"]}, leeway=10,
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token expired") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        raise _unauthorized("invalid token") from exc
    return claims


def require_owner(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Owner:
    if credentials is None and settings.auth_local_bypass:
        # Settings already refuse the bypass outside ENVIRONMENT=local; also require this machine.
        host = request.client.host if request.client else ""
        if settings.environment == "local" and host in LOOPBACK:
            return Owner(user_id="local-bypass", aal="none", local_bypass=True)
        raise _unauthorized("local bypass accepts requests from this machine only")

    if not settings.owner_user_id or not settings.supabase_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("missing bearer token")

    claims = verify_token(credentials.credentials, settings)
    if claims.get("sub") != settings.owner_user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not the owner")
    if claims.get("aal") != "aal2":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "two-factor authentication required")
    return Owner(user_id=str(claims["sub"]), aal="aal2")


OwnerDep = Annotated[Owner, Depends(require_owner)]
EngineDep = Annotated[Engine, Depends(engine_dep)]
RegistryDep = Annotated[Registry, Depends(registry_dep)]
SettingsDep = Annotated[Settings, Depends(settings_dep)]
RawStoreFactoryDep = Annotated[Callable[[], RawStore], Depends(raw_store_factory_dep)]
