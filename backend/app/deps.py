from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Header, HTTPException, status
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientError

from app import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserContext:
    id: str
    email: str


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    jwks_url = f"{url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True)


def _decode_supabase_jwt(token: str) -> dict:
    """
    Supabase user access tokens are either:
    - HS256 signed with the project's JWT secret (legacy / symmetric), or
    - RS256 / ES256 via JWKS after "new JWT signing keys" migration.

    We branch on the token header `alg`.
    """
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as e:
        logger.warning("JWT header parse failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from e

    alg = (header.get("alg") or "HS256").upper()

    if alg == "HS256":
        if not settings.SUPABASE_JWT_SECRET:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server missing SUPABASE_JWT_SECRET (required for HS256 tokens)",
            )
        try:
            return jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except InvalidTokenError as e:
            logger.warning("HS256 JWT verification failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from e

    if not settings.SUPABASE_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server missing SUPABASE_URL (required to verify asymmetric JWTs)",
        )

    issuer = f"{settings.SUPABASE_URL}/auth/v1"
    try:
        signing_key = _jwks_client(settings.SUPABASE_URL).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            issuer=issuer,
        )
    except (InvalidTokenError, PyJWKClientError) as e:
        logger.warning("JWKS JWT verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


async def require_supabase_user(
    authorization: Annotated[str | None, Header()] = None,
) -> UserContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer <access_token>",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
        )

    if (
        not settings.SUPABASE_JWT_SECRET
        and not settings.SUPABASE_URL
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server missing SUPABASE_JWT_SECRET and SUPABASE_URL",
        )

    payload = _decode_supabase_jwt(token)
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )
    email = payload.get("email")
    em = email if isinstance(email, str) else ""
    return UserContext(id=sub, email=em)
