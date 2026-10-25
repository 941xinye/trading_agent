"""本地 HTTP API：把快照、简报、问答与仪表盘暴露为接口，供本地页面或脚本调用。

只依赖标准库（ThreadingHTTPServer），默认仅监听 127.0.0.1；如需局域网访问，
显式传 `--host 0.0.0.0` 并配置 `API_TOKEN` 做简单鉴权。
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import analysis, briefing, config, dashboard
from .agent import MarketAnalyst
from .llm import LLMError

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_BODY_BYTES = 64 * 1024
# /ask 接口保护：限制并发 LLM 请求数与单次最长耗时，避免耗尽资源
MAX_CONCURRENT_ASK = 2
ASK_TIMEOUT_SECONDS = 60

_ask_in_flight = 0
_ask_lock = threading.Lock()


def _response(payload: Any, status: int = 200) -> tuple[int, str, str]:
    return (
        status,
        "application/json; charset=utf-8",
        json.dumps(payload, ensure_ascii=False, default=str),
    )


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def _symbols(raw: str) -> list[str] | None:
    items = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return items or None


def _authorized(headers: dict[str, str]) -> bool:
    if not config.API_TOKEN:
        return True
    token = headers.get("x-api-token", "")
    if not token:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    return token == config.API_TOKEN


def dispatch(
    method: str,
    path: str,
    query: dict[str, list[str]],
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, str, str]:
    """处理一次请求，返回 (status, content_type, payload)。

    独立于 HTTP 服务器，便于单测直接调用。
    """
    # /health 始终公开，便于容器健康检查与外部探活
    if method == "GET" and path == "/health":
        return _response({"status": "ok", "exchange": config.EXCHANGE, "symbols": config.SYMBOLS})

    if not _authorized(headers):
        return _response({"error": "unauthorized"}, 401)

    if method == "GET" and path == "/snapshot":
        include_news = _first(query, "news", "0") not in ("0", "false", "no")
        snapshot = analysis.build_snapshot(
            symbols=_symbols(_first(query, "symbols")),
            include_news=include_news,
            source="api",
        )
        return _response(snapshot.to_dict())

    if method == "GET" and path == "/dashboard":
        try:
            days = max(1, min(90, int(_first(query, "days", "7"))))
        except ValueError:
            return _response({"error": "days 必须是整数"}, 400)
        source = _first(query, "source", "all") or "all"
        return 200, "text/html; charset=utf-8", dashboard.render(days=days, source=source)

    if path == "/brief":
        params = parse_qs(body.decode("utf-8", "ignore")) if method == "POST" and body else query
        use_llm = _first(params, "llm", "1") not in ("0", "false", "no")
        try:
            snapshot, markdown, commentary, report_path = briefing.build(
                symbols=_symbols(_first(params, "symbols")),
                use_llm=use_llm,
            )
        except Exception as exc:
            return _response({"error": f"简报生成失败: {exc}"}, 500)
        if not snapshot.symbols:
            return _response({"error": "未获取到任何标的，请检查网络与数据源配置"}, 503)
        return _response(
            {
                "path": str(report_path),
                "markdown": markdown,
                "commentary": commentary,
                "warnings": snapshot.warnings,
                "generated_at": snapshot.generated_at,
            }
        )

    if method == "POST" and path == "/ask":
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            return _response({"error": "请求体必须是 JSON"}, 400)
        question = str(payload.get("question") or "").strip()
        if not question:
            return _response({"error": "缺少 question 字段"}, 400)
        symbols = payload.get("symbols")
        if isinstance(symbols, str):
            symbols = _symbols(symbols)

        # 并发限制：避免同时跑太多 LLM 请求耗尽资源
        global _ask_in_flight
        with _ask_lock:
            if _ask_in_flight >= MAX_CONCURRENT_ASK:
                return _response({"error": "问答服务繁忙，请稍后再试"}, 429)
            _ask_in_flight += 1

        try:
            answer = _ask_with_timeout(question, symbols)
        except _AskTimeout:
            return _response({"error": "回答超时，请稍后重试"}, 504)
        except LLMError as exc:
            return _response({"error": str(exc)}, 503)
        finally:
            with _ask_lock:
                _ask_in_flight -= 1

        return _response({"question": question, "answer": answer})

    return _response({"error": "not found"}, 404)


class _AskTimeout(Exception):
    """问答操作超时。"""


def _ask_with_timeout(question: str, symbols: list[str] | None) -> str:
    """在独立线程中执行问答，超时抛出 _AskTimeout。"""
    result: dict[str, Any] = {}

    def run():
        try:
            result["answer"] = MarketAnalyst().ask(question, symbols=symbols)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(ASK_TIMEOUT_SECONDS)
    if thread.is_alive():
        raise _AskTimeout()
    if "error" in result:
        raise result["error"]
    return result["answer"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "ChainLensAPI/1.0"

    def do_GET(self):  # noqa: N802 - http.server 约定
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            status, content_type, payload = _response({"error": "请求体过大"}, 413)
        else:
            body = self.rfile.read(length) if length > 0 else b""
            headers = {key.lower(): value for key, value in self.headers.items()}
            try:
                status, content_type, payload = dispatch(
                    method, parsed.path, parse_qs(parsed.query), body, headers
                )
            except Exception as exc:  # 单次请求异常不影响服务
                status, content_type, payload = _response({"error": str(exc)}, 500)

        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s %s", self.address_string(), fmt % args)


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or config.API_HOST
    port = port or config.API_PORT
    server = ThreadingHTTPServer((host, port), _Handler)
    logger.info(
        "本地 API 已启动：http://%s:%s （/health /snapshot /dashboard /brief /ask，鉴权 %s）",
        host, port, "开启" if config.API_TOKEN else "关闭",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("API 已停止。")
    finally:
        server.server_close()
