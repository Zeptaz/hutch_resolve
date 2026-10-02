# Conversation module (Tevin)

`ConversationService.handle_turn(ctx, turn) -> TurnResult` is the single entrypoint for the text route and Harry's Voice bridge. It runs in-process inside the Resolve FastAPI app. See [Tevin's plan](../../../docs/plans/tevin.md) and [contracts](../../../docs/contracts.md).

| File | Purpose |
| --- | --- |
| `dto.py` | **Provisional** Pydantic mirrors of contract v1.0.0. Replace with re-exports once Harry publishes `resolve.contracts`; parity tests guard drift. |
| `ports.py` | Protocols Harry implements: `ResolveFacade`, `ConversationRepository`, `KnowledgeRepository`. |
| `state.py` | `DialogueState` stored by Harry as JSONB on the conversation: language, active case, pending question, candidate complaint, pending proposal reference. No business decisions. |
| `identity.py` | Turn fingerprint and deterministic per-turn command keys. |
| `service.py` | Claim → route → complete lifecycle; structured paths and free-text routing. |
| `extraction.py` | T-02 model output schema, prompt, budgeted `Extractor` (6 s total, one repair), and code-side time-window resolution. |
| `model.py` | `ModelClient` boundary and `GeminiModelClient` (google-genai, JSON-schema output, SDK retries off). |
| `resolve_adapter.py` | Adapts Harry's sync, dict-returning `backend.resolve.services.facade.ResolveFacade` to `ports.ResolveFacade`: worker threads, context/error conversion, DTO validation of every result. No business logic. |
| `try_extract.py` | Manual live check: `python -m resolve.conversation.try_extract` with `GEMINI_API_KEY`/`GEMINI_TEXT_MODEL`. |
| `rewrite.py` | `ReplyRewriter`: re-expresses the English reply in the customer's detected language/style (e.g. Singlish). Code keeps every number/ID, rejects new numbers, links and "80k"-style magnitudes, and falls back to English on any doubt. Optional; off unless passed to the service. |
| `templates.py` | Deterministic replies. English is authoritative; Sinhala/Tamil load from `locales/*.json` only when marked `REVIEWED`. |
| `locales/` | Machine-drafted, **unreviewed** Sinhala and Tamil wording (inactive). See `LANGUAGE_REVIEW.md`. |
| `eval/extraction_cases.jsonl` | 41-case multilingual extraction set; `try_extract --eval` scores live Gemini per variety. |

## What Harry's implementations must do

- `ConversationRepository.claim_turn` follows the documented check order: scope → completed replay/`IDEMPOTENCY_CONFLICT` → `TURN_IN_PROGRESS` → `CONVERSATION_BUSY` → `STALE_VERSION` → persist claim with a lease (no DB lock held across calls). `complete_turn` stores both messages, the result and state, mirrors `state.active_case_id` to `conversations.active_case_id`, advances the version once and releases the claim. `release_turn` drops the claim after an unexpected failure.
- `ResolveFacade` treats `command_key` as the idempotency key: the same key returns the saved result. Keys are `conv:{conversation}:turn:{turn}:{command}[:{target}]`. `create_case` replays on (conversation, originating turn).
- Expected customer-facing facade errors (`PROPOSAL_EXPIRED`, `PROPOSAL_INVALIDATED`, `ACTION_NOT_ALLOWED`, `CONFIRMATION_REQUIRED`, `RESOURCE_NOT_FOUND` on case selection) become replies. Everything else propagates as `ResolveError` for the API layer to render as the error envelope.
- The text route builds the turn with `NormalizedTurn.from_message(conversation_id, MessageRequest)`; browser bodies can never set channel or Voice evidence. The Voice bridge constructs `NormalizedTurn` with `channel=VOICE` and, for decisions, `VoiceConsentEvidence`.

## Free text (T-02)

Gemini classifies a message into one intent (`FAQ`, `ACCOUNT_ENQUIRY`, `NEW_COMPLAINT`, `FOLLOW_UP`, `CORRECTION`, `ACTION_DECISION`, `STATUS`, `HUMAN_REQUEST`, `OTHER`) and extracts customer-reported candidates. Code then:

- validates the JSON strictly; one repair attempt, all within 6 s; otherwise the structured forms are offered;
- turns the reported time ("yesterday", "last 2 days", a date) into an explicit UTC window from the **simulation clock**, rejecting future, reversed or >30-day windows; with no time mentioned it checks the local day so far and says so;
- asks at most one clarification per turn and never repeats one (`CLARIFY_TIME_WINDOW`, `CLARIFY_AMOUNT`, `CLARIFY_TARGET`, `CLARIFY_NEGATION`);
- never treats a typed or spoken "yes" as consent; the explicit `action_decision` control is required;
- answers FAQs only from reviewed knowledge cards with citations; guests get FAQs only.

Pending question codes for the UI: `CHOOSE_COMPLAINT_TYPE`, `COMPLAINT_DETAILS`, `DESCRIBE_COMPLAINT`, `CONFIRM_ACTION`, `LOGIN_REQUIRED`, and the four `CLARIFY_*` codes. The UI should choose forms from `allowed_input_types`; `DESCRIBE_COMPLAINT` and `CLARIFY_*` are text-only and appear only when a model can read the answer.

**Needed from Harry:** `ConversationService(..., simulation_now=...)` must receive an async function returning the scoped run's `simulation_clock`. It is not in the facade contract yet.

## Decisions, handoff and knowledge (T-03)

- **Text chat:** only the explicit `action_decision` control confirms. A typed "yes", however clear, gets the button prompt.
- **Voice:** a spoken answer is sent to Resolve only when the Voice bridge supplies `VoiceConsentEvidence` whose proposal ID/hash match the pending proposal and whose `final_transcript` equals the turn text, *and* the model classifies the answer as a clear `ACCEPT` or `DECLINE`. `UNCLEAR`, missing/mismatched evidence or a model failure asks again. Resolve still makes the final acceptance decision.
- Declines are always sent to Resolve so they are recorded. `CONFIRMATION_REQUIRED` keeps the offer open and asks again; expired/invalidated/not-allowed clear it.
- Accepted review tickets report the request ID and the operation state. A ticket number is shown only when the operation outcome or receipt handoff actually carries `provider_ticket_id`; a CRM outage reads as "pending, no ticket number yet".
- Knowledge replies cite the reviewed card. `SYNTHETIC` cards are prefixed as demo policy, separate from `PUBLIC` HUTCH facts.

## Secondary paths and telemetry (T-04)

- B/C/E/F run through the same paths. Resolve's `review_reasons` are added to the reply (e.g. future renewal vs past charge).
- When Resolve makes **several** actions eligible, the first is offered as a normal Accept/Decline card and the rest are mentioned. Declining or accepting offers the next one, so **buttons alone reach every option** (works with no model). With a model, the customer can name another listed option to switch; the model can only pick an action Resolve listed for the active case, and choosing never confirms.
- Each model attempt produces a `ModelCallRecord` (request/conversation/case IDs, purpose, prompt version, attempt, provider, model, outcome, latency, provider-reported tokens, error class). It has no field for prompt, transcript or output. **Harry:** implement `ModelTelemetry.record_model_call` into `resolve.model_calls`; telemetry failures are logged and never fail a turn.

## Language

The customer never has to pick a language. Each text message's detected language and script (from extraction) become the conversation language; button clicks keep it, and a bare "ok" does not switch back to English. The UI's `language` field is only used before anything was detected. With a `ReplyRewriter`, non-English replies are machine-written from the English source and fact-checked by code. They are **not reviewed by a fluent speaker**, so label them that way in demos and documents. Reviewed `locales/*.json` wording remains the path to reviewed native-language replies.

## Time budget and audit

- Each turn has one deadline shared by all model calls: 15 s for text, **7 s for Voice** (its read timeout is 8 s). Extraction gets at most the remaining time; the reply rewrite is skipped when less than 1.5 s remains.
- A rewritten reply keeps its English original in `TurnDraft.source_reply_text`. Turn storage must persist it with the assistant message, so history and audit retain Resolve's wording.
- Reviewed knowledge (FAQ) replies are never machine-rewritten; they stay exactly as reviewed, with their citation.

## Fingerprint rule

The fingerprint covers channel, language, input and Voice evidence, but **not** `expected_version`. The version is concurrency control: the Voice bridge reads it from the binding at receipt time, so including it would turn a legitimate same-turn retry into a false `IDEMPOTENCY_CONFLICT`.

## Integration with Harry's facade

`ResolveFacadeAdapter(ResolveFacade(app_engine, cursor_secret=...))` is what the conversation service receives in the real app. Known deviations it absorbs or exposes are listed in `docs/plans/tevin.md` ("Integration findings").

```bash
PYTHON=~/.venvs/hutch/bin/python sh tests/conversation/run_integration.sh
```

This starts a **throwaway** PostgreSQL on port 55433 (never the shared container), applies Harry's migrations, runs `tests/conversation/test_resolve_integration.py` against his real facade, and removes the container. Turn storage is still the in-memory fake until Harry's repository exists.

## Dev backend (simulation for end-to-end testing)

`tests/conversation/dev_backend.py` serves the `/api/v1` customer routes Jayith's chat uses in live mode, with this module and real Gemini behind them. It is a test tool, not Harry's API, and nothing in the app imports it.

| `RESOLVE_BACKEND` | Resolve answers come from |
| --- | --- |
| `dummy` (default) | `tests/conversation/fakes.py`: contract examples for A/D, stand-ins for B/C/E/F, each line answering only its own complaint; simulated operation success and receipts |
| `hybrid` | Harry's real facade (adapter) for all four complaint types, offers, confirmations, his `OperationRunner` and receipts. Only turn storage and sign-in are dev stand-ins. Needs `sh tests/conversation/hybrid_db.sh start` (throwaway DB). Pick a line at `/api/v1/dev`: Harry's fixtures hold one problem per line |
| `real` | Not available until Harry ships turn storage and the conversation routes |

Start `dev-backend` and `frontend-live`, then just open **http://localhost:5174** and talk. In dummy mode you are one demo customer whose records hold every problem: an extra deduction gets A's breakdown, a missing reload E, data B, connection C, an unknown service F. This is a simulation shortcut, not evidence. The chat opens with suggestion chips (`opening_question`), and replies follow the language you write in. To test one specific line (e.g. D's conflict), open http://localhost:5174/api/v1/dev. The same journeys run against the dummy and the real facade in `test_resolve_integration.py`; a pass on both is what keeps the dummy honest.

## Tests

Dependencies used: `pydantic>=2.8,<3`, `google-genai` (tested with 2.27.0; imported only by `GeminiModelClient`), `pytest>=8`. Harry pins the app lock.

```bash
python -m pytest tests/conversation -q
```

Fakes in `tests/conversation/fakes.py` return the published contract examples (A sufficient, D conflicting, partial evidence) and a scripted model. They model contract semantics only; they are not a second implementation of business rules, and scripted model answers say nothing about real Gemini accuracy.
