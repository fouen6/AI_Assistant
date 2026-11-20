"""接口输入/输出与证据选择结构；没有数据库和模型调用。"""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictStr, StrictBool, field_validator


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextBody(StrictBody):
    message: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("message 不能全为空白。")
        return value


class TextReply(BaseModel):
    reply: str = Field(min_length=1)


class Credentials(StrictBody):
    username: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")
    password: StrictStr = Field(min_length=12,max_length=128,repr=False)


class SessionCreate(StrictBody):
    mode: Literal["chat", "agent"] = "chat"


class TurnBody(TextBody):
    request_id: UUID


class ApprovalBody(StrictBody):
    request_id: UUID
    approved: StrictBool


class UserChange(StrictBody):
    role: Literal["employee", "admin"]
    is_active: StrictBool


class AvatarBody(StrictBody):
    avatar_key: Literal["person", "robot", "cat", "plant"]


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: StrictStr
    quote: StrictStr = Field(min_length=1)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["supported", "insufficient"]
    evidence: list[Quote] = Field(max_length=6)
