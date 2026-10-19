from typing import Annotated
from pydantic import Field, StringConstraints, field_validator
from .base import RequestSchema, ResponseSchema

Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100,
                                           pattern=r"^[a-zA-Z0-9_.-]+$")]
Password = Annotated[str, Field(min_length=12, max_length=128, repr=False)]


class RegisterRequest(RequestSchema):
    username: Username
    password: Password
    email: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        if value is not None:
            value = value.strip().casefold()
            if "@" not in value or any(c.isspace() for c in value):
                raise ValueError("邮箱格式不正确")
        return value


class LoginRequest(RequestSchema):
    username: Username
    password: Annotated[str, Field(min_length=1, max_length=128, repr=False)]


class RegisteredUser(ResponseSchema):
    id: int
    username: str
    email: str | None


class TokenResponse(ResponseSchema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
