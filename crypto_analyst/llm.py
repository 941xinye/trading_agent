"""OpenAI 兼容的 LLM 客户端（DeepSeek / Kimi / 通义 / 本地 vLLM 均可）。"""
from __future__ import annotations

import requests

from . import config


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.LLM_API_KEY
        self.model = model or config.LLM_MODEL

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _endpoints(self) -> list[str]:
        if self.base_url.endswith("/v1"):
            return [f"{self.base_url}/chat/completions"]
        return [f"{self.base_url}/v1/chat/completions", f"{self.base_url}/chat/completions"]

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> dict:
        if not self.available:
            raise LLMError("未配置 LLM_API_KEY，无法调用大模型")

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        endpoints = self._endpoints()
        last_error: Exception | None = None
        for index, url in enumerate(endpoints):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=90)
                if resp.status_code == 404 and index < len(endpoints) - 1:
                    continue
                if resp.status_code != 200:
                    raise LLMError(f"LLM 接口返回 {resp.status_code}: {resp.text[:300]}")
                choice = resp.json()["choices"][0]
                return choice["message"]
            except LLMError:
                raise
            except Exception as exc:
                last_error = exc
        raise LLMError(f"LLM 请求失败: {last_error}")
