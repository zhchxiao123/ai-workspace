# ============================================================
#  CoderFleet — 统一工作容器镜像
#  包含：Python 3.12 / Node.js 20 / Codex CLI / Claude Code / OpenCode
#  平台：linux/amd64（Apple Silicon 通过 Docker 模拟运行）
# ============================================================

FROM --platform=linux/amd64 ubuntu:24.04

# 避免交互式安装询问
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# ── 基础系统工具 ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 网络工具
    curl wget ca-certificates netcat-openbsd \
    # 开发基础
    git build-essential pkg-config \
    # Python 依赖
    libssl-dev libffi-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev liblzma-dev \
    # 系统工具
    bash-completion less vim nano \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 (via NodeSource) ───────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 验证 Node 版本并安装常用全局包
RUN node --version && npm --version \
    && npm install -g npm@latest

# ── Python 3.12 (via deadsnakes PPA) ─────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 设置 python3.12 为默认 python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.12 1

# 安装常用 Python 工具
# Ubuntu 24.04 实行 PEP 668，系统 Python 需加 --break-system-packages
RUN pip3 install --no-cache-dir --break-system-packages \
    uv ruff black mypy pexpect

# ── Codex CLI ─────────────────────────────────────────────
RUN npm install -g @openai/codex

# ── Claude Code CLI ───────────────────────────────────────
RUN npm install -g @anthropic-ai/claude-code

# ── OpenCode CLI ──────────────────────────────────────────
RUN npm install -g opencode-ai@latest

# ── Rust 1.93.0 ───────────────────────────────────────────
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTUP_DIST_SERVER=https://rsproxy.cn \
    RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://rsproxy.cn/rustup-init.sh | sh -s -- -y --no-modify-path --default-toolchain 1.93.0 \
    && chmod -R a+w $RUSTUP_HOME $CARGO_HOME \
    && rustc --version

# ── 创建非特权用户 byclaw ─────────────────────────────────
RUN groupadd -r byclaw && useradd -r -g byclaw -m -s /bin/bash byclaw

# ── 工作目录和环境变量 ────────────────────────────────────
RUN mkdir -p /workspace && chown byclaw:byclaw /workspace
WORKDIR /workspace

# 这两个目录由 CoderFleet 按账号挂载，容器内路径固定
# Codex 认证：/home/byclaw/.codex   由 CODEX_HOME 控制
# Claude 认证：/home/byclaw/.claude  由 CLAUDE_CONFIG_DIR 控制
# OpenCode 数据：/home/byclaw/.opencode（运行时按账号挂载并设置 XDG_*_HOME）
ENV CODEX_HOME=/home/byclaw/.codex
ENV CLAUDE_CONFIG_DIR=/home/byclaw/.claude

# ── 启动脚本 ──────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY scripts/coderfleet_usage_status.py /usr/local/bin/coderfleet-usage-status
RUN chmod +x /usr/local/bin/coderfleet-usage-status

# ── 切换为非特权用户 ──────────────────────────────────────
USER byclaw

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sleep", "infinity"]
