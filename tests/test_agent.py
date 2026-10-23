"""Agent 会话测试：多轮上下文保留、单轮重置与超长裁剪。"""
from __future__ import annotations

from crypto_analyst.agent import MarketAnalyst


class FakeLLM:
    available = True

    def __init__(self):
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, temperature=0.3, max_tokens=None):
        self.calls.append([dict(message) for message in messages])
        return {"content": f"回答{len(self.calls)}"}


def test_chat_keeps_multi_turn_history():
    llm = FakeLLM()
    analyst = MarketAnalyst(llm=llm)

    assert analyst.chat("第一问") == "回答1"
    assert analyst.chat("第二问") == "回答2"

    second_call = llm.calls[1]
    user_messages = [m["content"] for m in second_call if m["role"] == "user"]
    assert user_messages == ["第一问", "第二问"]
    assert [m["role"] for m in analyst.messages] == ["system", "user", "assistant", "user", "assistant"]


def test_ask_starts_fresh_context():
    llm = FakeLLM()
    analyst = MarketAnalyst(llm=llm)
    analyst.chat("第一问")
    analyst.ask("单轮提问")

    last_call = llm.calls[-1]
    user_messages = [m["content"] for m in last_call if m["role"] == "user"]
    assert user_messages == ["单轮提问"]


def test_reset_clears_messages():
    analyst = MarketAnalyst(llm=FakeLLM())
    analyst.chat("问题")
    analyst.reset()
    assert analyst.messages == []


def test_trim_keeps_complete_turns():
    analyst = MarketAnalyst(llm=FakeLLM())
    analyst.messages = [{"role": "system", "content": "s"}]
    for index in range(7):
        analyst.messages.extend(
            [
                {"role": "user", "content": f"u{index}"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": f"c{index}", "function": {"name": "x", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": f"c{index}", "content": "{}"},
                {"role": "assistant", "content": "a"},
            ]
        )

    analyst._trim()
    assert analyst.messages[0]["role"] == "system"
    # 保留最近 3 轮，且不能从 tool 结果中间开始
    assert analyst.messages[1]["role"] == "user"
    assert analyst.messages[1]["content"] == "u4"
    roles = [m["role"] for m in analyst.messages[1:]]
    assert len(roles) == 12
    assert roles[:4] == ["user", "assistant", "tool", "assistant"]
