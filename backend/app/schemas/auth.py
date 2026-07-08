from pydantic import BaseModel


class PlatformLoginRequest(BaseModel):
    phone: str
    password: str


class PlatformLoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    role: str = "admin"
    phone_masked: str
    message: str
