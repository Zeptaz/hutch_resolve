"""Turn fingerprints and stable downstream command keys (T-01)."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from .dto import NormalizedTurn


def turn_fingerprint(turn: NormalizedTurn) -> str:
    """SHA-256 over the canonical turn body.

    expected_version is excluded on purpose: it is concurrency control, not request
    identity. The Voice bridge derives it from the binding at receipt time, so a
    retried Voice turn after completion would otherwise look like a changed body.
    The same holds for voice_evidence.presentation_response_id: the bridge looks up the
    latest recorded offer, which the first processing of this turn may itself replace.
    """
    body = turn.model_dump(mode="json", exclude={"expected_version", "turn_id", "conversation_id"})
    evidence = body.get("voice_evidence")
    if isinstance(evidence, dict):
        evidence.pop("presentation_response_id", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def command_key(conversation_id: UUID, turn_id: UUID, command: str, target: UUID | str | None = None) -> str:
    """Deterministic key for one facade command caused by one turn.

    Derived from conversation + originating turn + command + target, never random,
    so a replayed or reclaimed turn sends Resolve the same key and cannot repeat
    a business action.
    """
    parts = ["conv", str(conversation_id), "turn", str(turn_id), command]
    if target is not None:
        parts.append(str(target))
    return ":".join(parts)
