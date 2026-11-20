"""注册、登录、当前身份；认证细节由app/auth.py处理。"""
from typing import Annotated
from fastapi import APIRouter, Depends
from app.auth import get_accounts, current_user, require_admin, authenticate
from app.schemas.api import Credentials
from app.repositories.users import UserRepository, public_user
router = APIRouter(prefix="/auth", tags=["身份"])


@router.post("/register", status_code=201)
def register(req: Credentials, store: Annotated[UserRepository, Depends(get_accounts)]):
    return store.create(req.username, req.password, role="employee")


@router.get("/me")
def me(user: Annotated[dict, Depends(current_user)]):
    return public_user(user)


@router.get("/admin-check")
def admin_check(user: Annotated[dict, Depends(require_admin)]):
    return {"username": user["username"], "role": user["role"]}


@router.post("/login")
def login(req: Credentials, store: Annotated[UserRepository, Depends(get_accounts)]):
    return authenticate(req.username, req.password, store)
