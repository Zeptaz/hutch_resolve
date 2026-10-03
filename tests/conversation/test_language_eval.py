"""The extraction eval set must stay valid so live runs measure what they claim."""

from __future__ import annotations

from collections import Counter

from resolve.conversation.dto import ComplaintType, Language
from resolve.conversation.extraction import AccountTopic, Extraction, Intent, SpokenDecision, TimeKind
from resolve.conversation.try_extract import load_cases, score

ALLOWED = {
    "intent": {i.value for i in Intent},
    "complaint_type": {c.value for c in ComplaintType},
    "decision": {d.value for d in SpokenDecision} | {None},
    "detected_language": {l.value for l in Language},
    "time_kind": {k.value for k in TimeKind},
    "account_topic": {a.value for a in AccountTopic},
}


def test_cases_are_well_formed_and_cover_every_variety() -> None:
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    varieties = Counter(c["variety"] for c in cases)
    for variety in ("english", "singlish", "sinhala_script", "tanglish", "tamil_script", "mixed", "adversarial"):
        assert varieties[variety] >= 2, variety
    for case in cases:
        assert case["message"].strip()
        for field, value in case["expect"].items():
            if field == "intent_not":
                assert set(value) <= ALLOWED["intent"]
            elif field == "amount_lkr":
                assert value > 0
            else:
                assert value in ALLOWED[field], (case["id"], field, value)


def test_scoring_detects_mismatches() -> None:
    case = {"expect": {"intent": "NEW_COMPLAINT", "complaint_type": "DATA_DEPLETION", "amount_lkr": 500}}
    base = {
        "intent": "NEW_COMPLAINT", "decision": None, "action_choice": None, "detected_language": "si", "script": "LATIN",
        "complaint_type": "DATA_DEPLETION", "amount_lkr": 500, "recharge_reference": None, "faq_query": None,
        "summary": None, "ambiguities": [],
        "time_reference": {"kind": "NONE", "count": None, "start_date": None, "end_date": None},
    }
    assert score(case, Extraction.model_validate(base)) == []
    assert score(case, Extraction.model_validate(base | {"complaint_type": "CONNECTIVITY", "amount_lkr": 50})) == [
        "complaint_type",
        "amount_lkr",
    ]
    assert score(case, None) == ["<no extraction>"]
