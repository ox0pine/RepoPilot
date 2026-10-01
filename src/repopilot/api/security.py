from __future__ import annotations

from hmac import compare_digest

from fastapi import HTTPException, Request, status


def require_auth(request: Request) -> None:
    configured = request.app.state.settings.api_token.get_secret_value()
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or not compare_digest(token.encode(), configured.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
