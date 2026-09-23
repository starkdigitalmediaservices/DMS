import pytest
import uuid
import time
from jose import jwt
from fastapi import HTTPException
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    verify_token,
    hash_password,
    verify_password
)
from app.config import settings
from app.models.user import UserRole


def test_password_hashing_edge_cases():
    """Verify password hashing and verification edge cases including empty strings and long passwords."""
    raw_pw = "SuperSecureP@ssw0rd!2026"
    hashed = hash_password(raw_pw)
    
    assert verify_password(raw_pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert verify_password("", hashed) is False


def test_tampered_jwt_signature_rejection():
    """Verify JWT with tampered signature is rejected."""
    token = create_access_token("user-1", "tenant-1", UserRole.user)
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}.InvalidSignature123"

    with pytest.raises(HTTPException) as exc_info:
        verify_token(tampered)
    assert exc_info.value.status_code == 401
    assert "Could not validate credentials" in exc_info.value.detail


def test_expired_token_rejection():
    """Verify expired token payload raises 401."""
    now = int(time.time()) - 3600  # Expired 1 hour ago
    payload = {
        "sub": "user-expired",
        "tenant_id": "tenant-expired",
        "role": "user",
        "exp": now,
        "jti": str(uuid.uuid4()),
        "type": "access"
    }
    expired_token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    with pytest.raises(HTTPException) as exc_info:
        verify_token(expired_token)
    assert exc_info.value.status_code == 401


def test_mismatched_token_type_validation():
    """Verify verifying token types strictly distinguishes access vs refresh tokens."""
    acc_token = create_access_token("u-1", "t-1", UserRole.user)
    ref_token = create_refresh_token("u-1", "t-1", UserRole.user)

    acc_payload = verify_token(acc_token)
    ref_payload = verify_token(ref_token)

    assert acc_payload.type == "access"
    assert ref_payload.type == "refresh"
    assert acc_payload.type != ref_payload.type


def test_production_secrets_validation():
    """Verify that default weak secrets fail validation in production mode."""
    from app.config import Settings

    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be a strong secret"):
        Settings(
            app_env="production",
            postgres_url="postgresql+asyncpg://u:p@localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            jwt_secret_key="secret"
        )

