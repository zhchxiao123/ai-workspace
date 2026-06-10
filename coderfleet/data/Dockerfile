# ============================================================
#  CoderFleet — 统一工作容器镜像
#  包含：Python 3.12 / Node.js 20 / Rust / Java 21 + Maven
#        Codex CLI / Claude Code / OpenCode / Hermes / Grok
#        FFmpeg / LibreOffice (headless) / Playwright + Chromium
#  平台：linux/amd64 + linux/arm64（多平台镜像）
# ============================================================

FROM ubuntu:24.04

# 避免交互式安装询问
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
# C.UTF-8 is always available on glibc systems (no locale-gen needed).
# Without this, bash readline will not correctly echo multibyte UTF-8 input.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

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
    bash-completion less vim nano tmux \
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

# ── code-server（浏览器内 VS Code）────────────────────────
RUN curl -fsSL https://code-server.dev/install.sh | sh

# ── Hermes Agent ──────────────────────────────────────────
# ripgrep is required by hermes code-search tools
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
# Install in an isolated venv to avoid conflicts with Debian-managed packages
# (e.g. PyJWT installed by apt has no RECORD file and cannot be uninstalled by pip)
RUN python3 -m venv /opt/hermes-venv \
    && /opt/hermes-venv/bin/pip install --no-cache-dir \
        hermes-agent \
        anthropic \
        openai \
        google-genai \
        mistralai \
    && ln -s /opt/hermes-venv/bin/hermes /usr/local/bin/hermes

# ── Grok Build CLI (xAI) ─────────────────────────────────
# install.sh 把真正的二进制下载到 ~/.grok/downloads/，然后在 ~/.grok/bin/ 建 symlink。
# 新版安装器还会尝试把 grok/agent symlink 到 /usr/local/bin/；这里用临时 HOME
# 执行安装，再复制 symlink 指向的实体二进制，避免最终镜像依赖 root 的 ~/.grok。
# arm64 上如果 x.ai 尚未提供对应二进制，安装步骤会被跳过而不中断构建。
RUN set -eux; \
    grok_home="$(mktemp -d)"; \
    curl -fsSL https://x.ai/cli/install.sh -o /tmp/grok-install.sh; \
    if HOME="$grok_home" GROK_BIN_DIR="$grok_home/.grok/bin" bash /tmp/grok-install.sh; then \
        rm -f /usr/local/bin/grok /usr/local/bin/agent /usr/local/bin/grok-agent; \
        cp -L "$grok_home/.grok/bin/grok" /usr/local/bin/grok; \
        chmod 755 /usr/local/bin/grok; \
        if [ -e "$grok_home/.grok/bin/agent" ]; then \
            cp -L "$grok_home/.grok/bin/agent" /usr/local/bin/grok-agent; \
            chmod 755 /usr/local/bin/grok-agent; \
        fi; \
        GROK_HOME=/tmp/grok-home grok --version; \
        rm -rf /tmp/grok-home; \
    else \
        echo "WARNING: Grok CLI installation skipped (unsupported on $(uname -m))"; \
    fi; \
    rm -rf "$grok_home" /tmp/grok-install.sh

# ── Kimi Code CLI ────────────────────────────────────────
# Use the official native binary installer instead of npm because this image
# currently carries Node.js 20 while @moonshot-ai/kimi-code requires newer Node.
ARG KIMI_CODE_VERSION=0.11.0
RUN set -eux; \
    curl -fsSL https://code.kimi.com/kimi-code/install.sh -o /tmp/kimi-code-install.sh; \
    KIMI_VERSION="${KIMI_CODE_VERSION}" \
    KIMI_INSTALL_DIR=/opt/kimi-code \
    KIMI_NO_MODIFY_PATH=1 \
    bash /tmp/kimi-code-install.sh; \
    ln -sf /opt/kimi-code/bin/kimi /usr/local/bin/kimi; \
    chmod -R a+rx /opt/kimi-code; \
    kimi --version; \
    rm -f /tmp/kimi-code-install.sh

# ── Rust 1.93.0 ───────────────────────────────────────────
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTUP_DIST_SERVER=https://rsproxy.cn \
    RUSTUP_UPDATE_ROOT=https://rsproxy.cn/rustup
RUN curl --proto '=https' --tlsv1.2 -sSf https://rsproxy.cn/rustup-init.sh | sh -s -- -y --no-modify-path --default-toolchain 1.93.0 \
    && chmod -R a+w $RUSTUP_HOME $CARGO_HOME \
    && rustc --version

# ── Java 21 (OpenJDK) + Maven ─────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jdk \
    maven \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && ln -sfn "$(readlink -f /usr/bin/java | sed 's|/bin/java||')" /usr/lib/jvm/java-21
ENV JAVA_HOME=/usr/lib/jvm/java-21
ENV PATH=$JAVA_HOME/bin:$PATH

# ── FFmpeg ────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── LibreOffice（含中文字体与语言包）────────────────────
# 只装 Writer / Calc / Impress / Draw + headless 核心，跳过 Java / Base
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    libreoffice-draw \
    libreoffice-l10n-zh-cn \
    fonts-wqy-zenhei \
    fonts-noto-cjk \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── Playwright + Chromium ─────────────────────────────────
# 1. Python Playwright 包（同时提供 `playwright` CLI）
RUN pip3 install --no-cache-dir --break-system-packages playwright

# 2. 浏览器统一安装到全局共享路径，两种语言运行时共用，避免重复下载
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

# 3. 安装 Chromium 系统依赖，再下载 Chromium 二进制
#    playwright install-deps 内部会调用 apt，需要 root
RUN playwright install-deps chromium \
    && playwright install chromium \
    && chmod -R a+rx /opt/playwright-browsers

# 4. Node.js Playwright（复用 PLAYWRIGHT_BROWSERS_PATH，无需重新下载浏览器）
RUN npm install -g playwright

# ── 创建非特权用户 byclaw（含 sudo 免密）─────────────────
RUN apt-get update && apt-get install -y --no-install-recommends sudo \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && groupadd -r byclaw && useradd -r -g byclaw -m -s /bin/bash byclaw \
    && chown -R byclaw:byclaw /opt/hermes-venv \
    && echo 'byclaw ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/byclaw \
    && chmod 440 /etc/sudoers.d/byclaw

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
