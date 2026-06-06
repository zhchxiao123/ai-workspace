#!/bin/bash
# ============================================================
#  CoderFleet — 容器启动脚本
#  负责：代理验证、认证目录初始化、启动保活进程
# ============================================================

set -e

# ── 打印启动信息 ─────────────────────────────────────────
echo "========================================"
echo "  CoderFleet Container"
echo "  账号：${CODERFLEET_ACCOUNT_NAME:-unknown}"
echo "  类型：${CODERFLEET_ACCOUNT_TYPE:-unknown}"
echo "  认证：${CODERFLEET_ACCOUNT_AUTH:-login}"
echo "========================================"

# ── 等待代理中继就绪 ──────────────────────────────────────
if [ "${CODERFLEET_ACCOUNT_PROXY:-relay}" = "off" ]; then
    echo "账号代理：关闭（不等待代理中继）"
else
    PROXY_HOST="${CODERFLEET_RELAY_IP:-172.20.0.2}"
    PROXY_PORT="${CODERFLEET_RELAY_PORT:-7890}"
    MAX_WAIT=30
    waited=0

    echo "等待代理中继 ${PROXY_HOST}:${PROXY_PORT}..."
    while ! nc -z "$PROXY_HOST" "$PROXY_PORT" 2>/dev/null; do
        if [ "$waited" -ge "$MAX_WAIT" ]; then
            echo "警告：代理中继未就绪，继续启动（网络可能不可用）"
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    [ "$waited" -lt "$MAX_WAIT" ] && echo "代理中继已就绪"
fi

# ── 初始化认证目录 ────────────────────────────────────────
ACCOUNT_TYPE="${CODERFLEET_ACCOUNT_TYPE:-codex}"
case "$ACCOUNT_TYPE" in
    codex)
        mkdir -p "$CODEX_HOME"
        echo "Codex 认证目录：$CODEX_HOME"
        ;;
    claude)
        mkdir -p "$CLAUDE_CONFIG_DIR"
        echo "Claude 认证目录：$CLAUDE_CONFIG_DIR"
        if [ "${CODERFLEET_ACCOUNT_AUTH:-login}" = "env" ]; then
            echo "Claude 环境变量认证：已启用"
        fi
        ;;
    opencode)
        mkdir -p "${XDG_DATA_HOME:-/home/byclaw/.opencode/data}"
        mkdir -p "${XDG_CONFIG_HOME:-/home/byclaw/.opencode/config}"
        mkdir -p "${XDG_STATE_HOME:-/home/byclaw/.opencode/state}"
        mkdir -p "${XDG_CACHE_HOME:-/home/byclaw/.opencode/cache}"
        echo "OpenCode 数据目录：${XDG_DATA_HOME:-/home/byclaw/.opencode/data}"
        if [ "${CODERFLEET_ACCOUNT_AUTH:-login}" = "env" ]; then
            echo "OpenCode 环境变量认证：已启用"
        fi
        ;;
    hermes)
        HERMES_DIR="${HERMES_HOME:-/home/byclaw/.hermes}"
        mkdir -p "$HERMES_DIR"
        echo "Hermes 数据目录：$HERMES_DIR"
        # Disable interactive approval prompts for headless task execution
        /opt/hermes-venv/bin/hermes config set approvals.mode off 2>/dev/null || true
        if [ "${CODERFLEET_ACCOUNT_AUTH:-login}" = "env" ]; then
            echo "Hermes 环境变量认证：已启用"
        fi
        ;;
    kimi)
        KIMI_DIR="${KIMI_CODE_HOME:-/home/byclaw/.kimi-code}"
        mkdir -p "$KIMI_DIR"
        echo "Kimi Code 数据目录：$KIMI_DIR"
        if [ "${CODERFLEET_ACCOUNT_AUTH:-login}" = "env" ]; then
            echo "Kimi Code 环境变量模型认证：已启用"
        fi
        ;;
esac

# ── 可选启动 code-server（浏览器内 VS Code）────────────────
if [ "${CODERFLEET_IDE:-off}" = "on" ]; then
    IDE_PORT="${CODERFLEET_IDE_PORT:-8080}"
    IDE_AUTH="${CODERFLEET_IDE_AUTH:-password}"
    IDE_BIND="${CODERFLEET_IDE_BIND:-127.0.0.1}"
    # 覆写 config.yaml，防止旧配置里的 bind-addr 覆盖当前设置
    mkdir -p /home/byclaw/.config/code-server
    cat > /home/byclaw/.config/code-server/config.yaml << EOF
bind-addr: ${IDE_BIND}:${IDE_PORT}
auth: ${IDE_AUTH}
cert: false
EOF
    echo "启动 code-server：${IDE_BIND}:${IDE_PORT} -> /workspace（auth=${IDE_AUTH}）"
    code-server /workspace > /tmp/coderfleet-code-server.log 2>&1 &
fi

# ── 执行传入的命令（默认 sleep infinity）─────────────────
echo "容器启动完成，执行：$*"
exec "$@"
