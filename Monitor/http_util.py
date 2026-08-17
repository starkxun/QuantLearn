"""带限频节奏和重试的 GET 封装。

两件事这里必须做对，做错了后面全是玄学问题：

1. **限频节奏**：中转站两个上游各限 6 次/分钟且独立计数，所以按 bucket 分别节流。
   不是"打了 429 再退避"，而是**主动按 11s 间隔发**——被动挨 429 会浪费配额。

2. **重试**：本机代理有丢包，同一个请求时通时不通（实测多次）。
   但只重试**临时性**错误：429/502/503/504/连接异常。
   400/401/404/405 是确定性错误，重试没用只会更快耗掉配额（手册也明确要求不要重试）。
"""

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from config import MAX_RETRIES, MIN_INTERVAL, TIMEOUT, api_key

# 值得重试的：限流 + 网关临时故障
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# 明确不重试的：参数/认证/路径/方法错误，重试改变不了结果
FATAL_STATUS = {400, 401, 403, 404, 405}


class _Pacer:
    """按 bucket 记录上次请求时刻，保证同一 bucket 内的最小间隔。"""

    def __init__(self):
        self._last = {}

    def wait(self, bucket: str) -> None:
        gap = MIN_INTERVAL.get(bucket, 0.0)
        if gap <= 0:
            return
        last = self._last.get(bucket)
        if last is not None:
            sleep = gap - (time.monotonic() - last)
            if sleep > 0:
                time.sleep(sleep)
        self._last[bucket] = time.monotonic()


_pacer = _Pacer()


class ApiError(RuntimeError):
    """确定性失败（认证/参数/路径），调用方应该停下来看，而不是重试。"""


def get_json(base: str, path: str, bucket: str, params: dict | None = None,
             auth: bool = True, quiet: bool = False) -> dict:
    """发一次 GET，返回解析后的 JSON。

    bucket 决定用哪条限频节奏；auth=False 用于无需 key 的币安镜像。
    """
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {"Accept": "application/json", "User-Agent": "QuantLearn-Monitor/0.1"}
    if auth:
        # key 只放请求头，绝不进 URL —— 查询参数会落进各种日志
        headers["X-API-Key"] = api_key()

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        _pacer.wait(bucket)
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            if e.code in FATAL_STATUS:
                raise ApiError(f"HTTP {e.code} {path} → {body}") from None
            if e.code not in RETRYABLE_STATUS:
                raise ApiError(f"HTTP {e.code} {path} → {body}") from None
            # 429 优先听服务端的 Retry-After
            wait = _retry_after(e) or _backoff(attempt)
            last_err = f"HTTP {e.code} {body}"

        except Exception as e:                      # 连接重置、超时、DNS —— 代理丢包的日常
            wait = _backoff(attempt)
            last_err = f"{type(e).__name__}: {e}"

        if attempt < MAX_RETRIES:
            if not quiet:
                print(f"    重试 {attempt}/{MAX_RETRIES - 1} ({last_err[:80]})，{wait:.1f}s 后再来")
            time.sleep(wait)

    raise ApiError(f"{path} 重试 {MAX_RETRIES} 次仍失败，最后一次：{last_err}")


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    try:
        v = e.headers.get("Retry-After")
        return float(v) + 0.5 if v else None
    except (TypeError, ValueError):
        return None


def _backoff(attempt: int) -> float:
    """指数退避 + 抖动。抖动是为了避免多个脚本同时重试撞在一起。"""
    return min(2.0 ** attempt, 30.0) + random.uniform(0, 1.0)


def check_lsr(payload: dict, what: str) -> dict:
    """LSR 接口：HTTP 200 不代表业务成功，手册要求再看 success 字段。"""
    if payload.get("success") is False:
        raise ApiError(f"{what} 业务失败：{str(payload)[:200]}")
    return payload


def check_coinglass(payload: dict, what: str) -> dict:
    """Coinglass 接口：手册要求 code == "0" 才算成功。"""
    code = str(payload.get("code", ""))
    if code not in ("0", ""):
        raise ApiError(f"{what} 业务失败 code={code}：{str(payload.get('msg'))[:160]}")
    return payload
