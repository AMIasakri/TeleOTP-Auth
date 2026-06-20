from sqlalchemy import Column, Integer, String, DateTime, Boolean
from core_db import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True)
    password_hash = Column(String(255))
    telegram_chat_id = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)


class OTPSession(Base):
    __tablename__ = "otp_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    login_session = Column(String(255))
    otp_hash = Column(String(255))
    expires_at = Column(DateTime)
    attempts = Column(Integer, default=0)
    is_used = Column(Boolean, default=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    token = Column(String(255))
    expires_at = Column(DateTime)
    revoked = Column(Boolean, default=False)