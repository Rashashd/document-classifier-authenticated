from __future__ import annotations

from fastapi import APIRouter

from app.domain.user import UserCreate, UserRead
from app.services.auth_service import auth_backend, fastapi_users

router = APIRouter()

# POST /auth/register
router.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

# POST /auth/login (issues JWT)
# POST /auth/logout
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth",
    tags=["auth"],
)
