"""
system_llm.py — 系统级 LLM 能力

CoderFleet 自身发起的基础设施 AI 调用（翻译、摘要、命名……）走这里，
与「任务执行」用的池化账号/容器完全分开：系统的碎碎念不该啃编码配额。

这是一个 deep module：调用方只需认识 complete() 一个方法与一个中性的
message 形状；Anthropic / OpenAI 两种 wire 格式的差异全部藏在 adapter 里。

配置来自 config.conf 的 SYSTEM_LLM_* 键：
    SYSTEM_LLM_PROVIDER   anthropic | openai        （默认 anthropic）
    SYSTEM_LLM_API_KEY    密钥
    SYSTEM_LLM_MODEL      模型 id
    SYSTEM_LLM_BASE_URL   可选，覆盖默认端点（OpenAI 兼容口、私有网关等）
    SYSTEM_LLM_PROXY      可选，出站代理，如 http://127.0.0.1:10808
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx

from coderfleet.config import load_config


class SystemLLMError(RuntimeError):
    """系统 LLM 调用失败（未配置、网络错误、上游返回非 2xx 等）。"""


Message = dict[str, str]  # {"role": "user"|"assistant", "content": str}


class SystemLLM:
    def __init__(
        self,
        *,
        provider: str = "anthropic",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        proxy: str = "",
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.provider = (provider or "anthropic").strip().lower()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.proxy = proxy.strip()
        self.timeout = timeout
        # 仅供测试注入 httpx.MockTransport；真实路径永远为 None。
        self._transport = transport

    @classmethod
    def from_config(cls, ws: Path, **overrides: Any) -> "SystemLLM":
        cfg = load_config(ws)
        params = dict(
            provider=cfg.get("SYSTEM_LLM_PROVIDER", "anthropic"),
            api_key=cfg.get("SYSTEM_LLM_API_KEY", ""),
            model=cfg.get("SYSTEM_LLM_MODEL", ""),
            base_url=cfg.get("SYSTEM_LLM_BASE_URL", ""),
            proxy=cfg.get("SYSTEM_LLM_PROXY", ""),
        )
        params.update(overrides)
        return cls(**params)

    def is_configured(self) -> bool:
        """未配置时系统功能应优雅降级，而不是抛错给用户看。"""
        return bool(self.api_key and self.model)

    async def complete(
        self,
        messages: list[Message],
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        """一次无状态补全，返回助手文本。system 由各 adapter 放到正确位置。"""
        if not self.is_configured():
            raise SystemLLMError("系统 LLM 未配置（缺少 SYSTEM_LLM_API_KEY 或 SYSTEM_LLM_MODEL）")
        model = model or self.model
        if self.provider == "anthropic":
            return await self._complete_anthropic(messages, system, model, max_tokens)
        if self.provider == "openai":
            return await self._complete_openai(messages, system, model, max_tokens)
        raise SystemLLMError(f"未知的 SYSTEM_LLM_PROVIDER: {self.provider}")

    # ── adapters（内部 seam，实现私有） ──────────────────────

    async def _complete_anthropic(
        self, messages: list[Message], system: Optional[str], model: str, max_tokens: int
    ) -> str:
        base = self.base_url or "https://api.anthropic.com"
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            body["system"] = system
        data = await self._post(
            f"{base}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            body=body,
        )
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts).strip()

    async def _complete_openai(
        self, messages: list[Message], system: Optional[str], model: str, max_tokens: int
    ) -> str:
        base = self.base_url or "https://api.openai.com/v1"
        wire: list[Message] = ([{"role": "system", "content": system}] if system else []) + messages
        data = await self._post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            body={"model": model, "max_tokens": max_tokens, "messages": wire},
        )
        choices = data.get("choices") or []
        if not choices:
            raise SystemLLMError("上游未返回 choices")
        return (choices[0].get("message", {}).get("content") or "").strip()

    # ── HTTP（httpx，proxy 与容器保持一致由配置控制） ────────

    async def _post(self, url: str, *, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        elif self.proxy:
            kwargs["proxy"] = self.proxy
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as e:
            raise SystemLLMError(f"请求上游失败: {e}") from e
        if resp.status_code >= 400:
            raise SystemLLMError(f"上游返回 {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as e:
            raise SystemLLMError(f"上游响应非 JSON: {resp.text[:200]}") from e
