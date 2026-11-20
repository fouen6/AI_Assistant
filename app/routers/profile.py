"""当前登录用户的头像接口；用户SQL位于repositories/users.py。"""
from typing import Annotated
from fastapi import APIRouter, Depends
from app.auth import current_user
from app.schemas.api import AvatarBody
from app.repositories.users import UserRepository
router = APIRouter(prefix="/profile", tags=["个人资料"])


def get_profiles():
    return UserRepository()


@router.get("")
def read(user: Annotated[dict, Depends(current_user)],
         store: Annotated[UserRepository, Depends(get_profiles)]):
    return store.read_profile(user["id"])


@router.patch("/avatar")
def change(req: AvatarBody, user: Annotated[dict, Depends(current_user)],
           store: Annotated[UserRepository, Depends(get_profiles)]):
    return store.change_avatar(user, req.avatar_key)
