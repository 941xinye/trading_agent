"""Agent 主循环：LLM + 工具调用（Function Calling）。"""
from __future__ import annotations

import json

from . import prompts, tools
from .llm import LLMClient, LLMError

MAX_STEPS = 6


class MarketAnalyst:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def ask(self, question: str) -> str:
        if not self.llm.available:
            raise LLMError(
                "未配置 LLM_API_KEY。请在 .env 中设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，"
                "或先使用 `python main.py brief --no-llm` 查看规则引擎生成的简报。"
            )

        messages: list[dict] = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        for _ in range(MAX_STEPS):
            message = self.llm.chat(messages, tools=tools.TOOL_SPECS)
            tool_calls = message.get("tool_calls")
            if not tool_calls:
                return (message.get("content") or "").strip()

            messages.append(
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
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        messages.append({"role": "user", "content": "请停止调用工具，基于已有结果直接给出最终结论。"})
        final = self.llm.chat(messages, temperature=0.2)
        return (final.get("content") or "").strip()

    def comment(self, facts_markdown: str) -> str:
        """基于规则引擎产出的事实写点评，杜绝数字幻觉。"""
        message = self.llm.chat(
            [
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": f"{prompts.BRIEF_COMMENT_PROMPT}\n\n---\n{facts_markdown}"},
            ],
            temperature=0.4,
            max_tokens=500,
        )
        return (message.get("content") or "").strip()
