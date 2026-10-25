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


def prune_cache(max_age_days: float | None = None, max_files: int | None = None) -> int:
    """清理过期与超量缓存文件，返回删除数量。

    先按保留天数删除过期文件；若剩余文件数仍超过上限，再按修改时间从旧到新删除。
    任何单个文件删除失败都跳过，不影响启动流程。
    """
    directory = config.CACHE_DIR
    if not directory.exists():
        return 0

    age_days = config.CACHE_MAX_AGE_DAYS if max_age_days is None else max_age_days
    limit = config.CACHE_MAX_FILES if max_files is None else max_files
    cutoff = time.time() - max(age_days, 0.0) * 86400
    removed = 0
    entries: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if age_days > 0 and mtime < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        entries.append((mtime, path))

    if limit > 0 and len(entries) > limit:
        entries.sort(key=lambda item: item[0])
        for _mtime, path in entries[: len(entries) - limit]:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


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
    try:
        data = _request(url, params, retries, timeout).json()
    except ValueError as exc:  # 接口返回非 JSON（限流页/HTML 错误页）时统一转成 RuntimeError
        raise RuntimeError(f"响应解析失败 {url}: {exc}") from exc
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


def post_json(
    url: str,
    payload: dict | None = None,
    ttl: int = 60,
    retries: int = 2,
    timeout: float | None = None,
) -> Any:
    """POST JSON 并解析响应，缓存与重试策略同 get_json。"""
    key = "post::" + url + ("::" + json.dumps(payload or {}, sort_keys=True))
    if ttl > 0:
        cached = _load_cache(key, ttl)
        if cached is not None:
            return cached
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _SESSION.post(
                url,
                json=payload,
                timeout=timeout or config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if ttl > 0:
                _save_cache(key, data)
            return data
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"POST 请求失败 {url}: {last_error}")
