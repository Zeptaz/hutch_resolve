"""T-04: Sinhala/Tamil wording is used only after fluent human review."""

from __future__ import annotations

import string

import pytest

from conftest import ACCOUNT_A, Harness, customer
from resolve.conversation import templates as t
from resolve.conversation.dto import Language

LOCALES = [Language.SI, Language.TA, "si-Latn"]
# Romanized chat keeps everyday English loanwords ("data", "connection"), so a label may equal English.
LOANWORD_SECTIONS = {"complaint_labels", "missing_labels", "case_status"}


def placeholders(value: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(value) if name}


@pytest.mark.parametrize("language", LOCALES)
def test_draft_has_same_keys_and_placeholders_as_english(language: Language | str) -> None:
    locale = t.load_locale(language)
    assert locale["language"] == str(language)
    sections = locale["sections"]
    assert set(sections) == set(t.ENGLISH)
    for section, english in t.ENGLISH.items():
        expected = set(english) - (t.NOT_LOCALIZED if section in {"strings", "complaint_labels"} else set())
        assert set(sections[section]) == expected, (language, section)
        for key, value in sections[section].items():
            assert placeholders(value) == placeholders(english[key]), (language, section, key)
            placeholder_only = english[key].strip("{}") in placeholders(english[key])  # e.g. "{complaint}"
            loanword = str(language).endswith("-Latn") and section in LOANWORD_SECTIONS
            assert value.strip() and (value != english[key] or placeholder_only or loanword)


@pytest.mark.parametrize("language", LOCALES)
def test_unreviewed_drafts_are_never_used(language: Language | str) -> None:
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


@pytest.mark.parametrize(
    ("language", "script", "expected"),
    [
        (Language.EN, "LATIN", Language.EN),
        (Language.SI, None, Language.SI),
        (Language.SI, "SINHALA", Language.SI),
        (Language.SI, "LATIN", "si-Latn"),
        (Language.SI, "MIXED", "si-Latn"),
        (Language.TA, "TAMIL", Language.TA),
        (Language.TA, "LATIN", "ta-Latn"),  # no Tanglish file yet: falls back to English, not Tamil script
    ],
)
def test_locale_follows_how_the_customer_writes(language, script, expected) -> None:
    assert t.locale_for(language, script) == expected


def test_missing_romanized_locale_falls_back_to_english_not_native_script() -> None:
    assert t.text("declined", "ta-Latn") == t.ENGLISH["strings"]["declined"]


def _reviewed_singlish(monkeypatch: pytest.MonkeyPatch) -> dict:
    draft = t.load_locale("si-Latn")
    reviewed = draft | {"status": t.REVIEWED, "reviewed_by": "test", "reviewed_at": "2026-10-03"}
    monkeypatch.setattr(t, "load_locale", lambda language: reviewed if str(language) == "si-Latn" else {})
    return draft["sections"]["strings"]


def test_singlish_customer_gets_reviewed_singlish_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    from fakes import FakeModel, extraction

    strings = _reviewed_singlish(monkeypatch)
    h = Harness(model=FakeModel())
    h.model.on("mata help ekak ona", extraction(intent="OTHER", detected_language="si", script="LATIN"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "mata help ekak ona"}))
    assert result.reply_text == strings["choose_complaint"]


def test_voice_never_uses_romanized_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    from fakes import FakeModel, extraction
    from resolve.conversation.dto import Channel

    _reviewed_singlish(monkeypatch)
    h = Harness(model=FakeModel())
    h.model.on("mata help ekak ona", extraction(intent="OTHER", detected_language="si", script="LATIN"))
    ctx = customer(ACCOUNT_A, Channel.VOICE)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "mata help ekak ona"}, channel=Channel.VOICE))
    assert result.reply_text == t.ENGLISH["strings"]["choose_complaint"]  # spoken: native-script locale or English


def test_reviewed_template_reply_is_not_machine_rewritten_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from fakes import FakeModel, extraction
    from test_rewrite import RewriteModel
    from resolve.conversation.rewrite import ReplyRewriter

    strings = _reviewed_singlish(monkeypatch)
    h = Harness(model=FakeModel())
    rewriter_model = RewriteModel()
    h.service._rewriter = ReplyRewriter(rewriter_model)
    h.model.on("mata help ekak ona", extraction(intent="OTHER", detected_language="si", script="LATIN"))
    ctx = customer(ACCOUNT_A)
    conv = h.open(ctx)
    result = h.send(ctx, h.turn(conv, {"type": "text", "text": "mata help ekak ona"}))
    assert result.reply_text == strings["choose_complaint"] and rewriter_model.sources == []
