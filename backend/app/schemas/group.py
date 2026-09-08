from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.underlying import UnderlyingResponse


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None


class GroupAddUnderlyings(BaseModel):
    underlying_ids: list[int]


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    icon: str | None
    is_default: bool
    position: int
    created_at: datetime
    underlyings: list[UnderlyingResponse] = []
