"""Identity (L12) — per-agent workload identity.

Every agent IS a workload identity. When one agent calls another over A2A, it
presents a signed token; the callee verifies it and knows WHO is calling — the
zero-trust property ("the token is the agent's identity on every call"). The
audit log (L9) then attributes every action to a verified caller.

L31.P backing: a locally-signed JWT (HS256, dev key). This is the same SHAPE as
a production OIDC client-credentials token from Authelia / Okta / Entra ID — the
verify path is identical (decode + check claims). Authelia (real OIDC issuer) is
the documented production swap behind `mint_token` / `verify_token`.

Modes:
- permissive (default): a missing/invalid token is logged but not rejected — so
  the demo + older running servers keep working.
- strict (LUNASRE_ENFORCE_IDENTITY=1): an invalid/missing token is rejected.
"""

from __future__ import annotations

import os
from typing import Any

import jwt

# Local dev signing key. In prod this is the OIDC issuer's key (Authelia/Okta);
# verification fetches the issuer JWKS. Same verify path, different key source.
_DEV_KEY = "luna-dev-signing-key-not-for-prod"
_ALG = "HS256"
_ISSUER = "lunasre-local"


def enforce_enabled() -> bool:
    return os.environ.get("LUNASRE_ENFORCE_IDENTITY", "") == "1"


def mint_token(agent_id: str, *, scopes: list[str] | None = None) -> str:
    """Mint a workload-identity token for an agent (its client-credentials grant).

    Prod swap: Authelia issues this via the OIDC client_credentials flow keyed by
    each agent's client_id/secret; the claims (sub, scopes) are the same shape.
    """
    claims: dict[str, Any] = {"iss": _ISSUER, "sub": agent_id}
    if scopes:
        claims["scopes"] = scopes
    return jwt.encode(claims, _DEV_KEY, algorithm=_ALG)


def verify_token(token: str) -> dict[str, Any]:
    """Verify a token + return its claims. Raises jwt.InvalidTokenError if bad.

    Prod swap: verify against the OIDC issuer JWKS instead of the dev key.
    """
    return jwt.decode(token, _DEV_KEY, algorithms=[_ALG], issuer=_ISSUER)


def caller_from_authorization(authorization: str | None) -> tuple[str, bool]:
    """Extract + verify the caller agent_id from an Authorization header.

    Returns (agent_id, verified). In permissive mode an absent/invalid token
    yields ("anonymous", False) without raising; strict mode raises.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        if enforce_enabled():
            raise PermissionError("identity required (strict mode) — no bearer token")
        return ("anonymous", False)
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_token(token)
        return (str(claims.get("sub", "unknown")), True)
    except jwt.InvalidTokenError as e:
        if enforce_enabled():
            raise PermissionError(f"identity invalid (strict mode): {e}") from e
        return ("anonymous", False)
