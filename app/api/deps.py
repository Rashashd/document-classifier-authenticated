from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.db.models import User
from app.services.auth_service import fastapi_users

# Resolves the bearer token and returns the current active user
# Return a FastAPI dependency that enforces Casbin RBAC
get_current_user = fastapi_users.current_user(active=True)


def require_role(*roles: str):
    """
    Raises 401 if no valid token is present (via get_current_user).
    Raises 403 if the authenticated user's role is not permitted.
    """

    async def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> User:
        enforcer = request.app.state.enforcer
        allowed = any(enforcer.enforce(current_user.role, role) for role in roles)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return current_user

    return _dependency
