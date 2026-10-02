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

Pending question codes for the UI: `CHOOSE_COMPLAINT_TYPE`, `COMPLAINT_DETAILS`, `CONFIRM_ACTION`, `CHOOSE_ACTION`, `LOGIN_REQUIRED`, and the four `CLARIFY_*` codes. The UI should choose forms from `allowed_input_types`; `CLARIFY_*` and `CHOOSE_ACTION` are text-only.

**Needed from Harry:** `ConversationService(..., simulation_now=...)` must receive an async function returning the scoped run's `simulation_clock`. It is not in the facade contract yet.

## Decisions, handoff and knowledge (T-03)

- **Text chat:** only the explicit `action_decision` control confirms. A typed "yes", however clear, gets the button prompt.
- **Voice:** a spoken answer is sent to Resolve only when the Voice bridge supplies `VoiceConsentEvidence` whose proposal ID/hash match the pending proposal and whose `final_transcript` equals the turn text, *and* the model classifies the answer as a clear `ACCEPT` or `DECLINE`. `UNCLEAR`, missing/mismatched evidence or a model failure asks again. Resolve still makes the final acceptance decision.
- Declines are always sent to Resolve so they are recorded. `CONFIRMATION_REQUIRED` keeps the offer open and asks again; expired/invalidated/not-allowed clear it.
- Accepted review tickets report the request ID and the operation state. A ticket number is shown only when the operation outcome or receipt handoff actually carries `provider_ticket_id`; a CRM outage reads as "pending, no ticket number yet".
- Knowledge replies cite the reviewed card. `SYNTHETIC` cards are prefixed as demo policy, separate from `PUBLIC` HUTCH facts.

## Secondary paths and telemetry (T-04)

- B/C/E/F run through the same paths. Resolve's `review_reasons` are added to the reply (e.g. future renewal vs past charge).
- When Resolve makes **several** actions eligible, the reply lists them with pending question `CHOOSE_ACTION` (text only). A choice must match an action Resolve listed for the active case, and only requests a proposal; consent still needs the Accept control.
- Each model attempt produces a `ModelCallRecord` (request/conversation/case IDs, purpose, prompt version, attempt, provider, model, outcome, latency, provider-reported tokens, error class). It has no field for prompt, transcript or output. **Harry:** implement `ModelTelemetry.record_model_call` into `resolve.model_calls`; telemetry failures are logged and never fail a turn.

## Fingerprint rule

The fingerprint covers channel, language, input and Voice evidence, but **not** `expected_version`. The version is concurrency control: the Voice bridge reads it from the binding at receipt time, so including it would turn a legitimate same-turn retry into a false `IDEMPOTENCY_CONFLICT`.

## Integration with Harry's facade

`ResolveFacadeAdapter(ResolveFacade(app_engine, provider_engine=sandbox_engine))` is what the conversation service receives in the real app. Known deviations it absorbs or exposes are listed in `docs/plans/tevin.md` ("Integration findings").

```bash
PYTHON=~/.venvs/hutch/bin/python sh tests/conversation/run_integration.sh
```

This starts a **throwaway** PostgreSQL on port 55433 (never the shared container), applies Harry's migrations, runs `tests/conversation/test_resolve_integration.py` against his real facade, and removes the container. Turn storage is still the in-memory fake until Harry's repository exists.

## Tests

Dependencies used: `pydantic>=2.8,<3`, `google-genai` (tested with 2.27.0; imported only by `GeminiModelClient`), `pytest>=8`. Harry pins the app lock.

```bash
python -m pytest tests/conversation -q
```

Fakes in `tests/conversation/fakes.py` return the published contract examples (A sufficient, D conflicting, partial evidence) and a scripted model. They model contract semantics only; they are not a second implementation of business rules, and scripted model answers say nothing about real Gemini accuracy.
