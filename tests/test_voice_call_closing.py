"""A Voice call ends only when the caller says goodbye to "anything else?" (or by UI / time limit)."""

from __future__ import annotations

import pytest

from backend.resolve.app.voice_api import _voice_response
from backend.resolve.conversation.service import _is_closing_reply


def _result(question):
    return {"response_id": "r-1", "case_id": None, "reply_text": "Some reply", "cards": [],
            "pending_question": question, "operation_ids": []}


def test_goodbye_result_ends_the_voice_session():
    response = _voice_response(_result({"code": "CALL_ENDED", "text": "Goodbye!", "allowed_input_types": ["text"]}))
    assert response["end_session"] is True and response["pending_question"] is None


@pytest.mark.parametrize("code", ["ANYTHING_ELSE", "CONFIRM_ACTION", "CHOOSE_COMPLAINT_TYPE"])
def test_other_questions_never_end_the_voice_session(code):
    response = _voice_response(_result({"code": code, "text": "Question?", "allowed_input_types": ["text"]}))
    assert response["end_session"] is False and response["pending_question"] == "Question?"


@pytest.mark.parametrize("text", ["No", "no, thank you.", "Nope", "That's all, thanks", "Okay bye", "Nothing else",
                                  "නැහැ", "නෑ ස්තූතියි", "epa", "இல்லை", "illa nandri"])
def test_clear_goodbyes_close_the_call(text):
    assert _is_closing_reply(text)


@pytest.mark.parametrize("text", ["No, my balance is wrong", "no data since yesterday", "what about my VAS charges",
                                  "yes", "ok", "thank you", "I have another question"])
def test_anything_more_than_a_goodbye_keeps_the_call(text):
    assert not _is_closing_reply(text)
