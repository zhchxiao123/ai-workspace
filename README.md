# CoderFleet

> 把多个 Codex CLI / Claude Code / OpenCode / Hermes Agent / Grok Build / Kimi Code 账号变成一支可调度的 AI 开发舰队。

CoderFleet 是一个面向 AI 编程重度用户的本地多账号开发工作台。

它可以在一台机器上同时运行多个 AI 编程账号，每个账号拥有独立容器、独立认证文件和独立代理出口，并通过 Web 控制台或 CLI 提交异步开发任务、实时查看日志、管理项目与账号。

## 适合谁？

- 同时使用多个 Codex / Claude Code / OpenCode / Grok Build / Kimi Code 账号的开发者
- 经常遇到单账号额度限制的 AI 编程重度用户
- 希望多个 AI Agent 并行处理任务的独立开发者
- 需要本地容器隔离、代理隔离和账号隔离的用户

## 核心能力

- 多账号管理：Codex / Claude Code / OpenCode / Hermes Agent / Grok Build / Kimi Code
- 每个账号独立容器隔离
- 每个项目可绑定不同账号
- Web 控制台提交任务和查看日志
- CLI 异步任务队列
- 宿主机代理中继，统一管理网络出口
- macOS 系统托盘：登录自动启动，任务完成原生通知
---

<video src="https://github.com/user-attachments/assets/37bee884-09c7-40ca-bc7b-87ce26422c8c" controls width="100%"></video>

## 安装

### 环境要求

- Docker Desktop（macOS / Linux）或 Docker Engine + Compose V2
- 宿主机已运行代理软件（Clash / v2ray 等），并开启 allow-lan
- Python 3.10+（推荐用 uv 管理，见下方）

---

### 第零步：安装 uv（推荐）

[uv](https://docs.astral.sh/uv/) 是极速 Python 包管理器，同时负责安装 Python 本身和管理工具隔离环境，比 pip / pipx 更快且无副作用。

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 重新加载 shell 使 uv 命令生效
source $HOME/.local/bin/env   # bash/zsh
# 或重新打开终端
```

安装完成后，用 uv 装好 Python：

```bash
uv python install 3.12
```

---

### 方式一：uv tool 安装（推荐，新用户）

`uv tool` 等价于 pipx，将 coderfleet 装进独立隔离环境，不污染系统 Python。

```bash
uv tool install coderfleet
```

安装完成后，`coderfleet` 命令全局可用，工作区默认位于 `~/.coderfleet/`。

> 如果未安装 uv，也可用 pipx：
> ```bash
> pipx install coderfleet
> ```

### 方式二：克隆仓库（开发者 / 老用户）

```bash
git clone https://github.com/zhchxiao123/coderfleet.git
cd coderfleet

# 用 uv 创建虚拟环境并安装（推荐）
uv venv && source .venv/bin/activate
uv pip install -e .

# 或用系统 pip（可能需要 --break-system-packages）
pip install -e .
```

> 老用户请参考 [从 `./aicm.sh` 迁移到 `coderfleet`](changelog/migrate-from-aicm-to-coderfleet.md)。旧的 `./aicm.sh` 包装脚本不再保留。

## 快速开始

### 1. 初始化工作区

```bash
coderfleet init
```

交互式向导将引导你：
- 确认工作区目录（默认 `~/.coderfleet/`）
- 填写宿主机代理端口（HTTP / SOCKS5）
- 设置 Docker 镜像名称和构建平台

初始化完成后，工作区结构如下：

```
~/.coderfleet/
├── config.conf        # 全局配置（镜像、代理、网络）
├── accounts.conf      # 账号配置
├── projects.conf      # 项目配置
├── docker-compose.yml # 自动生成，勿手动编辑
└── accounts/          # 各账号认证数据（自动管理）
    ├── alice/
    └── bob/
```

### 2. 构建镜像

```bash
coderfleet build
```

首次构建约需 5~10 分钟，镜像包含：

- Ubuntu 24.04
- Python 3.12（含 uv / ruff / black / mypy）
- Node.js 20
- Rust 1.93.0
- Codex CLI（`@openai/codex`）
- Claude Code（`@anthropic-ai/claude-code`）
- OpenCode（`opencode-ai`）
- Hermes Agent（安装在 `/opt/hermes-venv`）
- Grok Build（`grok` CLI，需 `XAI_API_KEY`）
- Kimi Code（`kimi` CLI）

### 3. 添加账号与项目

```bash
# Codex 账号
coderfleet account add alice TYPE=codex
coderfleet project add app-a alice ~/projects/app-a

# Claude Code 账号
coderfleet account add bob TYPE=claude
coderfleet project add app-b bob ~/projects/app-b

# OpenCode 账号
coderfleet account add carol TYPE=opencode
coderfleet project add app-c carol ~/projects/app-c

# Hermes Agent 账号
coderfleet account add dave TYPE=hermes
coderfleet project add app-d dave ~/projects/app-d

# Grok Build 账号（API Key 认证）
coderfleet account add eve TYPE=grok --auth env
coderfleet project add app-e eve ~/projects/app-e
# 编辑 ~/.coderfleet/accounts/eve/env，填入 XAI_API_KEY=xai-...

# Kimi Code 账号
coderfleet account add frank TYPE=kimi
coderfleet project add app-f frank ~/projects/app-f
# 或使用环境变量模型：coderfleet account add kimi-api TYPE=kimi --auth env
# 编辑 env 文件，填入 KIMI_MODEL_NAME / KIMI_MODEL_API_KEY 等
```

### 4. 生成配置并启动容器

```bash
coderfleet apply
```

### 5. 登录账号

```bash
coderfleet login alice
```

CLI 会输出授权 URL，在宿主机浏览器打开 → 完成授权 → 复制 code → 粘贴回终端。

如果账号容器还未启动，`login` 会自动拉起一个临时认证容器；登录结束后容器删除，认证文件保留在 `accounts/<账号名>/`。

> Grok Build 只支持 `AUTH=env`，不需要执行 `coderfleet login`，直接配置 env 文件后 `coderfleet apply` 即可。

### 6. 进入容器工作

```bash
# 打开多个终端
coderfleet enter app-a   # 进入 app-a 项目容器（使用 Codex 账号 alice）
coderfleet enter app-b   # 进入 app-b 项目容器（使用 Claude Code 账号 bob）
coderfleet enter app-c   # 进入 app-c 项目容器（使用 OpenCode 账号 carol）
coderfleet enter app-d   # 进入 app-d 项目容器（使用 Hermes Agent 账号 dave）
coderfleet enter app-e   # 进入 app-e 项目容器（使用 Grok Build 账号 eve）
coderfleet enter app-f   # 进入 app-f 项目容器（使用 Kimi Code 账号 frank）
```

进入后使用对应 CLI：

```bash
# Codex 容器内
codex "帮我实现用户认证模块"

# Claude Code 容器内
claude "帮我重构这个函数"

# OpenCode 容器内
opencode run "帮我修复测试"

# Hermes Agent 容器内
hermes chat -q "帮我分析这个项目"

# Grok Build 容器内
grok -p "帮我实现支付接口" --output-format streaming-json

# Kimi Code 容器内
kimi
# 或单次非交互执行：
kimi -p "帮我分析这个项目"
```

### 7. 启动调度服务与 Web 控制台

除了交互式进入容器，CoderFleet 还提供 **FastAPI 任务调度服务与 Web 聊天控制台**，可在宿主机直接触发并监控容器内的异步开发任务。

```bash
coderfleet server              # 前台运行
coderfleet server --daemon     # 后台守护进程模式
coderfleet server --status     # 查看运行状态
coderfleet server --stop       # 停止后台 server
```

启动后可访问：
- **Web UI 对话控制台**：[http://localhost:8765](http://localhost:8765)（多任务聊天界面、日志流式追踪、WebSocket 容器终端）
- **API 交互式文档**：[http://localhost:8765/docs](http://localhost:8765/docs)

### 8. 命令行异步任务

服务运行期间，可在宿主机直接提交后台任务：

```bash
# 一次性开发任务
coderfleet task run "在 auth.py 中添加 JWT 生成逻辑" --project app-b

# 全自动模式（跳过 CLI 权限确认）
coderfleet task run "运行并修复所有 lint 错误" --project app-b --auto

# 开启新的对话链（维持上下文）
coderfleet task run "开始实现支付接口" --project app-b --new-chain 支付功能

# 续接已有对话上下文
coderfleet task run "在刚才的支付接口上增加退款支持" --conversation <任务链ID>

# 查看任务列表与实时日志
coderfleet task list
coderfleet task logs <任务ID> -f
```

---

## macOS 系统托盘

CoderFleet 提供原生 macOS 菜单栏 tray，可在登录时自动启动、后台管理 server 生命周期，并在任务完成时推送系统通知。

### 安装（一次性）

```bash
coderfleet tray install
```

这会：
1. 写入 `~/Library/LaunchAgents/com.coderfleet.tray.plist`
2. 立即启动 tray 进程（launchd 管理，登录自动启动，崩溃自动重启）
3. Tray 启动时若 server 未运行，自动以守护进程模式拉起 server

### Tray 功能

菜单栏图标提供：

| 功能 | 说明 |
|------|------|
| Server 状态 | 显示当前 server 运行状态和 PID |
| Open Web UI | 在浏览器打开 `http://localhost:8765` |
| Start / Stop Server | 独立控制 server 生命周期，关闭 tray 不影响 server |
| Check for Updates | 对比 PyPI 最新版本，提示升级命令 |
| Quit | 退出 tray 进程，server 继续运行，任务不中断 |

任务完成、失败或终止时，自动发送系统通知（内容含项目名和状态）。

### Tray 管理命令

```bash
coderfleet tray install    # 安装 LaunchAgent + 立即启动
coderfleet tray uninstall  # 移除 LaunchAgent（server 继续运行）
coderfleet tray start      # 启动 tray（需已安装）
coderfleet tray stop       # 停止 tray（server 继续运行）
coderfleet tray status     # 查看 tray 和 server 状态
coderfleet tray            # 前台调试运行（不经过 LaunchAgent）
```

### Server 与 Tray 的生命周期关系

```
登录
  └── launchd 自动启动 coderfleet tray
          └── tray 检测 server 未运行
                  └── 自动启动 coderfleet server --daemon（写 PID 文件）

关闭 tray（Quit 菜单或 coderfleet tray stop）
  └── tray 进程退出
  └── server 继续运行，任务不中断 ✓

coderfleet server --stop
  └── server 停止
  └── tray 继续运行，5 秒内自动重启 server ✓
```

> **注意**：`coderfleet tray` 目前仅支持 macOS。

---

## 命令速查

### 生命周期

| 命令 | 说明 |
|------|------|
| `coderfleet init` | 交互式初始化工作区（首次使用） |
| `coderfleet build` | 构建自定义 Docker 镜像 |
| `coderfleet apply` | 重新生成 docker-compose.yml 并重启所有容器 |
| `coderfleet up` | 启动所有容器 |
| `coderfleet down` | 停止所有容器 |
| `coderfleet restart` | 重启所有容器 |
| `coderfleet status` | 查看容器、代理中继和镜像状态 |
| `coderfleet server` | 前台启动 FastAPI 调度服务及 Web UI（默认端口 8765） |
| `coderfleet server --daemon` | 后台守护进程模式启动 server |
| `coderfleet server --stop` | 停止后台 server |
| `coderfleet server --status` | 查看 server 运行状态 |

### 账号管理

| 命令 | 说明 |
|------|------|
| `coderfleet account add <名称> TYPE=codex\|claude\|opencode\|hermes\|grok\|kimi [--auth env] [--env-file 路径] [--proxy relay\|off]` | 添加账号 |
| `coderfleet account remove <名称>` | 删除账号（自动停止关联容器） |
| `coderfleet account list` | 列出所有账号及运行状态 |
| `coderfleet login <账号名\|all>` | 登录账号并持久化认证文件 |

### 项目管理

| 命令 | 说明 |
|------|------|
| `coderfleet project add <名称> <账号名> <项目路径> [--ide --ide-port 18080]` | 添加项目，可选启用浏览器 IDE；端口省略时随机分配空闲端口 |
| `coderfleet project remove <名称>` | 删除项目 |
| `coderfleet project list` | 列出所有项目 |

### 容器操作

| 命令 | 说明 |
|------|------|
| `coderfleet enter <项目名>` | 进入项目容器 shell（TTY 直连） |
| `coderfleet logs [项目名]` | 查看日志（不加参数看全部容器） |
| `coderfleet check-proxy` | 验证代理隔离及出口 IP |

### 任务管理（需先启动 server）

| 命令 | 说明 |
|------|------|
| `coderfleet task run "<prompt>" [--project 名称] [--auto] [--at ISO时间]` | 提交异步任务 |
| `coderfleet task list [--status 状态] [--account 账号]` | 查看任务列表 |
| `coderfleet task status <任务ID>` | 查看任务详情 |
| `coderfleet task logs <任务ID> [-f]` | 查看任务日志（`-f` 实时跟踪） |
| `coderfleet task kill <任务ID>` | 终止任务 |
| `coderfleet task clean [N]` | 清理历史记录（保留最近 N 条，默认 30） |

### macOS 系统托盘

| 命令 | 说明 |
|------|------|
| `coderfleet tray install` | 安装 LaunchAgent，登录自动启动 |
| `coderfleet tray uninstall` | 移除 LaunchAgent |
| `coderfleet tray start` | 启动 tray |
| `coderfleet tray stop` | 停止 tray（server 继续运行） |
| `coderfleet tray status` | 查看 tray 和 server 状态 |

---

## 配置文件详解

### config.conf

由 `coderfleet init` 自动生成，修改后执行 `coderfleet apply` 生效。

```conf
# 自建镜像名称和标签
IMAGE_NAME=coderfleet
IMAGE_TAG=latest
BUILD_PLATFORM=linux/amd64   # Apple Silicon 改为 linux/arm64

# 宿主机代理（容器通过 host.docker.internal 访问）
PROXY_HOST=host.docker.internal
PROXY_HTTP_PORT=7890
PROXY_SOCKS5_PORT=7891

# 内部网络
INTERNAL_SUBNET=172.21.0.0/16
RELAY_IP=172.21.0.2
RELAY_LISTEN_PORT=7890

# 代理中继镜像
RELAY_IMAGE=gogost/gost:3
```

### accounts.conf

```conf
# 格式：NAME=<名称>  TYPE=codex|claude|opencode|hermes|grok|kimi  [AUTH=login|env] [ENV_FILE=路径] [PROXY=relay|off]
NAME=alice       TYPE=codex
NAME=bob         TYPE=claude
NAME=carol       TYPE=opencode
NAME=dave        TYPE=hermes
NAME=eve         TYPE=grok     AUTH=env
NAME=frank       TYPE=kimi
NAME=claude-api  TYPE=claude   AUTH=env  ENV_FILE=./accounts/claude-api/env
NAME=opencode-api TYPE=opencode AUTH=env ENV_FILE=./accounts/opencode-api/env
NAME=hermes-api  TYPE=hermes   AUTH=env  ENV_FILE=./accounts/hermes-api/env
NAME=kimi-api    TYPE=kimi     AUTH=env  ENV_FILE=./accounts/kimi-api/env
NAME=local       TYPE=claude   PROXY=off
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `NAME` | 是 | 账号名，只允许字母/数字/连字符 |
| `TYPE` | 是 | `codex` / `claude` / `opencode` / `hermes` / `grok` / `kimi` |
| `AUTH` | 否 | 认证方式，默认 `login`；`claude` / `opencode` / `hermes` / `grok` / `kimi` 可用 `env` |
| `ENV_FILE` | 否 | Docker Compose env_file 路径；`AUTH=env` 时省略则默认 `./accounts/<名称>/env` |
| `PROXY` | 否 | 默认 `relay`（走代理中继）；`off` 表示不注入代理变量 |

`AUTH=env` 示例 env 文件：

```env
# Claude Code
ANTHROPIC_API_KEY=sk-ant-...

# Grok Build
XAI_API_KEY=xai-...

# Kimi Code（新版不直接读取 KIMI_API_KEY，需使用 KIMI_MODEL_*）
KIMI_MODEL_NAME=kimi-for-coding
KIMI_MODEL_API_KEY=sk-...
KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1
```

> `login all` 会自动跳过所有 `AUTH=env` 账号。Grok Build 仅支持 `AUTH=env`，无需执行 `login`。

### projects.conf

```conf
# 格式：NAME=<名称>  ACCOUNT=<账号名>  PATH=<项目路径>  [IDE=on IDE_PORT=<端口>]
# IDE_PORT 可省略，由 CoderFleet 随机选择一个本机空闲端口。
NAME=my-app      ACCOUNT=alice  PATH=~/projects/my-app
NAME=my-app-ide  ACCOUNT=alice  PATH=~/projects/my-app  IDE=on IDE_PORT=18080
NAME=api-server  ACCOUNT=bob    PATH=~/projects/api-server
NAME=grok-app    ACCOUNT=eve    PATH=~/projects/grok-app
```

---

## 网络架构

```
[codex-alice]  ──┐
[claude-bob]   ──┤── intnet（internal=true，无公网路由）
[opencode-carol] ─┤
[hermes-dave]  ──┤         │
[grok-eve]     ──┘         ▼
                    [coderfleet-proxy-relay]（gost）
                            │  HTTP → 宿主机代理
                            ▼
                    宿主机代理（Clash/v2ray）
                            │
                            ▼
                           公网
```

关键设计：
- `intnet` 设置 `internal: true`，Docker 不添加公网路由，容器物理上无法直连公网
- 所有出站流量必须经过 `coderfleet-proxy-relay` 才能到达公网
- `PROXY=off` 账号的项目接入普通外网网络，不经过中继（适合内网服务）

验证代理隔离：

```bash
coderfleet check-proxy
```

---

## 认证机制

| CLI | 容器内认证目录 | 认证方式 |
|-----|---------------|---------|
| Codex | `/home/byclaw/.codex` | `login`（OAuth） |
| Claude Code | `/home/byclaw/.claude` | `login`（OAuth）或 `env`（`ANTHROPIC_API_KEY`） |
| OpenCode | `/home/byclaw/.opencode` | `login` 或 `env`（provider API key） |
| Hermes Agent | `/home/byclaw/.hermes` | `login` 或 `env`（provider API key） |
| Grok Build | `/home/byclaw/.grok` | `env` 仅（`XAI_API_KEY`） |
| Kimi Code | `/home/byclaw/.kimi-code` | `login`（OAuth）或 `env`（`KIMI_MODEL_*`） |

每个账号的认证数据独立存储，容器删除重建后无需重新登录。

---

## 项目结构

```
coderfleet/                      # Python 包
├── cli.py                 # Click 命令组入口
├── config.py              # 工作区路径解析、.conf 文件读写
├── server_daemon.py       # server 守护进程管理（PID 文件、启停）
├── tray.py                # macOS 系统托盘（rumps）+ LaunchAgent 管理
├── config_cmds.py         # account / project 子命令
├── compose.py             # docker-compose.yml 生成器（pyyaml）
├── docker_ops.py          # build / apply / up / down / enter 等命令
├── login_cmd.py           # coderfleet login（TTY 直通，os.execvp）
├── task_cmds.py           # coderfleet task 子命令（HTTP 调用 server）
├── init_wizard.py         # coderfleet init 交互式向导
├── account_type_registry.py # 账号类型注册表（新增账号类型只需编辑此文件）
├── data/                  # 打包进 wheel 的资源文件
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── *.conf.example
└── server/                # FastAPI 调度服务（包内模块）
    ├── main.py            # REST API + WebSocket 终端
    ├── scheduler.py       # 任务队列与执行引擎
    ├── models.py          # Pydantic 数据模型
    ├── docker_mgr.py      # Docker exec 封装
    ├── terminal.py        # WebSocket ↔ docker exec -it 桥接
    └── static/            # Web UI（原生 JS，无构建步骤）
pyproject.toml             # pip/pipx 打包配置
```

### 新增账号类型

账号类型集中在 `account_type_registry.py`，添加新类型只需：
1. 编写 `_build_<type>()` 函数（构建容器内 CLI 命令）
2. 编写 `_extract_<type>()` 函数（从日志提取 session ID）
3. 在 `ACCOUNT_TYPES` 末尾注册一条 `AccountTypeSpec`

其余组件（compose、login、scheduler、前端）全部自动适配。

---

## 常见问题

**Q: 构建镜像时速度很慢怎么办？**

镜像需要下载 Node.js、Python、AI CLI 包，确保宿主机代理正常且 Docker Desktop 已配置代理即可。

**Q: 登录时浏览器无法弹出？**

容器内没有浏览器，CLI 会输出一个 URL，复制到宿主机浏览器打开授权即可。

**Q: macOS Apple Silicon（M 系列芯片）能用吗？**

可以，`coderfleet init` 会自动检测并默认选择 `linux/arm64`。若需 amd64 模拟，修改 `config.conf` 中的 `BUILD_PLATFORM=linux/amd64` 后重新 `coderfleet build`。

**Q: 删除账号后认证数据还在吗？**

`coderfleet account remove <名称>` 只从 `accounts.conf` 移除配置，`accounts/<名称>/` 目录保留。需要彻底清除时手动执行：

```bash
rm -rf ~/.coderfleet/accounts/<名称>
```

**Q: Grok Build 如何配置？**

Grok Build 只支持 API Key 认证：

```bash
coderfleet account add my-grok TYPE=grok --auth env
coderfleet project add my-project my-grok ~/projects/my-project
coderfleet apply
```

编辑 `~/.coderfleet/accounts/my-grok/env`：

```env
XAI_API_KEY=xai-...
```

API Key 在 [console.x.ai](https://console.x.ai/) 获取。

**Q: macOS tray 安装后没有显示图标？**

运行 `coderfleet tray status` 确认 LaunchAgent 是否已加载。如果未显示，可能是 macOS 通知权限问题，在「系统设置 → 通知」中确认 Python 或 Script Editor 的通知权限已开启。

**Q: 关闭 tray 后任务会中断吗？**

不会。Tray 和 server 是两个独立进程。从托盘菜单点击 Quit 只退出 tray 进程，server 继续运行，正在执行的任务不受影响。下次登录时 launchd 会自动重启 tray。

**Q: 如何更新 CLI 版本？**

```bash
# 更新 coderfleet 本身
pip install --upgrade coderfleet
# 或通过 tray 菜单 "Check for Updates" 查看提示

# 重新构建容器镜像（更新容器内的 AI CLI 版本）
coderfleet build
coderfleet restart
```

**Q: 多个账号能同时工作吗？**

可以，每个账号是独立容器，互不影响。打开多个终端窗口，分别 `coderfleet enter <项目名>` 进入即可。

---

## 自定义镜像

如需在镜像中添加更多工具，在自定义镜像流程中追加系统包或语言工具，然后重新构建：

```dockerfile
# 示例：添加 Java
RUN apt-get update && apt-get install -y openjdk-21-jdk
```

```bash
coderfleet build
coderfleet restart
```
