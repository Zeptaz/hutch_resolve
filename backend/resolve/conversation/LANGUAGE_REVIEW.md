# Sinhala and Tamil review (T-04)

The plan allows claiming only language support that a fluent person has checked. This file explains the two checks a reviewer runs and how to record them.

## 1. Reply wording (`locales/si.json`, `locales/ta.json`)

Both files are **machine-drafted** with `"status": "UNREVIEWED_DRAFT"`. While a file is unreviewed, every reply in that language falls back to English.

A fluent reviewer should:

1. Read each string in context: the English original is in `templates.py` (`_EN` and the label tables).
2. Pay most attention to the critical meanings:
   - **Amounts and dates:** `account_balance`, `checked_window`, `clarify_amount`.
   - **Targets:** `offer_action`, the `action_labels`.
   - **Negation:** `declined`, `clarify_negation`.
   - **Accept or decline:** `confirm_prompt`, `confirm_prompt_voice`.
   - **Pending vs success:** all of `operation_status`, `delivery`, `accepted`, `accepted_review`. "Pending" must never read like "done".
   - **Missing evidence:** `evidence_partial`, `evidence_conflicting`, `missing_labels`.
3. Keep every `{placeholder}` exactly. Tests fail if one is added, removed or renamed.
4. Check that `Accept` / `Decline` match the button labels in Jayith's UI.
5. When satisfied, set `"status": "REVIEWED"`, `"reviewed_by"` (name or initials) and `"reviewed_at"` (date), then run `python -m pytest tests/conversation -q`.

Known gaps a reviewer should be aware of (not fixed by translation):

- **Finding text is English.** Sentences such as "reconcile to LKR 420" come verbatim from Resolve, so a Sinhala or Tamil reply will still contain English findings. Localizing them needs per-finding-code templates agreed with Harry. Machine translation is not allowed, because numbers must stay exact.
- **Dates use English month abbreviations** ("2 Oct") and amounts use `LKR 1,000.00`.
- `account_balance` inserts the wallet name from Resolve (e.g. `main`) untranslated.
- Action labels are inserted mid-sentence (`offer_action`, `multiple_actions`). Check that the grammar still works with each label.

## 2. Understanding (live Gemini)

The extraction test set is `eval/extraction_cases.jsonl`: 41 messages across English, Singlish, Sinhala script, Tanglish, Tamil script, mixed language and prompt-injection attempts. With a key:

```bash
cd backend
GEMINI_API_KEY=... GEMINI_TEXT_MODEL=... python -m resolve.conversation.try_extract --eval
```

The output gives a score per language variety, latency, fallbacks and every miss. A fluent reviewer should also confirm the **expected** answers in the set are right; the set was drafted without native review.

Record each run in `docs/plans/tevin.md` with the model, date, case count and per-variety scores. Only a measured, reviewed result supports a claim like "understands Singlish"; a result from a small set should be described as small.
