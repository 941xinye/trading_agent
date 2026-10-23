"""Agent 主循环：LLM + 工具调用（Function Calling），支持单轮问答与多轮会话。"""
from __future__ import annotations

import json

from . import prompts, tools
from .llm import LLMClient, LLMError

MAX_STEPS = 6
# 多轮会话保留的最大消息条数（含系统提示），避免上下文无限膨胀
MAX_MESSAGES = 25


class MarketAnalyst:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    def ask(self, question: str, symbols: list[str] | None = None) -> str:
        """单轮问答：每次从干净上下文开始（brief / 脚本调用保持无状态）。"""
        self.reset()
        return self.chat(question, symbols=symbols)

    def chat(self, question: str, symbols: list[str] | None = None) -> str:
        """多轮问答：保留历史消息，交互模式下由调用方维护实例生命周期。"""
        if not self.llm.available:
            raise LLMError(
                "未配置 LLM_API_KEY。请在 .env 中设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，"
                "或先使用 `python main.py brief --no-llm` 查看规则引擎生成的简报。"
            )

        if not self.messages:
            self.messages.append({"role": "system", "content": prompts.SYSTEM_PROMPT})
        self.messages.append({"role": "user", "content": question})
        self._trim()

        previous_scope = tools.set_symbol_scope(symbols)
        try:
            for _ in range(MAX_STEPS):
                message = self.llm.chat(self.messages, tools=tools.TOOL_SPECS)
                tool_calls = message.get("tool_calls")
                if not tool_calls:
                    return self._finish((message.get("content") or "").strip())

                self.messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    name = call["function"]["name"]
                    raw_args = call["function"].get("arguments") or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError:
                        arguments = {}
                    result = tools.dispatch(name, arguments)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )

            self.messages.append({"role": "user", "content": "请停止调用工具，基于已有结果直接给出最终结论。"})
            final = self.llm.chat(self.messages, temperature=0.2)
            return self._finish((final.get("content") or "").strip())
        finally:
            tools.set_symbol_scope(previous_scope)

    def _finish(self, answer: str) -> str:
        self.messages.append({"role": "assistant", "content": answer})
        return answer

    def _trim(self) -> None:
        """超长会话按"完整轮次"裁剪，避免切断 assistant tool_calls / tool 结果配对。"""
        if len(self.messages) <= MAX_MESSAGES:
            return
        tail = self.messages[1:]
        # 从倒数第 3 条 user 消息开始保留，保证 assistant tool_calls 与 tool 结果成对
        turn_starts = [index for index, message in enumerate(tail) if message["role"] == "user"]
        if len(turn_starts) > 3:
            self.messages = [self.messages[0]] + tail[turn_starts[-3] :]

    def comment(self, facts_markdown: str) -> str:
        """基于规则引擎产出的事实写点评，杜绝数字幻觉。"""
        message = self.llm.chat(
            [
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"{prompts.BRIEF_COMMENT_PROMPT}\n\n---\n{facts_markdown}",
                },
            ],
            temperature=0.4,
            max_tokens=500,
        )
        return (message.get("content") or "").strip()
