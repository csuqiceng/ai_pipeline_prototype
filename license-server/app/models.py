from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, Text, CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    """客户/被授权方（无登录功能，只有 admins 可登录后台）"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=True)
    phone = Column(String(50), nullable=True)
    contact_name = Column(String(100), nullable=True)
    company_name = Column(String(255), nullable=True)
    address = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    status = Column(String(20), default="active")

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'deleted')",
            name='ck_user_status'
        ),
        {'sqlite_autoincrement': True},
    )


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    license_code = Column(String(64), unique=True, nullable=False, index=True)
    license_type = Column(String(20), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    voice_enabled = Column(Boolean, default=False)
    deepseek_enabled = Column(Boolean, default=False)
    voice_daily_quota = Column(Integer, default=0)
    deepseek_monthly_quota = Column(Integer, default=0)

    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    activated_at = Column(DateTime, nullable=True)
    machine_id = Column(String(128), nullable=True)
    machine_name = Column(String(255), nullable=True)
    status = Column(String(20), default="inactive")

    __table_args__ = (
        CheckConstraint(
            "license_type IN ('trial', 'monthly', 'yearly', 'lifetime', 'custom')",
            name='ck_license_type'
        ),
        CheckConstraint(
            "status IN ('inactive', 'active', 'expired', 'suspended', 'revoked')",
            name='ck_status'
        ),
        {'sqlite_autoincrement': True},
    )


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey('licenses.id'), nullable=False)
    machine_id = Column(String(128), nullable=True)
    service_type = Column(String(20), nullable=False)
    request_id = Column(String(64), unique=True, nullable=False)

    input_chars = Column(Integer, default=0)
    output_chars = Column(Integer, default=0)
    audio_seconds = Column(Float, default=0)

    status = Column(String(20), default="success")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "service_type IN ('voice', 'deepseek')",
            name='ck_service_type'
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'quota_exceeded')",
            name='ck_usage_status'
        ),
        Index('idx_license_date', 'license_id', 'created_at'),
        Index('idx_created_at', 'created_at'),
        {'sqlite_autoincrement': True},
    )


class AccessToken(Base):
    __tablename__ = "access_tokens"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey('licenses.id', ondelete='CASCADE'), nullable=False)
    token_hash = Column(String(64), nullable=False, index=True)
    machine_id = Column(String(128), nullable=True)

    issued_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
    is_revoked = Column(Boolean, default=False)

    __table_args__ = (
        Index('idx_expires', 'expires_at'),
        {'sqlite_autoincrement': True},
    )


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="operator")
    created_at = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'admin', 'operator')",
            name='ck_admin_role'
        ),
        {'sqlite_autoincrement': True},
    )


class MigrationLog(Base):
    __tablename__ = "migration_logs"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey('licenses.id'), nullable=False)
    old_machine_id = Column(String(128), nullable=True)
    new_machine_id = Column(String(128), nullable=True)
    operator_id = Column(Integer, ForeignKey('admins.id'), nullable=True)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_license', 'license_id'),
        {'sqlite_autoincrement': True},
    )
