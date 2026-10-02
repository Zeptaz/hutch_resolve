"""T-04: Sinhala/Tamil wording is used only after fluent human review."""

from __future__ import annotations

import string

import pytest

from conftest import ACCOUNT_A, Harness, customer
from resolve.conversation import templates as t
from resolve.conversation.dto import Language

LOCALES = [Language.SI, Language.TA]


def placeholders(value: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(value) if name}


@pytest.mark.parametrize("language", LOCALES)
def test_draft_has_same_keys_and_placeholders_as_english(language: Language) -> None:
    locale = t.load_locale(language)
    assert locale["language"] == language.value
    sections = locale["sections"]
    assert set(sections) == set(t.ENGLISH)
    for section, english in t.ENGLISH.items():
        expected = set(english) - (t.NOT_LOCALIZED if section == "strings" else set())
        assert set(sections[section]) == expected, (language, section)
        for key, value in sections[section].items():
            assert placeholders(value) == placeholders(english[key]), (language, section, key)
            assert value.strip() and value != english[key]


@pytest.mark.parametrize("language", LOCALES)
def test_unreviewed_drafts_are_never_used(language: Language) -> None:
    assert t.load_locale(language)["status"] != t.REVIEWED
    assert t.text("declined", language) == t.text("declined", Language.EN)
    assert t.operation_status_text(t.OperationStatus.PENDING, language) == t.operation_status_text(t.OperationStatus.PENDING, Language.EN)


def test_reviewed_locale_is_used_and_falls_back_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = t.load_locale(Language.SI)
    reviewed = draft | {"status": t.REVIEWED, "reviewed_by": "test", "reviewed_at": "2026-10-02"}
    reviewed["sections"] = {**draft["sections"], "strings": {k: v for k, v in draft["sections"]["strings"].items() if k != "faq_none"}}
    monkeypatch.setattr(t, "load_locale", lambda language: reviewed if language is Language.SI else {})

    assert t.text("declined", Language.SI) == draft["sections"]["strings"]["declined"]
    assert t.text("faq_none", Language.SI) == t.ENGLISH["strings"]["faq_none"]  # missing key -> English
    assert t.text("default_escalation_reason", Language.SI) == t.ENGLISH["strings"]["default_escalation_reason"]


def test_reply_language_follows_turn_when_reviewed(monkeypatch: pytest.MonkeyPatch, h: Harness) -> None:
    draft = t.load_locale(Language.SI)
    monkeypatch.setattr(t, "load_locale", lambda language: draft | {"status": t.REVIEWED} if language is Language.SI else {})
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "category_selection", "complaint_type": "DATA_DEPLETION"}, language="si"))
    assert result.reply_text == draft["sections"]["strings"]["complaint_details"]
