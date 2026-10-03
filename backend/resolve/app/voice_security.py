from __future__ import annotations

import hashlib
import hmac
import json
import time


def canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def signature(secret: bytes, timestamp: str, event_id: str, digest: str) -> str:
    msg = f"{timestamp}.{event_id}.{digest}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def signed_headers(secret: bytes, event_id: str, body: bytes, *, now: int | None = None) -> dict[str, str]:
    timestamp = str(int(time.time()) if now is None else now)
    digest = body_digest(body)
    return {
        "X-Voice-Timestamp": timestamp,
        "X-Voice-Event-Id": event_id,
        "X-Voice-Body-Sha256": digest,
        "X-Voice-Signature": signature(secret, timestamp, event_id, digest),
        "Content-Type": "application/json",
    }


def verify_headers(secret: bytes, headers, body: bytes, *, now: int | None = None) -> bool:
    timestamp = headers.get("x-voice-timestamp", "")
    event_id = headers.get("x-voice-event-id", "")
    digest = headers.get("x-voice-body-sha256", "")
    supplied = headers.get("x-voice-signature", "")
    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 12:
        return False
    if not event_id or len(event_id) > 128 or len(digest) != 64:
        return False
    if any(character not in "0123456789abcdef" for character in digest):
        return False
    try:
        current = int(time.time()) if now is None else now
        if abs(current - int(timestamp)) > 60:
            return False
    except (TypeError, ValueError):
        return False
    actual = body_digest(body)
    if not hmac.compare_digest(actual, digest):
        return False
    return hmac.compare_digest(signature(secret, timestamp, event_id, digest), supplied)
