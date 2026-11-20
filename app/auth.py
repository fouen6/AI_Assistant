"""密码哈希、JWT、当前用户和管理员依赖；HTTP路由在routers/auth.py。"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.config import setting
from app.errors import AppError
bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def hasher():
    from pwdlib import PasswordHash
    return PasswordHash.recommended()


def unauthorized():
    return AppError(401,"invalid_credentials","需要有效的登录身份。",{"WWW-Authenticate":"Bearer"})


class TokenService:
    def __init__(self, secret):
        if len(secret.encode("utf-8"))<32:
            raise AppError(503,"auth_configuration","请配置至少32字节的随机签名密钥。")
        self.secret = secret

    def issue(self, user, seconds=1800):
        now = datetime.now(timezone.utc)
        return jwt.encode({
            "sub": str(user["id"]), "iat":now,
            "exp": now + timedelta(seconds=seconds),
            "iss": "assistant-study", "aud":"assistant-api",
            "av": user["auth_version"]
        }, self.secret, algorithm="HS256")

    def decode(self, token):
        try:
            data = jwt.decode(token, self.secret, algorithms=["HS256"],
                issuer="assistant-study", audience="assistant-api",
                options={"require": ["sub", "iat", "exp", "iss", "aud", "av"]})
            if not data["sub"].isdigit() or type(data["av"]) is not int:
                raise ValueError("身份字段不合法")
            return data
        except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError) as exc:
            raise unauthorized() from exc


@lru_cache(maxsize=1)
def get_tokens():
    return TokenService(setting("API_JWT_SECRET"))


@lru_cache(maxsize=1)
def dummy_hash():
    return hasher().hash("Fixed-Dummy-Password_2026!")


def get_accounts():
    from app.repositories.users import UserRepository
    return UserRepository()


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    store: Annotated[object, Depends(get_accounts)],
):
    if credentials is None:
        raise unauthorized()
    data = get_tokens().decode(credentials.credentials)
    user = store.by_id(int(data["sub"]))
    if (user is None or not user["is_active"]
            or user["auth_version"] != data["av"]):
        raise unauthorized()
    return user


def require_admin(user: Annotated[dict, Depends(current_user)]):
    if user["role"] != "admin":
        raise AppError(403, "admin_required", "该操作需要管理员权限。")
    return user


def authenticate(username, password, store):
    user = store.by_name(username)
    hashed = user["password_hash"] if user else dummy_hash()
    if not hasher().verify(password, hashed) or user is None or not user["is_active"]:
        raise unauthorized()
    return {"access_token": get_tokens().issue(user), "token_type": "bearer", "expires_in": 1800}
