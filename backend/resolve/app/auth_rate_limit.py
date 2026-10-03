"""Small PostgreSQL-backed fixed-window limits for public session endpoints."""

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import ipaddress

from fastapi import Request
from sqlalchemy import text

from .auth import ResolveError


def client_address(request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    trusted = request.app.state.settings.trusted_proxy_ips
    if client in trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                return ipaddress.ip_address(candidate).compressed
            except ValueError:
                pass
    try:
        return ipaddress.ip_address(client).compressed
    except ValueError:
        return "unknown"


def enforce(request: Request, bucket: str, limit: int) -> None:
    """Atomically consume an attempt. DB failure is closed at the auth boundary."""
    injected = getattr(request.app.state, "auth_rate_limiter", None)
    if injected is not None:
        injected(bucket, limit)
        return
    settings = request.app.state.settings
    raw = f"auth-rate:{bucket}".encode("utf-8")
    digest = hmac.new(settings.app_secret_key, raw, hashlib.sha256).digest()
    now = datetime.now(UTC)
    engine = request.app.state.database.engine
    try:
        with engine.begin() as connection:
            # Bound table growth without scanning the whole history on each
            # auth attempt. Expired buckets are disposable, so reap a small
            # indexed batch in the same transaction as the current counter.
            connection.execute(text("""
                DELETE FROM resolve.auth_rate_limits
                WHERE bucket_hash IN (
                  SELECT bucket_hash FROM resolve.auth_rate_limits
                  WHERE expires_at<=:now ORDER BY expires_at LIMIT 100
                )
            """), {"now": now})
            count = connection.execute(text("""
                INSERT INTO resolve.auth_rate_limits(bucket_hash,window_started_at,attempt_count,expires_at)
                VALUES (:bucket,:now,1,:expiry)
                ON CONFLICT(bucket_hash) DO UPDATE SET
                  window_started_at=CASE WHEN resolve.auth_rate_limits.expires_at<=:now
                                         THEN :now ELSE resolve.auth_rate_limits.window_started_at END,
                  attempt_count=CASE WHEN resolve.auth_rate_limits.expires_at<=:now
                                     THEN 1 ELSE resolve.auth_rate_limits.attempt_count+1 END,
                  expires_at=CASE WHEN resolve.auth_rate_limits.expires_at<=:now
                                  THEN :expiry ELSE resolve.auth_rate_limits.expires_at END
                RETURNING attempt_count
            """), {"bucket": digest, "now": now, "expiry": now + timedelta(minutes=10)}).scalar_one()
    except Exception as exc:
        raise ResolveError(503, "DEPENDENCY_UNAVAILABLE", "Session service is temporarily unavailable", True) from exc
    if count > limit:
        raise ResolveError(429, "RATE_LIMITED", "Too many attempts; try again shortly", True)
