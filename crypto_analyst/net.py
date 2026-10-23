"""HTTP 访问层：统一超时、重试与磁盘缓存，降低对行情接口的请求频率。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from . import config

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CryptoAnalystAgent/1.0",
        "Accept": "application/json, text/xml, */*",
    }
)


def _cache_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{digest}.json"


def _load_cache(key: str, ttl: float) -> Any | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(payload.get("ts", 0)) > ttl:
        return None
    return payload.get("data")


def _save_cache(key: str, data: Any) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False), "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _request(
    url: str, params: dict | None, retries: int, timeout: float | None = None
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _SESSION.get(
                url, params=params, timeout=timeout or config.HTTP_TIMEOUT
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:  # 网络波动统一重试
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"请求失败 {url}: {last_error}")


def get_json(
    url: str,
    params: dict | None = None,
    ttl: int = 60,
    retries: int = 2,
    timeout: float | None = None,
) -> Any:
    key = url + ("?" + urlencode(sorted((params or {}).items())) if params else "")
    if ttl > 0:
        cached = _load_cache(key, ttl)
        if cached is not None:
            return cached
    data = _request(url, params, retries, timeout).json()
    if ttl > 0:
        _save_cache(key, data)
    return data


def get_text(url: str, ttl: int = 600, retries: int = 2) -> str:
    key = "text::" + url
    if ttl > 0:
        cached = _load_cache(key, ttl)
        if cached is not None:
            return cached
    text = _request(url, None, retries).text
    if ttl > 0:
        _save_cache(key, text)
    return text
