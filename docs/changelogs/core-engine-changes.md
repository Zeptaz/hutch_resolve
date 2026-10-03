# Core engine change log (Resolve engine — owner Harry)

Changes the chatbot work needs **outside** `backend/resolve/conversation/**`: Resolve services and
facade, providers, migrations, authorization, shared DTOs/contracts (`docs/contracts*`), the Voice bridge,
or the frontend. Each entry is a request/record for Harry (engine) or Jayith (frontend) and must say how
risky the change is. Do not merge an entry rated MEDIUM or HIGH without the owner's review.

Chatbot-only changes are in [chatbot-changes.md](chatbot-changes.md).

## Risk levels

| Level | Meaning |
| --- | --- |
| SMALL / LOW | Customer-facing wording or an additive optional field. No business rule, schema or permission change. Existing tests should pass unchanged. |
| MEDIUM | Additive contract/DTO change (new field or code), or a change other modules read. Needs contract update (`docs/contracts.md`, OpenAPI), regenerated frontend types and owner review. |
| BIG / HIGH | Changes business decisions, evidence sufficiency, permissions, action/consent flow, migrations or idempotency. Needs owner implementation or approval and PostgreSQL-gated tests. |

## Entry format

```
### CE-NNN — title
- Date / status: (PROPOSED | AGREED | DONE | REJECTED)
- Owner: Harry | Jayith
- Risk: SMALL | MEDIUM | BIG — why
- Files:
- What / why:
- Requested by: CB-NNN
- Verification:
- Commit:
```

---

### CE-001 — Engine-owned customer text is English-only and not localizable

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: MEDIUM — additive DTO/contract change; no business-rule change
- Requested by: CB-001
- Where the English text is produced:
  - `backend/resolve/providers/sandbox.py:769` — "The observed closing balance matches opening plus posted entries."
  - `backend/resolve/providers/sandbox.py:1048` — "No matching recharge record was found…"
  - `backend/resolve/services/facade.py:517` — VAS activation-evidence / consent limitation text.
  - `backend/resolve/services/facade.py:697` — proposal `consequences` ("Create a human review request for …; no account change is made now.").
- What / why: findings, review reasons and proposal consequences reach the customer as finished English
  sentences. The chatbot cannot translate them safely by template because it only receives the sentence,
  not a stable meaning. Proposal: add an optional machine-readable `code` (+ `params`) next to each
  `finding.text`, `review_reason` and `consequences`, keeping the English text unchanged as the
  authoritative fallback. The chatbot would then render reviewed Sinhala/Singlish/Tamil templates per code.
- Verification: not started.
- Commit: none.

### CE-002 — Internal escalation reason and raw enum shown to the customer

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: SMALL — wording only
- Requested by: CB-001
- Files: `backend/resolve/services/facade.py:639` (and its use in `consequences`).
- What / why: the offer the customer reads ends with "Reason: Human review offered for the reported
  vas dispute issue; see the case evidence." This is an agent-facing note, and `vas dispute` is the
  lower-cased `VAS_DISPUTE` enum. Either keep the reason out of the customer-facing `consequences`, or
  give it a customer-friendly label ("service charge"). The chatbot can hide it on its side only by string
  matching, which is fragile.
- Update after CB-001: still visible in the text message and the confirmation card ("What this means").
  With the Singlish rewrite it now appears translated too ("Reason: Reported balance recharge issue eka gana
  human review eka offer karala thiyenawa…"), so it reads even more like a message to the customer.
- Verification: not started.
- Commit: none.

### CE-003 — Optional: let the customer see the English original of a rewritten reply

- Date / status: 2026-10-03 — PROPOSED (optional; nothing changed)
- Owner: Harry (API) + Jayith (frontend)
- Risk: MEDIUM — additive response field and UI control; no business-rule change.
- Requested by: CB-001
- What / why: rewritten replies already persist Resolve's English wording as `messages.source_reply_text`,
  but `TurnResult` does not expose it, so the chat UI cannot offer "Show original (English)". Exposing it as
  an optional field (contract + OpenAPI + generated types) would let a customer or demo viewer check the
  machine-written Singlish against the authoritative text.
- Verification: not started.
- Commit: none.

### CE-004 — Account view has no price/charge per subscription

- Date / status: 2026-10-03 — PROPOSED (nothing changed)
- Owner: Harry
- Risk: MEDIUM — additive optional fields on `SubscriptionSummary` (contract + OpenAPI + generated types);
  no business-rule change.
- Requested by: CB-003
- What / why: for "what are my VAS charges?" the chatbot can list services from `AccountView.subscriptions`
  but cannot say what each costs, because the view has no `price_minor`/renewal period. Today it offers a
  VAS investigation to read the actual posted charges from the ledger. Adding e.g. `price_minor`,
  `currency` and `renewal_period` (from `sandbox.offers`) would let it answer in one turn without opening a
  case. The chatbot would only display the values, never compute charges.
- Verification: not started.
- Commit: none.

