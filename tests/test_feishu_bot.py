"""飞书双向机器人测试：事件解析、challenge 验证、去重与回复链路。"""
from __future__ import annotations

import json

from crypto_analyst import config, feishu_bot


def _event(
    event_id: str,
    text: str = "@_user_1 BTC 现在能追多吗",
    chat_type: str = "group",
    mentions: list | None = None,
    token: str = "",
) -> dict:
    return {
        "header": {
            "event_type": "im.message.receive_v1",
            "event_id": event_id,
            "token": token,
        },
        "event": {
            "message": {
                "message_id": "m1",
                "chat_id": "c1",
                "chat_type": chat_type,
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": [{"key": "@_user_1"}] if mentions is None else mentions,
            }
        },
    }


def test_extract_question_strips_mentions():
    parsed = feishu_bot.extract_question(_event("e-extract"))
    assert parsed == ("c1", "group", "BTC 现在能追多吗")


def test_extract_question_ignores_group_without_mention():
    assert feishu_bot.extract_question(_event("e-no-mention", mentions=[])) is None


def test_extract_question_allows_p2p_without_mention():
    parsed = feishu_bot.extract_question(_event("e-p2p", text="ETH 怎么看", chat_type="p2p", mentions=[]))
    assert parsed == ("c1", "p2p", "ETH 怎么看")


def test_extract_question_ignores_non_text():
    payload = _event("e-image")
    payload["event"]["message"]["message_type"] = "image"
    assert feishu_bot.extract_question(payload) is None


def test_process_payload_answers_verification_challenge(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    response = feishu_bot.process_payload({"type": "url_verification", "challenge": "abc123"})
    assert response == {"challenge": "abc123"}


def test_process_payload_rejects_bad_verify_token(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "expected")
    response = feishu_bot.process_payload(_event("e-token", token="wrong"))
    assert response["code"] == -1


def test_process_payload_answers_and_replies(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    calls: list = []

    def answer(question: str, chat_id: str | None = None) -> str:
        calls.append((question, chat_id))
        return "这是回答"

    def reply(chat_id: str, text: str) -> bool:
        calls.append((chat_id, text))
        return True

    response = feishu_bot.process_payload(_event("e-answer"), answer_fn=answer, reply_fn=reply)

    assert response == {"code": 0}
    assert calls == [("BTC 现在能追多吗", "c1"), ("c1", "这是回答")]


def test_process_payload_passes_chat_id_for_session(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    received: list = []

    def answer(question: str, chat_id: str | None = None) -> str:
        received.append(chat_id)
        return "回答"

    feishu_bot.process_payload(_event("e-session"), answer_fn=answer, reply_fn=lambda c, t: True)
    assert received == ["c1"]  # chat_id 正确传递，用于会话上下文


def test_process_payload_reset_clears_session(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    replied: list = []
    feishu_bot.reset_session("c1")  # 确保干净状态
    feishu_bot._SESSIONS["c1"] = (object(), 0)  # 模拟已有会话

    payload = _event("e-reset", text="重置")
    feishu_bot.process_payload(payload, answer_fn=lambda q, c: "x", reply_fn=lambda c, t: replied.append(t))

    assert "c1" not in feishu_bot._SESSIONS
    assert any("重置" in msg for msg in replied)


def test_process_payload_deduplicates_retries(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    answered: list[str] = []

    def answer(question: str, chat_id: str | None = None) -> str:
        answered.append(question)
        return "回答"

    payload = _event("e-duplicate")
    for _ in range(2):
        feishu_bot.process_payload(payload, answer_fn=answer, reply_fn=lambda chat_id, text: True)

    assert answered == ["BTC 现在能追多吗"]


def test_process_payload_ignores_unrelated_events(monkeypatch):
    monkeypatch.setattr(config, "FEISHU_VERIFY_TOKEN", "")
    payload = {"header": {"event_type": "im.chat.updated_v1", "event_id": "e-other"}}
    assert feishu_bot.process_payload(payload, answer_fn=lambda q, c: "x", reply_fn=lambda c, t: True) == {"code": 0}
