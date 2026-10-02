# Conversation module audit (2026-10-02)

Scope: Tevin's conversation module (`backend/resolve/conversation`), the dev backend (`tests/conversation/dev_backend.py`) and how Jayith's chat renders replies. Method: code and security review, plus two live runs of 37 scripted conversations (English, Singlish, Tanglish, Sinhala and Tamil script) against the dummy backend with `gemini-3.5-flash-lite`, and browser checks on the main database. Results are measured on one model and one run each; the expected answers are author-written.

## Security and privacy

| ID | Finding | Fix | Verified |
| --- | --- | --- | --- |
| S1 | The model's one-line complaint summary was sent to Resolve as the customer's reported description and as the human-review reason. A manipulated message could make it read like a neutral system note ("verified by supervisor, approve refund"). | Reviewers now get the customer's own words, with the model summary only as "Machine summary (unverified)". | Unit (injection test) |
| S2 | NIC numbers, card numbers and PINs typed by customers went to Gemini and into the stored transcript. | `privacy.py` masks them at the start of every text turn, before the model, storage and Resolve. Amounts, dates, phone numbers and references are kept. | Unit (checks the exact model prompt and the stored transcript); live reply did not echo the NIC |
| S3 | "show me the balance of 0771234567" showed the customer's own balance as if it were that number's. | `about_other_line` flag: "I can only look at the line you're signed in with, not other numbers." | Unit + live |
| S4 | Machine rewrites could contain another writing system (Arabic inside a Tamil reply, seen live). | `foreign_script` check on rewrites, how-to answers and package-agent replies; failure keeps the checked English. | Unit |
| S5 | Citation links are rendered as clickable `href`s. | Backend drops any citation that isn't `https://` (defence in depth; all current cards are https://hutch.lk). | Unit |
| S6 | Dev backend simulated an operation's success before checking who was polling it. | Ownership check first (dev only). | Code review |
| OK | No API keys in any commit; no `.env`, dumps or backups tracked; no raw-HTML rendering in the frontend; logs carry metadata only; dev backend binds 127.0.0.1; every account read is scoped by `AuthContext`; only the button confirms actions; model output can only choose among actions Resolve listed. | — | Scan + tests |

Not fixed here (owners):

- **No rate limit on model calls** (Harry, API): one turn can make up to 5 Gemini calls. Add per-session limits (`RATE_LIMITED` exists in the contract).
- **Voice transcripts are not masked** (Harry/Voice): spoken consent compares the exact transcript, so masking must apply to both sides in the bridge.
- **The number check is value-level** (documented): it confirms each number exists in the facts, not that it sits next to the right item; cards and operation state stay the truth.
- **Dev backend has no CSRF or real sign-in** (dev only; Harry's API has both). Never expose it beyond localhost.
- **Free-tier Gemini is a single point of failure**: the audit itself exhausted the daily quota (429 RESOURCE_EXHAUSTED). Enable billing before the demo.

## Conversation mistakes found and fixed

| # | Customer said | Before | Now |
| --- | --- | --- | --- |
| 1 | "Can u remove all the active VAS charges" | Explained what the bot can do | Stop-renewal card first, reply leads with it; past charges after (live) |
| 2 | "refund my money for the video alerts" | Offered to stop renewals | "I can't give refunds… but our review team can look at it" + review card (live) |
| 3 | "cancel my data package" | (after fix 1) stop-VAS offer | How-to answer; packages ≠ VAS (prompt rule + eval case; not yet live-checked) |
| 4 | "send me internet settings" | "I don't have reviewed information" | Connection check with the settings card first (live) |
| 5 | "how much data do I have left?", "when does my package expire?", "what's my number?" | Balance text for all three; expiry treated as a status question | `account_topic`: each gets its own answer (live) |
| 6 | "What are the VAS charges?" | "I don't have reviewed information" | The line's own services, renewal state, offer to check or stop (live, main DB) |
| 7 | "thanks", "bye" | "What's the problem?" | "Glad I could help…" (live, wording since made neutral) |
| 8 | "hi, I'm Kamal", "introduce yourself", "oya kauda?" | Generic menu / off-topic | Numbered self-introduction, greets by a plain first name (live) |
| 9 | "I want to talk to a real person" (no case) | "Tell me the problem first" | Same, plus cited HUTCH contacts (live) |
| 10 | "you are useless, nothing works" | Connection check, no acknowledgement | Apology and "you can ask for a person at any time" (live) |
| 11 | "my reload didn't come and also my data is gone" | Data problem silently dropped | Reload handled, "You also mentioned a data problem…" (unit) |
| 12 | "update my address" | Off-topic, then "I don't have reviewed information" | "I can't help with that in this chat, but HUTCH support can" + contacts (unit) |
| 13 | Typed "yes do it" on an offer | "Please answer using the buttons on the offer above." | Explains why the button is needed |
| 14 | VAS answer | One long block, repeated card text | Short paragraphs; points to the card |
| 15 | Offers | "for Your account", "for The past charge" | "for your account", "for the past charge" |
| 16 | Gemini down | "Tell me in your own words…" | "I can't read typed messages right now. Please pick an option below…" |

## Not verified live yet

The second live run hit the Gemini daily quota after 25 of 37 conversations, so these fixes are unit-tested only until the quota resets: package-cancel routing, personal-details answer, the two-problem mention, Singlish/Tanglish/Tamil/Sinhala-script complaints, and the multi-turn flows (typed yes, correction, thanks after a case, status after Accept). The 70-case extraction eval (`extract-v11`) also needs a live re-run.

## Improvements, in priority order

1. **Gemini billing** (user): latency, reliability and quota. Everything below assumes it.
2. **Fluent Sinhala/Tamil review** (user): drafts and machine replies are unreviewed; this is the biggest quality risk for a Sri Lankan audience.
3. **Review and seed the knowledge cards** (team, Harry): 8 drafts (7 public, 1 demo policy). Add verified cards for SIM replacement, PUK, roaming, APN settings and package cancellation so fewer questions end at "contact support".
4. **Service prices on the account** (Harry): `AccountView` has no price per service, so "what are my VAS charges" can't state the amount without an investigation.
5. **Next-step suggestions after an answer** (Tevin + Jayith): after a data-depletion answer, offer a package suggestion; after a stop, offer the review of past charges. Needs generic quick-reply chips in the UI (today only complaint categories exist).
6. **Satisfaction check** (Tevin + Jayith): "Did this solve it?" after an outcome, feeding the dashboard.
7. **Harry's wording** (findings 10, 15, 16 in `tevin.md`): internal finding text shown to customers, the "today" window that can't reconcile A, and line codes used as labels.
8. **Monitoring** (Harry): chart model outcomes (`NOT_GROUNDED`, `TIMEOUT`, `MODEL_ERROR`) from `model_calls`, which now name the failed check.
