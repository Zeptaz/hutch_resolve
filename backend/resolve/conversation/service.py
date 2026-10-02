"""ConversationService.handle_turn: the single entrypoint for text and Voice.

Lifecycle per turn (T-01):
  1. fingerprint the normalized turn and claim it in storage (replay check first);
  2. route the input; every business fact comes from ResolveFacade;
  3. store messages, result and dialogue state, advancing the version once.

Free text (T-02) is classified by the model into an intent plus customer-reported
candidates, validated, then routed through the same facade paths as the
structured inputs. Without a model, or when it fails or exceeds its budget,
the structured forms remain fully usable.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from . import templates as t
from .dto import (
    AccountCard,
    AuthContext,
    CalculationCard,
    CaseSelectionInput,
    CaseView,
    CategoryInput,
    Channel,
    ComplaintType,
    ConfirmationCard,
    ConfirmationRequest,
    Citation,
    Decision,
    DecisionInput,
    DetailsInput,
    EscalationRequest,
    EvidenceState,
    FindingCard,
    InputType,
    InvestigationRequest,
    InvestigationResult,
    Language,
    NormalizedTurn,
    PendingQuestion,
    ProposalRequest,
    ProposalView,
    ActionType,
    OperationView,
    ReceiptCard,
    ReceiptCardData,
    ReportedFacts,
    Role,
    TextInput,
    TicketCard,
    TurnResult,
)
from .errors import ResolveError
from .extraction import (
    AMBIGUITY_PRIORITY,
    Ambiguity,
    Extraction,
    ExtractionContext,
    ExtractionOutcome,
    Extractor,
    Intent,
    PROMPT_VERSION,
    SpokenDecision,
    TimeKind,
    default_window,
    resolve_window,
)
from .identity import command_key, turn_fingerprint
from .answer import ANSWER_PROMPT_VERSION, GroundedAnswerer
from .rewrite import REWRITE_PROMPT_VERSION, ReplyRewriter
from .ports import (
    ConversationRepository,
    KnowledgeRepository,
    ModelCallRecord,
    ModelTelemetry,
    NullTelemetry,
    Replay,
    ResolveFacade,
    TurnDraft,
)
from .state import ActionChoice, Candidate, DialogueState, PendingProposalRef

MAX_WINDOW = timedelta(days=30)
# One deadline per turn shared by every model call: Voice must answer inside its 8 s read timeout.
TURN_BUDGET_SECONDS = {Channel.TEXT: 15.0, Channel.VOICE: 7.0}
MIN_REWRITE_SECONDS = 1.5  # below this, keep the English reply rather than risk the deadline
_DEADLINE: ContextVar[float | None] = ContextVar("turn_deadline", default=None)
# Knowledge topics where a signed-in customer's current balance is useful context.
_BALANCE_TOPICS = {"how-to-reload", "check-balance-how", "reload-not-received", "prepaid-recharge"}
_CODE = re.compile(r"[A-Z0-9_]+")

# Pending question codes shared with the frontend.
Q_CHOOSE_COMPLAINT = "CHOOSE_COMPLAINT_TYPE"
Q_COMPLAINT_DETAILS = "COMPLAINT_DETAILS"
Q_LOGIN_REQUIRED = "LOGIN_REQUIRED"
Q_CONFIRM_ACTION = "CONFIRM_ACTION"
Q_DESCRIBE_COMPLAINT = "DESCRIBE_COMPLAINT"
# Text only: a details form carries its own complaint type and could override the one being collected.
Q_CLARIFY = {
    Ambiguity.TIME_WINDOW: ("CLARIFY_TIME_WINDOW", "clarify_time_window", ["text"]),
    Ambiguity.AMOUNT: ("CLARIFY_AMOUNT", "clarify_amount", ["text"]),
    Ambiguity.TARGET: ("CLARIFY_TARGET", "clarify_target", ["text"]),
    Ambiguity.NEGATION: ("CLARIFY_NEGATION", "clarify_negation", ["text"]),
}

# Facade outcomes the customer should hear about; others propagate to the API layer.
_PROPOSAL_ERROR_TEMPLATES = {
    "PROPOSAL_EXPIRED": "proposal_expired",
    "PROPOSAL_INVALIDATED": "proposal_invalidated",
    "ACTION_NOT_ALLOWED": "not_allowed",
}
# Resolve rejected the consent evidence but the proposal itself is still valid: ask again.
_ASK_AGAIN_CODES = {"CONFIRMATION_REQUIRED"}

# Intents that continue a complaint still being collected rather than starting something else.
_CONTINUES_CANDIDATE = {Intent.NEW_COMPLAINT, Intent.CORRECTION, Intent.FOLLOW_UP, Intent.OTHER}

SimulationClock = Callable[[AuthContext], Awaitable[datetime]]

log = logging.getLogger(__name__)


async def _real_time(ctx: AuthContext) -> datetime:
    return datetime.now(UTC)


Step = tuple[TurnDraft, DialogueState]


class ConversationService:
    def __init__(
        self,
        facade: ResolveFacade,
        conversations: ConversationRepository,
        knowledge: KnowledgeRepository,
        extractor: Extractor | None = None,
        simulation_now: SimulationClock = _real_time,
        telemetry: ModelTelemetry | None = None,
        rewriter: ReplyRewriter | None = None,
        turn_budgets: dict[Channel, float] | None = None,
        answerer: GroundedAnswerer | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """`simulation_now` returns the scoped run's simulation clock (Harry); business
        windows use it, while auth and proposal expiry stay on real time in Resolve."""
        self._facade = facade
        self._conversations = conversations
        self._knowledge = knowledge
        self._extractor = extractor
        self._simulation_now = simulation_now
        self._telemetry = telemetry or NullTelemetry()
        self._rewriter = rewriter  # replies in the customer's language/style (machine-written; see rewrite.py)
        self._budgets = {**TURN_BUDGET_SECONDS, **(turn_budgets or {})}
        self._answerer = answerer  # grounded how-to answers from knowledge cards (answer.py)
        self._monotonic = monotonic

    async def handle_turn(self, ctx: AuthContext, turn: NormalizedTurn) -> TurnResult:
        _check_trusted_fields(ctx, turn)
        outcome = await self._conversations.claim_turn(
            ctx, turn.conversation_id, turn.turn_id, turn_fingerprint(turn), turn.expected_version
        )
        if isinstance(outcome, Replay):
            return outcome.result
        claim = outcome.claim
        token = _DEADLINE.set(self._monotonic() + self._budgets[turn.channel])
        try:
            start = claim.state
            if start.script is None:  # nothing detected from the customer yet: use the UI hint
                start = start.evolve(language=turn.language)
            draft, state = await self._route(ctx, turn, start)
            draft = await self._localize(ctx, turn, draft, state)
        except Exception:
            # Same-id retry re-claims; deterministic command keys stop Resolve repeating work.
            await self._conversations.release_turn(ctx, claim)
            raise
        finally:
            _DEADLINE.reset(token)
        return await self._conversations.complete_turn(ctx, claim, _user_body(turn, state.language), draft, state)

    def _remaining(self) -> float:
        deadline = _DEADLINE.get()
        return float("inf") if deadline is None else deadline - self._monotonic()

    async def _route(self, ctx: AuthContext, turn: NormalizedTurn, state: DialogueState) -> Step:
        inp = turn.input
        if isinstance(inp, TextInput):
            return await self._on_text(ctx, turn, inp, state)
        if not _is_customer(ctx):
            return _login_required(state)
        if isinstance(inp, CategoryInput):
            return self._on_category(inp, state)
        if isinstance(inp, DetailsInput):
            return await self._on_details(ctx, turn, inp, state)
        if isinstance(inp, DecisionInput):
            return await self._on_decision(ctx, turn, inp, state)
        if isinstance(inp, CaseSelectionInput):
            return await self._on_case_selection(ctx, turn, inp, state)
        raise ResolveError("VALIDATION_ERROR", "unsupported input type")

    # --- free text ------------------------------------------------------------

    async def _on_text(self, ctx: AuthContext, turn: NormalizedTurn, inp: TextInput, state: DialogueState) -> Step:
        now = await self._simulation_now(ctx)
        extraction = await self._extract(ctx, turn, inp.text, state, now)
        if extraction is None:
            return self._structured_fallback(ctx, state)
        if extraction.detected_language is not Language.EN or len(inp.text.split()) >= 3:
            # Reply in the language the customer writes; a bare "ok" does not switch back to English.
            state = state.evolve(language=extraction.detected_language, script=extraction.script)

        intent = extraction.intent
        choice = _match_choice(state, extraction)
        if choice is not None:
            return await self._choose_action(ctx, turn, choice, state)
        if intent is Intent.ACTION_DECISION:
            if state.pending_proposal is None:
                return TurnDraft(reply_text=t.text("no_pending_action", state.language), case_id=state.active_case_id), state
            if turn.channel is Channel.VOICE and _is_customer(ctx):
                return await self._spoken_decision(ctx, turn, inp, extraction, state)
            # Typed "yes" is never consent: text chat uses the explicit action_decision control.
            return _confirm_prompt(state, turn.channel)
        if intent is Intent.FAQ:
            return await self._faq(ctx, inp.text, extraction, state)
        if not _is_customer(ctx):
            if intent is Intent.OTHER:
                return _ask(state, Q_LOGIN_REQUIRED, t.text("guest_help", state.language), ["text"])
            return _login_required(state)

        if state.candidate is not None and intent in _CONTINUES_CANDIDATE:
            return await self._collect_complaint(ctx, turn, extraction, state, state.candidate, now)
        match intent:
            case Intent.ACCOUNT_ENQUIRY:
                return await self._account(ctx, state)
            case Intent.NEW_COMPLAINT:
                return await self._collect_complaint(ctx, turn, extraction, state, Candidate(), now)
            case Intent.CORRECTION if state.active_case_id:
                return await self._correct(ctx, turn, extraction, state, now)
            case Intent.FOLLOW_UP if state.active_case_id:
                return await self._follow_up(ctx, state)
            case Intent.STATUS:
                return await self._status(ctx, state)
            case Intent.HUMAN_REQUEST:
                return await self._human_request(ctx, turn, extraction, state)
            case Intent.CORRECTION | Intent.FOLLOW_UP if extraction.complaint_type:
                return await self._collect_complaint(ctx, turn, extraction, state, Candidate(), now)
            case Intent.OTHER if state.active_case_id and state.pending_proposal is None:
                # "thanks", "ok" after a case: offer more help instead of restarting the menu.
                return _ask(state, Q_CHOOSE_COMPLAINT, t.text("anything_else", state.language), ["category_selection", "text"])
        return _ask(state, Q_CHOOSE_COMPLAINT, t.text("choose_complaint", state.language), ["category_selection", "text"])

    async def _extract(
        self, ctx: AuthContext, turn: NormalizedTurn, text: str, state: DialogueState, now: datetime
    ) -> Extraction | None:
        if self._extractor is None:
            return None
        active_type = None
        if state.active_case_id is not None and _is_customer(ctx):
            try:
                active_type = (await self._facade.get_case(ctx, state.active_case_id)).complaint_type
            except ResolveError as err:
                if err.code != "RESOURCE_NOT_FOUND":
                    raise
        context = ExtractionContext(
            now=now,
            pending_question_code=state.pending_question.code if state.pending_question else None,
            candidate_complaint_type=state.candidate.complaint_type if state.candidate else None,
            active_case_complaint_type=active_type,
            has_pending_proposal=state.pending_proposal is not None,
            actions_offered=tuple(c.action_type.value for c in state.pending_choices),
        )
        outcome = await self._extractor.extract(text, context, budget_seconds=self._remaining())
        await self._record_usage(ctx, turn, state, outcome)
        return outcome.extraction

    async def _localize(self, ctx: AuthContext, turn: NormalizedTurn, draft: TurnDraft, state: DialogueState) -> TurnDraft:
        """Re-express the reply in the customer's language/style; keep English on any doubt."""
        if self._rewriter is None or self._rewriter.style_for(state.language, state.script) is None:
            return draft
        if draft.citations:
            return draft  # reviewed knowledge text is shown exactly as reviewed, never machine-rewritten
        remaining = self._remaining()
        if remaining < MIN_REWRITE_SECONDS:
            return draft  # not enough of the turn budget left (e.g. Voice after a slow extraction)
        outcome = await self._rewriter.rewrite(draft.reply_text, state.language, state.script, budget_seconds=remaining - 0.5)
        client, reply = self._rewriter.client, outcome.reply
        await self._safe_record(ctx, ModelCallRecord(
            request_id=ctx.request_id, conversation_id=turn.conversation_id, case_id=draft.case_id or state.active_case_id,
            purpose="REPLY_REWRITE", prompt_version=REWRITE_PROMPT_VERSION, attempt=1,
            provider=reply.provider if reply else client.provider, model=reply.model if reply else client.model_name,
            outcome=outcome.failure or "OK", latency_ms=outcome.latency_ms,
            input_tokens=reply.input_tokens if reply else None, output_tokens=reply.output_tokens if reply else None,
            error_type=None,
        ))
        if outcome.text is None:
            return draft
        question = draft.pending_question
        if question is not None and question.text == draft.reply_text:
            question = question.model_copy(update={"text": outcome.text})
        return TurnDraft(reply_text=outcome.text, case_id=draft.case_id, cards=draft.cards, citations=draft.citations,
                         pending_question=question, operation_ids=draft.operation_ids, source_reply_text=draft.reply_text)

    async def _safe_record(self, ctx: AuthContext, record: ModelCallRecord) -> None:
        try:
            await self._telemetry.record_model_call(ctx, record)
        except Exception:  # noqa: BLE001
            log.warning("model telemetry write failed", extra={"request_id": str(ctx.request_id), "outcome": record.outcome})

    async def _record_usage(self, ctx: AuthContext, turn: NormalizedTurn, state: DialogueState, outcome: ExtractionOutcome) -> None:
        """Usage telemetry must never block or fail a customer turn."""
        client = self._extractor.client
        for attempt in outcome.attempts:
            reply = attempt.reply
            record = ModelCallRecord(
                request_id=ctx.request_id,
                conversation_id=turn.conversation_id,
                case_id=state.active_case_id,
                purpose="EXTRACTION",
                prompt_version=PROMPT_VERSION,
                attempt=attempt.number,
                provider=reply.provider if reply else client.provider,
                model=reply.model if reply else client.model_name,
                outcome=attempt.outcome,
                latency_ms=attempt.latency_ms,
                input_tokens=reply.input_tokens if reply else None,
                output_tokens=reply.output_tokens if reply else None,
                error_type=attempt.error_type,
            )
            try:
                await self._telemetry.record_model_call(ctx, record)
            except Exception:  # noqa: BLE001
                log.warning("model telemetry write failed", extra={"request_id": str(ctx.request_id), "outcome": attempt.outcome})

    async def _spoken_decision(
        self, ctx: AuthContext, turn: NormalizedTurn, inp: TextInput, ex: Extraction, state: DialogueState
    ) -> Step:
        """Voice consent: a clear decision about the exact proposal the caller just heard.

        The bridge's VoiceConsentEvidence proves presentation; Resolve makes the final
        acceptance decision. Anything unclear, stale or mismatched asks again.
        """
        pending = state.pending_proposal
        evidence = turn.voice_evidence
        heard_this_offer = (
            evidence is not None
            and evidence.presented_proposal_id == pending.proposal_id
            and evidence.presented_proposal_hash == pending.proposal_hash
            and evidence.final_transcript == inp.text
        )
        if not heard_this_offer or ex.decision not in (SpokenDecision.ACCEPT, SpokenDecision.DECLINE):
            return _confirm_prompt(state, Channel.VOICE)
        decision = Decision.ACCEPT if ex.decision is SpokenDecision.ACCEPT else Decision.DECLINE
        return await self._confirm(ctx, turn, state, decision, evidence)

    def _structured_fallback(self, ctx: AuthContext, state: DialogueState) -> Step:
        if state.pending_proposal is not None:
            return _confirm_prompt(state, ctx.channel)
        if not _is_customer(ctx):
            return _login_required(state)
        if state.candidate is not None and state.candidate.complaint_type is not None:
            return _ask(state, Q_COMPLAINT_DETAILS, t.text("complaint_details", state.language), ["complaint_details", "text"])
        return _ask(state, Q_CHOOSE_COMPLAINT, t.text("choose_complaint", state.language), ["category_selection", "text"])

    async def _collect_complaint(
        self,
        ctx: AuthContext,
        turn: NormalizedTurn,
        ex: Extraction,
        state: DialogueState,
        base: Candidate,
        now: datetime,
    ) -> Step:
        """Merge new candidates, ask at most one clarification per turn, then investigate."""
        lang = state.language
        window = resolve_window(ex.time_reference, now)
        ambiguities = list(dict.fromkeys([*base.ambiguities, *ex.ambiguities]))
        if ex.time_reference.kind is not TimeKind.NONE and window is None:
            ambiguities.append(Ambiguity.TIME_WINDOW)  # stated but invalid (future, >30 days, reversed)
        if window is not None:
            ambiguities = [a for a in ambiguities if a is not Ambiguity.TIME_WINDOW]

        candidate = Candidate(
            complaint_type=ex.complaint_type or base.complaint_type,
            window_start=window[0] if window else base.window_start,
            window_end=window[1] if window else base.window_end,
            reported_facts=_merge_facts(base.reported_facts, ex),
            ambiguities=ambiguities,
            clarified=base.clarified,
        )

        if candidate.complaint_type is None:
            return _ask(
                state.evolve(candidate=candidate), Q_CHOOSE_COMPLAINT, t.text("choose_complaint", lang), ["category_selection", "text"]
            )
        for ambiguity in AMBIGUITY_PRIORITY:
            if ambiguity in Q_CLARIFY and ambiguity in candidate.ambiguities and ambiguity not in candidate.clarified:
                code, key, allowed = Q_CLARIFY[ambiguity]
                asked = candidate.model_copy(update={"clarified": [*candidate.clarified, ambiguity]})
                return _ask(state.evolve(candidate=asked), code, t.text(key, lang), allowed)

        if candidate.window_start and candidate.window_end:
            start, end, defaulted = candidate.window_start, candidate.window_end, False
        else:
            (start, end), defaulted = default_window(now), True
        draft, new_state = await self._open_and_investigate(
            ctx, turn, candidate.complaint_type, start, end, candidate.reported_facts, state
        )
        prefix = _checked_prefix(candidate.complaint_type, candidate.reported_facts, start, end, defaulted, lang)
        return _prefixed(draft, prefix), new_state

    async def _correct(self, ctx: AuthContext, turn: NormalizedTurn, ex: Extraction, state: DialogueState, now: datetime) -> Step:
        """Changed facts request a new investigation revision of the same case through Resolve."""
        lang = state.language
        case = await self._facade.get_case(ctx, state.active_case_id)
        previous = case.investigation
        window = resolve_window(ex.time_reference, now)
        if previous is None:
            base = Candidate(complaint_type=case.complaint_type)
            return await self._collect_complaint(ctx, turn, ex, state, base, now)
        if window is None and ex.amount_minor is None and ex.recharge_reference is None:
            code, key, allowed = Q_CLARIFY[Ambiguity.TIME_WINDOW]
            return _ask(state, code, t.text(key, lang), allowed)

        start, end = window or (previous.window_start, previous.window_end)
        investigation = await self._facade.investigate(
            ctx,
            case.id,
            InvestigationRequest(
                expected_version=case.version,
                complaint_type=case.complaint_type,
                window_start=start,
                window_end=end,
                reported_facts=_merge_facts(ReportedFacts(), ex),
            ),
            command_key(turn.conversation_id, turn.turn_id, "reinvestigate", case.id),
        )
        # Resolve invalidates proposals bound to the previous revision.
        proposal, alternatives = await self._propose_first(ctx, turn, case.id, investigation)
        draft = _investigation_draft(investigation, proposal, lang, alternatives)
        new_state = _after_investigation(state, turn, case.id, proposal, draft, alternatives)
        return _prefixed(draft, t.text("rechecked", lang, window=t.format_window(start, end))), new_state

    async def _follow_up(self, ctx: AuthContext, state: DialogueState) -> Step:
        """Answer from the saved investigation; never re-investigate or re-propose."""
        lang = state.language
        case = await self._facade.get_case(ctx, state.active_case_id)
        if case.investigation is None:
            return _ask(state, Q_COMPLAINT_DETAILS, t.text("complaint_details", lang), ["complaint_details", "text"])
        draft = _investigation_draft(case.investigation, None, lang)
        pending = state.pending_proposal if state.pending_proposal and state.pending_proposal.case_id == case.id else None
        if pending is not None:
            question = PendingQuestion(code=Q_CONFIRM_ACTION, text=t.text("confirm_prompt", lang), allowed_input_types=["action_decision", "text"])
            draft = TurnDraft(
                reply_text=f"{draft.reply_text} {t.text('offer_still_open', lang)}",
                case_id=case.id,
                cards=draft.cards,
                pending_question=question,
            )
            return draft, state.evolve(pending_question=question)
        return draft, state.evolve(pending_question=None)

    async def _account(self, ctx: AuthContext, state: DialogueState) -> Step:
        lang = state.language
        account = await self._facade.get_account(ctx)
        parts = [
            t.text("account_balance", lang, wallet=b.wallet.lower(), amount=t.format_lkr(b.amount_minor), as_of=t.format_time(b.as_of))
            for b in account.balances
        ] or [t.text("account_no_balance", lang)]
        if any(not s.complete for s in account.source_status):
            parts.append(t.text("account_incomplete", lang))
        return TurnDraft(reply_text=" ".join(parts), case_id=state.active_case_id, cards=[AccountCard(data=account)]), state.evolve(
            pending_question=None
        )

    async def _status(self, ctx: AuthContext, state: DialogueState) -> Step:
        lang = state.language
        if state.active_case_id is None:
            return _ask(state, Q_CHOOSE_COMPLAINT, t.text("no_active_case", lang), ["category_selection", "text"])
        case = await self._facade.get_case(ctx, state.active_case_id)
        parts = [t.text("case_status", lang, complaint=t.complaint_label(case.complaint_type, lang), status=t.case_status_label(case.status, lang))]
        operation_ids = []
        if case.operation_ids:
            operation = await self._facade.get_operation(ctx, case.operation_ids[-1])
            parts.append(t.operation_status_text(operation.status, lang))
            operation_ids = [operation.id]
        cards: list = []
        if case.receipt is not None:
            receipt = await self._facade.get_receipt(ctx, case.id)
            if receipt.handoff is not None:
                parts.append(t.handoff_text(receipt.handoff, lang))
                cards.append(TicketCard(data=receipt.handoff))
            parts.append(t.text("receipt_ready", lang))
            cards.append(ReceiptCard(data=ReceiptCardData(case_id=case.id, receipt_id=receipt.id, revision=receipt.revision)))
        draft = TurnDraft(reply_text=" ".join(parts), case_id=case.id, cards=cards, operation_ids=operation_ids)
        return draft, state

    async def _human_request(self, ctx: AuthContext, turn: NormalizedTurn, ex: Extraction, state: DialogueState) -> Step:
        """A person reviews a case with its evidence, so a case must exist first."""
        lang = state.language
        case = await self._facade.get_case(ctx, state.active_case_id) if state.active_case_id else None
        if case is None or case.investigation is None:
            return _ask(state, Q_CHOOSE_COMPLAINT, t.text("human_needs_case", lang), ["category_selection", "text"])
        proposal = await self._facade.prepare_escalation(
            ctx,
            case.id,
            EscalationRequest(
                expected_version=case.version,
                investigation_id=case.investigation.id,
                reason=ex.summary or t.text("default_escalation_reason", Language.EN),
            ),
            command_key(turn.conversation_id, turn.turn_id, "escalate", case.id),
        )
        draft = _offer_draft(proposal, lang, case.id)
        return draft, state.evolve(pending_question=draft.pending_question, pending_proposal=_proposal_ref(proposal, turn))

    async def _faq(self, ctx: AuthContext, text: str, ex: Extraction, state: DialogueState) -> Step:
        """Reviewed knowledge cards only; guests allowed; never account data."""
        lang = state.language
        cards = await self._knowledge.search(ctx, ex.faq_query or text, lang, limit=3)
        if not cards:
            return TurnDraft(reply_text=t.text("faq_none", lang), case_id=state.active_case_id), state
        if self._answerer is not None and self._remaining() >= MIN_REWRITE_SECONDS:
            grounded = await self._grounded_answer(ctx, text, cards, state)
            if grounded is not None:
                return grounded, state
        card = cards[0]
        citation = Citation(
            article_id=card.article_id, title=card.title, url=card.url, reviewed_at=card.reviewed_at, version=card.version, scope=card.scope
        )
        reply = card.content if card.scope == "PUBLIC" else f"{t.text('synthetic_policy', lang)} {card.content}"
        return TurnDraft(reply_text=reply, case_id=state.active_case_id, citations=[citation]), state

    async def _grounded_answer(self, ctx: AuthContext, text: str, cards: list, state: DialogueState) -> TurnDraft | None:
        """Model-written answer from the cards only (code-checked); None falls back to the card text."""
        account_fact, extra_cards = None, []
        if _is_customer(ctx) and cards[0].article_key in _BALANCE_TOPICS:
            try:
                account = await self._facade.get_account(ctx)
                main = next((b for b in account.balances if b.wallet.upper() == "MAIN"), None)
                if main is not None:
                    account_fact = f"The customer's current main balance is {t.format_lkr(main.amount_minor)} (as of {t.format_time(main.as_of)})."
                    extra_cards = [AccountCard(data=account)]
            except ResolveError:
                pass
        outcome = await self._answerer.answer(text, cards, state.language, state.script, account_fact,
                                              budget_seconds=self._remaining() - 0.5)
        client, reply = self._answerer.client, outcome.reply
        await self._safe_record(ctx, ModelCallRecord(
            request_id=ctx.request_id, conversation_id=state.active_case_id or ctx.session_id, case_id=state.active_case_id,
            purpose="FAQ_ANSWER", prompt_version=ANSWER_PROMPT_VERSION, attempt=1,
            provider=reply.provider if reply else client.provider, model=reply.model if reply else client.model_name,
            outcome=outcome.failure or "OK", latency_ms=outcome.latency_ms,
            input_tokens=reply.input_tokens if reply else None, output_tokens=reply.output_tokens if reply else None,
            error_type=None,
        ))
        if outcome.text is None:
            return None
        citations = [Citation(article_id=c.article_id, title=c.title, url=c.url, reviewed_at=c.reviewed_at,
                              version=c.version, scope=c.scope) for c in outcome.used]
        return TurnDraft(reply_text=outcome.text, case_id=state.active_case_id, cards=extra_cards, citations=citations,
                         source_reply_text=" ".join(c.content for c in outcome.used))

    # --- structured paths -----------------------------------------------------

    def _on_category(self, inp: CategoryInput, state: DialogueState) -> Step:
        base = state.candidate or Candidate()
        state = state.evolve(candidate=base.model_copy(update={"complaint_type": inp.complaint_type}))
        if self._extractor is not None:
            # Ask in plain words; the details form is the fallback when no model can read the answer.
            reply = t.text("describe_complaint", state.language, complaint=t.complaint_label(inp.complaint_type, state.language))
            return _ask(state, Q_DESCRIBE_COMPLAINT, reply, ["text"])
        return _ask(state, Q_COMPLAINT_DETAILS, t.text("complaint_details", state.language), ["complaint_details", "text"])

    async def _on_details(self, ctx: AuthContext, turn: NormalizedTurn, inp: DetailsInput, state: DialogueState) -> Step:
        lang = state.language
        window_problem = _window_problem(inp)
        if window_problem:
            candidate = Candidate(complaint_type=inp.complaint_type, reported_facts=inp.reported_facts, ambiguities=[Ambiguity.TIME_WINDOW])
            return _ask(state.evolve(candidate=candidate), Q_COMPLAINT_DETAILS, t.text(window_problem, lang), ["complaint_details", "text"])
        draft, new_state = await self._open_and_investigate(
            ctx, turn, inp.complaint_type, inp.window_start, inp.window_end, inp.reported_facts, state
        )
        prefix = _checked_prefix(inp.complaint_type, inp.reported_facts, inp.window_start, inp.window_end, False, lang)
        return _prefixed(draft, prefix), new_state

    async def _open_and_investigate(
        self,
        ctx: AuthContext,
        turn: NormalizedTurn,
        complaint_type: ComplaintType,
        start: datetime,
        end: datetime,
        facts: ReportedFacts,
        state: DialogueState,
    ) -> Step:
        conv_id, turn_id = turn.conversation_id, turn.turn_id
        # Each complaint gets its own case; never silently attach to the active one.
        case = await self._facade.create_case(
            ctx, conv_id, turn_id, complaint_type, expected_conversation_version=turn.expected_version
        )
        investigation = await self._facade.investigate(
            ctx,
            case.id,
            InvestigationRequest(
                expected_version=case.version,
                complaint_type=complaint_type,
                window_start=start,
                window_end=end,
                reported_facts=facts,
            ),
            command_key(conv_id, turn_id, "investigate", case.id),
        )
        proposal, alternatives = await self._propose_first(ctx, turn, case.id, investigation)
        draft = _investigation_draft(investigation, proposal, state.language, alternatives)
        return draft, _after_investigation(state, turn, case.id, proposal, draft, alternatives)

    async def _choose_action(self, ctx: AuthContext, turn: NormalizedTurn, choice: ActionChoice, state: DialogueState) -> Step:
        """The customer named another listed option: offer it instead. Consent still comes later."""
        proposal = await self._request_proposal(ctx, turn, choice)
        rest = [c for c in state.pending_choices if c != choice]
        previous = _pending_as_choice(state.pending_proposal)
        if previous is not None and previous.action_type is not choice.action_type and previous not in rest:
            rest.append(previous)  # the earlier offer stays reachable
        draft = _offer_with_alternatives(proposal, rest, state.language, choice.case_id)
        return draft, state.evolve(
            pending_choices=rest, pending_question=draft.pending_question, pending_proposal=_proposal_ref(proposal, turn)
        )

    async def _propose_first(
        self, ctx: AuthContext, turn: NormalizedTurn, case_id, investigation: InvestigationResult
    ) -> tuple[ProposalView | None, list[ActionChoice]]:
        """Offer Resolve's first eligible action now and keep the rest, so buttons alone reach every option."""
        choices = [
            ActionChoice(case_id=case_id, investigation_id=investigation.id, action_type=a.action_type,
                         target_id=a.target_id, target_label=a.target_label)
            for a in investigation.eligible_actions
        ]
        if not choices:
            return None, []
        return await self._request_proposal(ctx, turn, choices[0]), choices[1:]

    async def _request_proposal(self, ctx: AuthContext, turn: NormalizedTurn, choice: ActionChoice) -> ProposalView:
        current: CaseView = await self._facade.get_case(ctx, choice.case_id)
        return await self._facade.propose_action(
            ctx,
            choice.case_id,
            ProposalRequest(
                expected_version=current.version,
                investigation_id=choice.investigation_id,
                action_type=choice.action_type,
                target_id=choice.target_id,
            ),
            command_key(turn.conversation_id, turn.turn_id, "propose", f"{choice.action_type}:{choice.target_id}"),
        )

    async def _on_decision(self, ctx: AuthContext, turn: NormalizedTurn, inp: DecisionInput, state: DialogueState) -> Step:
        lang = state.language
        pending = state.pending_proposal
        # Only the proposal currently presented in this conversation can be decided here.
        if pending is None or pending.proposal_id != inp.proposal_id or pending.proposal_hash != inp.proposal_hash:
            return TurnDraft(reply_text=t.text("proposal_mismatch", lang), case_id=state.active_case_id), state

        voice_evidence = turn.voice_evidence
        if turn.channel is Channel.VOICE and (
            voice_evidence is None
            or voice_evidence.presented_proposal_id != inp.proposal_id
            or voice_evidence.presented_proposal_hash != inp.proposal_hash
        ):
            return _confirm_prompt(state, Channel.VOICE)
        return await self._confirm(ctx, turn, state, inp.decision, voice_evidence)

    async def _confirm(self, ctx: AuthContext, turn: NormalizedTurn, state: DialogueState, decision: Decision, voice_evidence) -> Step:
        """Record ACCEPT/DECLINE for the pending proposal. Declines are recorded too."""
        lang = state.language
        pending = state.pending_proposal
        cleared = state.evolve(pending_proposal=None, pending_question=None)
        try:
            result = await self._facade.confirm_action(
                ctx,
                pending.proposal_id,
                ConfirmationRequest(proposal_hash=pending.proposal_hash, decision=decision, client_turn_id=turn.turn_id),
                command_key(turn.conversation_id, turn.turn_id, "confirm", pending.proposal_id),
                voice_evidence,
            )
        except ResolveError as err:
            if err.code in _ASK_AGAIN_CODES:
                return _confirm_prompt(state, turn.channel)
            if err.code not in _PROPOSAL_ERROR_TEMPLATES:
                raise
            return TurnDraft(reply_text=t.text(_PROPOSAL_ERROR_TEMPLATES[err.code], lang), case_id=pending.case_id), cleared

        if decision is Decision.DECLINE or result.operation is None:
            base = TurnDraft(reply_text=t.text("declined", lang), case_id=pending.case_id)
        else:
            base = _operation_draft(result.operation, pending.action_type, lang)
        remaining = [c for c in state.pending_choices if c.case_id == pending.case_id]
        others = [c for c in state.pending_choices if c.case_id != pending.case_id]
        if remaining:
            # Offer the next listed option, so a customer using only buttons can still reach it.
            try:
                proposal = await self._request_proposal(ctx, turn, remaining[0])
            except ResolveError as err:
                if err.code in {"ACTION_NOT_ALLOWED", "STALE_VERSION", "PROPOSAL_INVALIDATED", "RESOURCE_NOT_FOUND"}:
                    return base, cleared.evolve(pending_choices=others)
                raise
            offer = _offer_with_alternatives(proposal, remaining[1:], lang, pending.case_id)
            draft = TurnDraft(
                reply_text=f"{base.reply_text} {offer.reply_text}", case_id=pending.case_id, cards=base.cards + offer.cards,
                pending_question=offer.pending_question, operation_ids=base.operation_ids,
            )
            return draft, cleared.evolve(
                pending_proposal=_proposal_ref(proposal, turn), pending_choices=remaining[1:] + others,
                pending_question=offer.pending_question,
            )
        return base, cleared.evolve(pending_choices=others)

    async def _on_case_selection(self, ctx: AuthContext, turn: NormalizedTurn, inp: CaseSelectionInput, state: DialogueState) -> Step:
        lang = state.language
        try:
            case = await self._facade.get_case(ctx, inp.case_id)
        except ResolveError as err:
            if err.code != "RESOURCE_NOT_FOUND":
                raise
            case = None
        if case is None or case.conversation_id != turn.conversation_id:
            return TurnDraft(reply_text=t.text("case_not_found", lang), case_id=state.active_case_id), state

        keep_proposal = state.pending_proposal if state.pending_proposal and state.pending_proposal.case_id == case.id else None
        keep_choices = [c for c in state.pending_choices if c.case_id == case.id]
        state = state.evolve(
            active_case_id=case.id,
            pending_proposal=keep_proposal,
            pending_choices=keep_choices,
            pending_question=state.pending_question if keep_proposal or keep_choices else None,
            candidate=None,
        )
        reply = t.text("case_selected", lang, complaint=t.complaint_label(case.complaint_type, lang), status=t.case_status_label(case.status, lang))
        return TurnDraft(reply_text=reply, case_id=case.id, pending_question=state.pending_question), state


# --- helpers ------------------------------------------------------------------


def _check_trusted_fields(ctx: AuthContext, turn: NormalizedTurn) -> None:
    if turn.channel is not ctx.channel:
        raise ResolveError("VALIDATION_ERROR", "turn channel does not match authenticated channel")
    if turn.voice_evidence is not None:
        if turn.channel is not Channel.VOICE or turn.voice_evidence.turn_id != turn.turn_id:
            raise ResolveError("VALIDATION_ERROR", "voice evidence is only valid for its own Voice turn")


def _is_customer(ctx: AuthContext) -> bool:
    return ctx.role is Role.CUSTOMER and ctx.account_id is not None and ctx.sandbox_id is not None


def _ask(state: DialogueState, code: str, reply: str, allowed: list[InputType]) -> Step:
    question = PendingQuestion(code=code, text=reply, allowed_input_types=allowed)
    draft = TurnDraft(reply_text=reply, case_id=state.active_case_id, pending_question=question)
    return draft, state.evolve(pending_question=question)


def _login_required(state: DialogueState) -> Step:
    return _ask(state, Q_LOGIN_REQUIRED, t.text("login_required", state.language), ["text"])


def _confirm_prompt(state: DialogueState, channel: Channel = Channel.TEXT) -> Step:
    key = "confirm_prompt_voice" if channel is Channel.VOICE else "confirm_prompt"
    return _ask(state, Q_CONFIRM_ACTION, t.text(key, state.language), ["action_decision", "text"])


def _operation_draft(operation: OperationView, action_type: ActionType, lang: Language) -> TurnDraft:
    """Report the actual operation state; an accepted request is never described as done."""
    status = t.operation_status_text(operation.status, lang)
    if action_type is ActionType.CREATE_REVIEW_TICKET:
        ticket = operation.outcome.provider_ticket_id
        reply = t.text("accepted_review", lang, reference=operation.id, status=status)
        if ticket:
            reply = f"{reply} {t.text('ticket_issued', lang, ticket=ticket)}"
    else:
        reply = t.text("accepted", lang, status=status)
    return TurnDraft(reply_text=reply, case_id=operation.case_id, operation_ids=[operation.id])


def _prefixed(draft: TurnDraft, prefix: str) -> TurnDraft:
    return TurnDraft(
        reply_text=f"{prefix} {draft.reply_text}",
        case_id=draft.case_id,
        cards=draft.cards,
        citations=draft.citations,
        pending_question=draft.pending_question,
        operation_ids=draft.operation_ids,
    )


def opening_question(language: Language | str) -> PendingQuestion:
    """First question of a new conversation: free text plus suggested categories (UI chips)."""
    language = Language(language)
    return PendingQuestion(code=Q_CHOOSE_COMPLAINT, text=t.text("choose_complaint", language),
                           allowed_input_types=["category_selection", "text"])


def _checked_prefix(
    complaint_type: ComplaintType, facts: ReportedFacts, start: datetime, end: datetime, defaulted: bool, lang: Language
) -> str:
    """What was checked, restating only what the customer reported (type, typed amount; no model text)."""
    key = "checked_default_window" if defaulted else "checked_window"
    parts = [t.text(key, lang, complaint=t.complaint_label(complaint_type, lang), window=t.format_window(start, end))]
    if facts.amount_minor is not None:
        parts.append(t.text("ack_amount", lang, amount=t.format_lkr(facts.amount_minor)))
    return " ".join(parts)


def _merge_facts(base: ReportedFacts, ex: Extraction) -> ReportedFacts:
    """Customer reports only. Newer statements replace older ones field by field."""
    return ReportedFacts(
        amount_minor=ex.amount_minor if ex.amount_minor is not None else base.amount_minor,
        recharge_reference=ex.recharge_reference or base.recharge_reference,
        subscription_id=base.subscription_id,
        description=ex.summary or base.description,
    )


def _window_problem(inp: DetailsInput) -> str | None:
    if inp.window_end <= inp.window_start:
        return "invalid_window_order"
    if inp.window_end - inp.window_start > MAX_WINDOW:
        return "invalid_window_length"
    return None


def _offer_draft(proposal: ProposalView, lang: Language, case_id) -> TurnDraft:
    question = PendingQuestion(code=Q_CONFIRM_ACTION, text=t.text("confirm_prompt", lang), allowed_input_types=["action_decision", "text"])
    reply = t.text("offer_action", lang, action=t.action_label(proposal.action_type, lang), target=proposal.target_label, consequences=proposal.consequences)
    return TurnDraft(reply_text=reply, case_id=case_id, cards=[ConfirmationCard(data=proposal)], pending_question=question)


def _offer_with_alternatives(proposal: ProposalView, alternatives: list[ActionChoice], lang: Language, case_id) -> TurnDraft:
    offer = _offer_draft(proposal, lang, case_id)
    if not alternatives:
        return offer
    options = "; ".join(f"{t.action_label(a.action_type, lang)} ({a.target_label})" for a in alternatives)
    return TurnDraft(reply_text=f"{offer.reply_text} {t.text('other_options', lang, options=options)}", case_id=case_id,
                     cards=offer.cards, pending_question=offer.pending_question)


def _investigation_draft(
    inv: InvestigationResult, proposal: ProposalView | None, lang: Language, alternatives: list[ActionChoice] | None = None
) -> TurnDraft:
    """Reply strictly from Resolve's findings; no number is computed or rephrased here."""
    parts = [finding.text for finding in inv.findings] or [t.text("no_findings", lang)]
    if inv.evidence_state is EvidenceState.PARTIAL:
        missing = ", ".join(dict.fromkeys(t.missing_label(code, lang) for code in inv.missing)) or t.missing_label("unknown", lang)
        parts.append(t.text("evidence_partial", lang, missing=missing))
    elif inv.evidence_state is EvidenceState.CONFLICTING:
        parts.append(t.text("evidence_conflicting", lang))
    # Resolve's customer-facing limits (e.g. future renewal vs past dispute). Code-style reasons such as
    # OPENING_SNAPSHOT_MISSING are already explained by the missing-records sentence and never shown raw.
    parts += [reason for reason in inv.review_reasons if not _CODE.fullmatch(reason)]

    cards: list = [CalculationCard(data=calc) for calc in inv.calculations]
    cards += [FindingCard(data=finding) for finding in inv.findings]

    question = None
    if proposal is not None:
        offer = _offer_with_alternatives(proposal, alternatives or [], lang, inv.case_id)
        parts.append(offer.reply_text)
        cards += offer.cards
        question = offer.pending_question

    return TurnDraft(reply_text=" ".join(parts), case_id=inv.case_id, cards=cards, pending_question=question)


def _after_investigation(
    state: DialogueState,
    turn: NormalizedTurn,
    case_id,
    proposal: ProposalView | None,
    draft: TurnDraft,
    alternatives: list[ActionChoice],
) -> DialogueState:
    return state.evolve(
        active_case_id=case_id,
        candidate=None,
        pending_question=draft.pending_question,
        pending_proposal=_proposal_ref(proposal, turn) if proposal else None,
        pending_choices=list(alternatives),
    )


def _pending_as_choice(ref: PendingProposalRef | None) -> ActionChoice | None:
    if ref is None or ref.investigation_id is None or ref.target_id is None:
        return None
    return ActionChoice(case_id=ref.case_id, investigation_id=ref.investigation_id, action_type=ref.action_type,
                        target_id=ref.target_id, target_label=ref.target_label or "")


def _match_choice(state: DialogueState, ex: Extraction) -> ActionChoice | None:
    """Only an action Resolve listed for the active case can be chosen; the model cannot add one."""
    if ex.action_choice is None:
        return None
    matches = [c for c in state.pending_choices if c.action_type is ex.action_choice and c.case_id == state.active_case_id]
    return matches[0] if len(matches) == 1 else None


def _proposal_ref(proposal: ProposalView, turn: NormalizedTurn) -> PendingProposalRef:
    return PendingProposalRef(
        proposal_id=proposal.id,
        proposal_hash=proposal.proposal_hash,
        case_id=proposal.case_id,
        action_type=proposal.action_type,
        expires_at=proposal.expires_at,
        presented_turn_id=turn.turn_id,
        investigation_id=proposal.investigation_id,
        target_id=proposal.target_id,
        target_label=proposal.target_label,
    )


def _user_body(turn: NormalizedTurn, lang: Language) -> str:
    """Readable stand-in for button/form turns; the structured input itself is in the fingerprint."""
    inp = turn.input
    if isinstance(inp, TextInput):
        return inp.text
    if isinstance(inp, CategoryInput):
        return t.text("user_category", lang, complaint=t.complaint_label(inp.complaint_type, lang).capitalize())
    if isinstance(inp, DetailsInput):
        return t.text("user_details", lang, complaint=t.complaint_label(inp.complaint_type, lang).capitalize())
    if isinstance(inp, DecisionInput):
        return t.text("user_accept" if inp.decision is Decision.ACCEPT else "user_decline", lang)
    return t.text("user_case_selection", lang)
