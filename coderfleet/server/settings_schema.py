"""
settings_schema.py — 系统设置的声明式登记表

config.conf 的可编辑面只在这里定义一次，同时驱动三件事：
  1. GET /api/config —— 按此渲染表单、注入当前值、脱敏密钥
  2. PUT /api/config —— 按此校验键名、判断哪些改动需要 apply
  3. 前端分组展示与「需 coderfleet apply」提示

新增一个可配置项 = 往这里加一行，前后端自动跟上（locality）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    secret: bool = False          # 密钥：GET 不回传明文，PUT 留空则保持不变
    requires_apply: bool = False  # 改动需 coderfleet apply + 重启容器才生效
    options: tuple[str, ...] = ()  # 非空则渲染为下拉框


@dataclass(frozen=True)
class SettingGroup:
    id: str
    title: str
    fields: tuple[SettingField, ...]
    help: str = ""


SETTINGS_GROUPS: tuple[SettingGroup, ...] = (
    SettingGroup(
        "system_llm", "系统级 LLM",
        help="翻译等系统功能调用的模型，独立于池化任务账号——不消耗编码配额。改动即时生效。",
        fields=(
            SettingField("SYSTEM_LLM_PROVIDER", "Provider",
                         options=("anthropic", "openai"),
                         help="openai 格式同时兼容 Ollama / vLLM / OpenRouter 等"),
            SettingField("SYSTEM_LLM_API_KEY", "API Key", placeholder="sk-...", secret=True),
            SettingField("SYSTEM_LLM_MODEL", "模型", placeholder="claude-haiku-4-5-20251001"),
            SettingField("SYSTEM_LLM_BASE_URL", "Base URL（可选）",
                         placeholder="留空用官方端点", help="OpenAI 兼容口 / 私有网关"),
            SettingField("SYSTEM_LLM_PROXY", "出站代理（可选）",
                         placeholder="http://127.0.0.1:10808"),
        ),
    ),
    SettingGroup(
        "server", "服务",
        fields=(
            SettingField("CODERFLEET_PORT", "监听端口", placeholder="8765", requires_apply=True),
        ),
    ),
    SettingGroup(
        "image", "镜像",
        help="修改后需执行 coderfleet apply / build 生效。",
        fields=(
            SettingField("IMAGE_NAME", "镜像名", placeholder="coderfleet", requires_apply=True),
            SettingField("IMAGE_TAG", "标签", placeholder="latest", requires_apply=True),
            SettingField("BUILD_PLATFORM", "构建平台",
                         options=("linux/arm64", "linux/amd64"), requires_apply=True),
        ),
    ),
    SettingGroup(
        "proxy", "代理",
        help="修改后需执行 coderfleet apply 并重启容器才生效，改错可能导致容器断网。",
        fields=(
            SettingField("PROXY_MODE", "代理模式", options=("host", "xray"), requires_apply=True),
            SettingField("PROXY_HOST", "宿主机代理地址",
                         placeholder="host.docker.internal", requires_apply=True),
            SettingField("PROXY_HTTP_PORT", "HTTP 端口", placeholder="10808", requires_apply=True),
            SettingField("PROXY_SOCKS5_PORT", "SOCKS5 端口", placeholder="10808", requires_apply=True),
        ),
    ),
    SettingGroup(
        "network", "内部网络",
        help="修改后需执行 coderfleet apply 并重启容器才生效，改错可能导致容器断网。",
        fields=(
            SettingField("INTERNAL_SUBNET", "内网网段", placeholder="172.21.0.0/16", requires_apply=True),
            SettingField("RELAY_IP", "中继 IP", placeholder="172.21.0.2", requires_apply=True),
            SettingField("RELAY_LISTEN_PORT", "中继端口", placeholder="10808", requires_apply=True),
            SettingField("RELAY_IMAGE", "中继镜像", placeholder="gogost/gost:3", requires_apply=True),
        ),
    ),
)


def all_fields() -> list[SettingField]:
    return [f for g in SETTINGS_GROUPS for f in g.fields]


def field_for(key: str) -> SettingField | None:
    return next((f for f in all_fields() if f.key == key.upper()), None)


def mask_secret(value: str) -> str:
    """密钥仅回一个提示尾巴，绝不回传明文。空值回空串。"""
    v = (value or "").strip()
    if not v:
        return ""
    return ("•" * 6) + v[-4:] if len(v) > 4 else "•" * len(v)
