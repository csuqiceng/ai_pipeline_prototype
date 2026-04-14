from pydantic import BaseModel
from datetime import datetime


# ---------- 请求模型 ----------

class ActivateRequest(BaseModel):
    license_code: str
    machine_id: str
    machine_name: str | None = None
    app_version: str | None = None


class DeactivateRequest(BaseModel):
    machine_id: str | None = None


class HeartbeatRequest(BaseModel):
    machine_id: str
    app_version: str | None = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class LicenseCreateRequest(BaseModel):
    user_id: int | None = None
    license_type: str = "monthly"  # trial, monthly, yearly, lifetime, custom
    voice_enabled: bool = False
    deepseek_enabled: bool = False
    voice_daily_quota: int = 0
    deepseek_monthly_quota: int = 0
    validity_days: int = 30


class LicenseUpdateRequest(BaseModel):
    voice_enabled: bool | None = None
    deepseek_enabled: bool | None = None
    voice_daily_quota: int | None = None
    deepseek_monthly_quota: int | None = None
    expires_at: datetime | None = None
    status: str | None = None


# ---------- 响应模型 ----------

class LicenseInfo(BaseModel):
    type: str
    voice_enabled: bool
    deepseek_enabled: bool
    voice_daily_quota: int
    deepseek_monthly_quota: int
    expires_at: datetime | None
    days_remaining: int | None = None


class QuotaInfo(BaseModel):
    voice_used_today: int = 0
    deepseek_used_this_month: int = 0


class ActivateResponse(BaseModel):
    access_token: str
    token_expires_in: int
    license: LicenseInfo
    quota: QuotaInfo


class HeartbeatResponse(BaseModel):
    valid: bool
    quota: QuotaInfo


class LicenseListItem(BaseModel):
    id: int
    license_code: str
    license_type: str
    status: str
    voice_enabled: bool
    deepseek_enabled: bool
    machine_id: str | None = None
    machine_name: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    activated_at: datetime | None = None


class StatisticsResponse(BaseModel):
    total_licenses: int = 0
    active_licenses: int = 0
    total_users: int = 0
    deepseek_calls_today: int = 0
    voice_calls_today: int = 0
