"""Jev 客户端 —— 唯一的外部依赖接线点。

默认走 HTTP：POST {JEV_BASE_URL}/v1/ask，Bearer TYPESAFE_API_KEY。
若你使用官方 typesafe-sdk，只需替换 _post_ask() 的实现（单点适配），其余代码不变。

任何失败（超时/网络/非 2xx/解析错误）都抛 JevError，由 server 层走保守降级。
"""
from __future__ import annotations

import os
from typing import Any

import httpx


class JevError(RuntimeError):
    """Jev 调用失败（未配置/超时/不可用/返回异常）。"""


class JevClient:
    def __init__(self, timeout_ms: int = 4000, max_retries: int = 1, base_url: str | None = None):
        self.api_key = os.environ.get("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or os.environ.get("JEV_BASE_URL", "")).rstrip("/")
        self.timeout_s = timeout_ms / 1000.0
        self.max_retries = max(0, max_retries)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def ask(self, questions: list[dict], context: str) -> dict[str, dict]:
        """发送一组 questions，返回 {"<name>": {"type", "value", "p"}}；失败抛 JevError。"""
        if not self.configured:
            raise JevError("not_configured: 缺少 TYPESAFE_API_KEY 或 JEV_BASE_URL")
        data = self._post_ask({"context": context, "questions": questions})
        return self._normalize(data)

    def _post_ask(self, payload: dict) -> Any:
        last_err: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self.base_url}/v1/ask",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout_s,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # 网络错误 / 超时 / 非 2xx / JSON 解析失败
                last_err = exc
        raise JevError(f"request_failed: {last_err}")

    @staticmethod
    def _normalize(data: Any) -> dict[str, dict]:
        """兼容 {"fields": {...}} 与裸 {...} 两种返回结构。"""
        raw = data.get("fields", data) if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            raise JevError("bad_response: 返回结构无法解析为字段字典")
        fields: dict[str, dict] = {}
        for name, item in raw.items():
            if not isinstance(item, dict):
                continue
            try:
                p = float(item.get("p", item.get("probability", 0.0)))
            except (TypeError, ValueError):
                continue
            fields[str(name)] = {
                "type": item.get("type", ""),
                "value": item.get("value") or item.get("choice"),
                "p": max(0.0, min(1.0, p)),
            }
        return fields
