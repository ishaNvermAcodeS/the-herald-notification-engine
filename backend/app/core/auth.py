from typing import Optional
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.config import settings

security_bearer = HTTPBearer(auto_error=False)


async def verify_api_key(
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> str:
    """
    Validates API key from either 'Authorization: Bearer <token>' or 'x-api-key: <token>'.
    Enforces documented API.md authentication convention.
    """
    provided_key = None
    if bearer and bearer.credentials:
        provided_key = bearer.credentials
    elif x_api_key:
        provided_key = x_api_key

    if not provided_key or provided_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided_key
