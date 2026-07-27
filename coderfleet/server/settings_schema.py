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
from typing import Callable

from coderfleet.config import container_timezone


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    secret: bool = False          # 密钥：GET 不回传明文，PUT 留空则保持不变
    requires_apply: bool = False  # 改动需 coderfleet apply + 重启容器才生效
    options: tuple[str, ...] = ()  # 非空则渲染为下拉框
    validator: Callable[[str], str] | None = None  # 配置边界校验，并返回规范化后的值


@dataclass(frozen=True)
class SettingGroup:
    id: str
    title: str
    fields: tuple[SettingField, ...]
    help: str = ""


def _validate_container_timezone(value: str) -> str:
    return container_timezone({"CONTAINER_TIMEZONE": value})


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
        "telegram", "Telegram 通知",
        help="任务结束后向 Telegram 播报并支持回复续聊。改动即时生效。"
             "chat_id 可向 @userinfobot 发消息获取。",
        fields=(
            SettingField("TELEGRAM_BOT_TOKEN", "Bot Token",
                         placeholder="123456:ABC-DEF...", secret=True,
                         help="向 @BotFather 申请"),
            SettingField("TELEGRAM_CHAT_ID", "Chat ID 白名单",
                         placeholder="123456789",
                         help="逗号分隔；所有白名单 chat 都会收到播报并可回复续聊。"
                              "已配置 Topics 群时可留空（群成员即受信）"),
            SettingField("TELEGRAM_PROXY", "出站代理（可选）",
                         placeholder="http://127.0.0.1:10808",
                         help="api.telegram.org 无法直连时必填"),
            SettingField("TELEGRAM_NOTIFY_MODE", "播报模式",
                         options=("off", "text", "voice"),
                         help="off 关闭；text 文本；voice 语音（TTS，失败自动降级为文本）"),
            SettingField("TELEGRAM_TOPIC_GROUP_ID", "Topics 群 ID（可选）",
                         placeholder="-1001234567890",
                         help="开启 Topics 的群，bot 需管理员+管理话题权限；"
                              "配置后播报按项目话题分频道发进该群（群内发 /id 可获取）"),
            SettingField("TELEGRAM_ASR_API_KEY", "语音转写 API Key（可选）",
                         placeholder="sk-...", secret=True,
                         help="OpenAI 兼容 /audio/transcriptions；配置后可用语音下发指令"),
            SettingField("TELEGRAM_ASR_BASE_URL", "语音转写 Base URL（可选）",
                         placeholder="https://api.openai.com/v1"),
            SettingField("TELEGRAM_ASR_MODEL", "语音转写模型（可选）",
                         placeholder="whisper-1"),
        ),
    ),
    SettingGroup(
        "github_webhook", "GitHub Webhook",
        help="用于在 PR checks 完成时恢复等待中的 Conversation。请在仓库或组织中"
             "配置 /api/integrations/github/webhook，并使用相同 secret。",
        fields=(
            SettingField("GITHUB_WEBHOOK_SECRET", "Webhook Secret",
                         placeholder="高熵随机字符串", secret=True),
        ),
    ),
    SettingGroup(
        "digest", "日报",
        help="AI 日报摘要通过向某个活跃项目提交一个真实任务来生成，"
             "会在该项目的容器/工作目录中运行，可能产生文件改动。"
             "默认关闭；开启后才会在设置页与「生成日报摘要」按钮响应生成请求，"
             "以及每晚 23:30 自动生成前一天的日报。改动即时生效。",
        fields=(
            SettingField("DIGEST_ENABLED", "AI 摘要生成", options=("off", "on")),
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
            SettingField("CONTAINER_TIMEZONE", "容器时区", placeholder="Asia/Shanghai",
                         help="IANA 时区名称，例如 Asia/Shanghai、UTC、America/New_York",
                         requires_apply=True, validator=_validate_container_timezone),
        ),
    ),
    SettingGroup(
        "docker", "Docker",
        help="把宿主机 Docker socket 挂进项目容器。开启后容器可控制宿主 Docker，请只用于可信镜像。",
        fields=(
            SettingField("DOCKER_SOCKET", "Docker socket",
                         placeholder="off | auto | /var/run/docker.sock",
                         help="off 关闭；auto 自动识别 Colima / Docker Desktop / Linux；也可填写 unix:// 或绝对路径",
                         requires_apply=True),
            SettingField("DOCKER_SOCKET_TARGET", "容器内 socket 路径",
                         placeholder="/var/run/docker.sock",
                         help="通常保持默认；容器内 DOCKER_HOST 会指向此路径",
                         requires_apply=True),
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
