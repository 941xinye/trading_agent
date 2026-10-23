"""飞书双向交互：接收群内 @机器人 的消息事件，调用 Agent 回答并回复到原会话。

实现方式为「事件订阅 + HTTP 回调」（标准库 http.server，无需额外依赖）：

1. 飞书开放平台创建自建应用，开启机器人能力，订阅 `im.message.receive_v1` 事件，
   把请求地址配置为 `https://你的域名/feishu/event`（本机可用内网穿透）；
2. 回调地址验证（`url_verification`）会自动回传 challenge；
3. 收到文本消息后立即返回 200，后台线程再调用 LLM 生成回答并回复，避免超时重试；
4. 需要应用具备 `im:message`（接收与发送）权限，并在群里 @机器人 才会收到事件。
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from . import config
from .agent import MarketAnalyst
from .llm import LLMError

MAX_ANSWER_CHARS = 4000
EVENT_TTL_SECONDS = 3600
MAX_SEEN_EVENTS = 1000
_MENTION_PATTERN = re.compile(r"@_user_\d+")

_TOKEN_LOCK = threading.Lock()
_TOKEN_VALUE = ""
_TOKEN_EXPIRES_AT = 0.0
_SEEN_EVENTS: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()


def extract_question(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """从事件中取出 (chat_id, chat_type, 问题文本)；非文本或群内未 @ 机器人时返回 None。"""
    event = payload.get("event") or {}
    message = event.get("message") or {}
    if message.get("message_type") != "text":
        return None
    chat_type = str(message.get("chat_type") or "")
    if chat_type == "group" and not message.get("mentions"):
        return None
    try:
        content = json.loads(message.get("content") or "{}")
    except ValueError:
        return None
    text = str(content.get("text") or "")
    text = _MENTION_PATTERN.sub("", text).strip()
    if not text or not message.get("chat_id"):
        return None
    return str(message["chat_id"]), chat_type, text


def _should_process(event_id: str, now: float | None = None) -> bool:
    """事件级去重：飞书超时会重推，同一 event_id 只处理一次。"""
    if not event_id:
        return True
    now = time.time() if now is None else now
    with _SEEN_LOCK:
        for key, ts in list(_SEEN_EVENTS.items()):
            if now - ts > EVENT_TTL_SECONDS:
                _SEEN_EVENTS.pop(key, None)
        if event_id in _SEEN_EVENTS:
            return False
        _SEEN_EVENTS[event_id] = now
        while len(_SEEN_EVENTS) > MAX_SEEN_EVENTS:
            _SEEN_EVENTS.pop(next(iter(_SEEN_EVENTS)))
    return True


def answer_question(question: str) -> str:
    """调用 Agent 生成回答；未配置 LLM 时给出明确提示而不是静默失败。"""
    if not config.LLM_API_KEY:
        return "未配置 LLM_API_KEY，暂时无法回答。请在 .env 中填写后重启 feishu-hook。"
    try:
        return MarketAnalyst().ask(question, symbols=None)
    except LLMError as exc:
        return f"回答失败：{exc}"
    except Exception as exc:  # 网络/解析异常都不应让机器人崩溃
        return f"回答失败：{exc}"


def _tenant_token() -> str:
    global _TOKEN_VALUE, _TOKEN_EXPIRES_AT
    with _TOKEN_LOCK:
        if _TOKEN_VALUE and time.time() < _TOKEN_EXPIRES_AT:
            return _TOKEN_VALUE
        url = f"{config.FEISHU_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json() if resp.content else {}
        token = str(data.get("tenant_access_token") or "")
        if resp.status_code >= 400 or data.get("code") != 0 or not token:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data.get('msg') or resp.text[:200]}")
        _TOKEN_VALUE = token
        _TOKEN_EXPIRES_AT = time.time() + max(int(data.get("expire") or 7200) - 300, 60)
        return token


def send_text(chat_id: str, text: str) -> bool:
    """以机器人身份向会话发送文本消息。"""
    if not config.FEISHU_APP_ID or not config.FEISHU_APP_SECRET:
        print("[飞书] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，无法回复。", flush=True)
        return False
    body = text if len(text) <= MAX_ANSWER_CHARS else text[: MAX_ANSWER_CHARS - 1] + "…"
    try:
        token = _tenant_token()
        resp = requests.post(
            f"{config.FEISHU_BASE_URL}/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": body}, ensure_ascii=False),
            },
            timeout=15,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code") != 0:
            print(f"[飞书] 回复失败 HTTP {resp.status_code}: {data.get('msg') or resp.text[:200]}", flush=True)
            return False
        return True
    except Exception as exc:
        print(f"[飞书] 回复异常: {exc}", flush=True)
        return False


def process_payload(
    payload: dict[str, Any],
    answer_fn: Callable[[str], str] = answer_question,
    reply_fn: Callable[[str, str], bool] = send_text,
) -> dict[str, Any]:
    """处理一次事件回调，返回给飞书的响应体（验证 challenge / 受理确认 / 错误）。"""
    if config.FEISHU_VERIFY_TOKEN:
        token = (payload.get("header") or {}).get("token") or payload.get("token")
        if token != config.FEISHU_VERIFY_TOKEN:
            return {"code": -1, "msg": "invalid verify token"}

    if payload.get("type") == "url_verification" or payload.get("challenge"):
        return {"challenge": payload.get("challenge", "")}

    header = payload.get("header") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return {"code": 0}

    if not _should_process(str(header.get("event_id") or "")):
        return {"code": 0}

    parsed = extract_question(payload)
    if not parsed:
        return {"code": 0}
    chat_id, _chat_type, question = parsed

    print(f"[飞书] 收到提问：{question}", flush=True)
    answer = answer_fn(question)
    reply_fn(chat_id, answer)
    return {"code": 0}


class _Handler(BaseHTTPRequestHandler):
    server_version = "ChainLensFeishu/1.0"

    def do_GET(self):  # noqa: N802 - http.server 约定
        if urlparse(self.path).path == "/health":
            self._write(200, {"status": "ok"})
        else:
            self._write(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/feishu/event":
            self._write(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            self._write(400, {"error": "invalid json"})
            return

        is_verification = payload.get("type") == "url_verification" or payload.get("challenge")
        if is_verification or (payload.get("header") or {}).get("event_type") != "im.message.receive_v1":
            # 验证与无关事件同步处理，保证飞书立刻拿到结果
            self._write(200, process_payload(payload))
            return

        # 问答耗时可能超过飞书 3 秒超时：先确认受理，后台线程再回答
        threading.Thread(
            target=process_payload,
            args=(payload,),
            daemon=True,
        ).start()
        self._write(200, {"code": 0})

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[飞书] {self.address_string()} {fmt % args}", flush=True)


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or config.FEISHU_BOT_HOST
    port = port or config.FEISHU_BOT_PORT
    if not config.FEISHU_APP_ID or not config.FEISHU_APP_SECRET:
        print(
            "未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，机器人无法回复；"
            "仍会启动回调服务用于验证地址。",
            flush=True,
        )
    server = ThreadingHTTPServer((host, port), _Handler)
    print(
        f"飞书事件回调已启动：http://{host}:{port}/feishu/event  "
        f"（订阅 im.message.receive_v1，Ctrl+C 退出）",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n飞书回调已停止。", flush=True)
    finally:
        server.server_close()
