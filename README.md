# AI Code Manager (aicm)

**更适合中国开发者（CN）体质的多账号 AI 辅助开发方案。**

<video src="https://github.com/user-attachments/assets/37bee884-09c7-40ca-bc7b-87ce26422c8c" controls width="100%"></video>

### 💡 为什么需要 AICM？

在重度 AI 编程开发中，大模型 CLI（如 Claude Code、Codex）经常面临尴尬的使用瓶颈：
* **单账号（Plus 会员）的额度不够用**，频繁遭遇大模型服务商的 Rate Limit 或当日使用上限；
* **高倍数套餐（如 5x/10x/20x 的账号会员）又极其昂贵**，在周期内往往使用量根本用不完，性价比极低；
* 最优解通常是**自己申请 2 个或多个普通/Plus 账号轮流使用**，但在单台机器上频繁切换账号、管理认证数据、以及维护隔离的网络环境极其繁杂。

**AICM 正是为此而生！**

它允许你在单台宿主机上同时运行多个 **Codex CLI** 和 **Claude Code** 账号。每个账号采用独立容器隔离、独立会话认证，并设计了严密的**物理内网中继代理（gost）**网络，确保所有网络出站流量强制且唯一地走宿主机代理出口，完美解决国内开发者在调用 Claude / OpenAI 服务时遭遇的网络封锁和封号风险，实现"多账号额度无缝叠加、轮询提效"。

---

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

`uv tool` 等价于 pipx，将 aicm 装进独立隔离环境，不污染系统 Python。

```bash
uv tool install aicm
```

安装完成后，`aicm` 命令全局可用，工作区默认位于 `~/.aicm/`。

> 如果未安装 uv，也可用 pipx：
> ```bash
> pipx install aicm
> ```

### 方式二：克隆仓库（开发者 / 老用户）

```bash
git clone https://github.com/your-org/aicm.git
cd aicm

# 用 uv 创建虚拟环境并安装（推荐）
uv venv && source .venv/bin/activate
uv pip install -e .

# 或用系统 pip（可能需要 --break-system-packages）
pip install -e .
```

> **向后兼容**：`./aicm.sh` 已改为薄包装层，将所有命令透传给 Python CLI，原有工作流完全不变。克隆目录自动作为工作区，无需额外配置。详见 [从 aicm.sh 迁移指南](changelog/migrate-from-aicm-sh.md)。

---

## 快速开始

### 1. 初始化工作区

```bash
aicm init
```

交互式向导将引导你：
- 确认工作区目录（默认 `~/.aicm/`）
- 填写宿主机代理端口（HTTP / SOCKS5）
- 设置 Docker 镜像名称和构建平台

初始化完成后，工作区结构如下：

```
~/.aicm/
├── config.conf        # 全局配置（镜像、代理、网络）
├── accounts.conf      # 账号配置
├── projects.conf      # 项目配置
├── Dockerfile         # 自建统一镜像
├── entrypoint.sh      # 容器启动脚本
├── docker-compose.yml # 自动生成，勿手动编辑
└── accounts/          # 各账号认证数据（自动管理）
    ├── alice/
    └── bob/
```

### 2. 构建镜像

```bash
aicm build
```

首次构建约需 5~10 分钟，镜像包含：

- Ubuntu 24.04
- Python 3.12（含 uv / ruff / black / mypy）
- Node.js 20
- Rust 1.93.0
- Codex CLI（`@openai/codex`）
- Claude Code（`@anthropic-ai/claude-code`）

### 3. 添加账号与项目

```bash
# Codex 账号
aicm account add alice TYPE=codex
aicm project add app-a alice ~/projects/app-a

# Claude Code 账号
aicm account add bob TYPE=claude
aicm project add app-b bob ~/projects/app-b
```

### 4. 生成配置并启动容器

```bash
aicm apply
```

### 5. 登录账号

```bash
aicm login alice
```

CLI 会输出授权 URL，在宿主机浏览器打开 → 完成授权 → 复制 code → 粘贴回终端。

如果账号容器还未启动，`login` 会自动拉起一个临时认证容器；登录结束后容器删除，认证文件保留在 `accounts/<账号名>/`。

### 6. 进入容器工作

```bash
# 打开多个终端
aicm enter app-a   # 进入 app-a 项目容器（使用 Codex 账号 alice）
aicm enter app-b   # 进入 app-b 项目容器（使用 Claude Code 账号 bob）
```

进入后使用对应 CLI：

```bash
# Codex 容器内
codex "帮我实现用户认证模块"

# Claude Code 容器内
claude "帮我重构这个函数"
```

### 7. 启动调度服务与 Web 控制台

除了交互式进入容器，AICM 还提供 **FastAPI 任务调度服务与 Web 聊天控制台**，可在宿主机直接触发并监控容器内的异步开发任务。

```bash
aicm server
```

启动后可访问：
- **Web UI 对话控制台**：[http://localhost:8765](http://localhost:8765)（多任务聊天界面、日志流式追踪、WebSocket 容器终端）
- **API 交互式文档**：[http://localhost:8765/docs](http://localhost:8765/docs)

### 8. 命令行异步任务

服务运行期间，可在宿主机直接提交后台任务：

```bash
# 一次性开发任务
aicm task run "在 auth.py 中添加 JWT 生成逻辑" --project app-b

# 全自动模式（跳过 CLI 权限确认）
aicm task run "运行并修复所有 lint 错误" --project app-b --auto

# 开启新的对话链（维持上下文）
aicm task run "开始实现支付接口" --project app-b --new-chain 支付功能

# 续接已有对话上下文
aicm task run "在刚才的支付接口上增加退款支持" --conversation <任务链ID>

# 查看任务列表与实时日志
aicm task list
aicm task logs <任务ID> -f
```

---

## 命令速查

### 生命周期

| 命令 | 说明 |
|------|------|
| `aicm init` | 交互式初始化工作区（首次使用） |
| `aicm build` | 构建自定义 Docker 镜像 |
| `aicm apply` | 重新生成 docker-compose.yml 并重启所有容器 |
| `aicm up` | 启动所有容器 |
| `aicm down` | 停止所有容器 |
| `aicm restart` | 重启所有容器 |
| `aicm status` | 查看容器、代理中继和镜像状态 |
| `aicm server [--port N]` | 启动 FastAPI 调度服务及 Web UI（默认端口 8765） |

### 账号管理

| 命令 | 说明 |
|------|------|
| `aicm account add <名称> TYPE=codex\|claude [--auth env] [--env-file 路径] [--proxy relay\|off]` | 添加账号 |
| `aicm account remove <名称>` | 删除账号（自动停止关联容器） |
| `aicm account list` | 列出所有账号及运行状态 |
| `aicm login <账号名\|all>` | 登录账号并持久化认证文件 |

### 项目管理

| 命令 | 说明 |
|------|------|
| `aicm project add <名称> <账号名> <项目路径>` | 添加项目 |
| `aicm project remove <名称>` | 删除项目 |
| `aicm project list` | 列出所有项目 |

### 容器操作

| 命令 | 说明 |
|------|------|
| `aicm enter <项目名>` | 进入项目容器 shell（TTY 直连） |
| `aicm logs [项目名]` | 查看日志（不加参数看全部容器） |
| `aicm check-proxy` | 验证代理隔离及出口 IP |

### 任务管理（需先启动 server）

| 命令 | 说明 |
|------|------|
| `aicm task run "<prompt>" [--project 名称] [--auto] [--at ISO时间]` | 提交异步任务 |
| `aicm task list [--status 状态] [--account 账号]` | 查看任务列表 |
| `aicm task status <任务ID>` | 查看任务详情 |
| `aicm task logs <任务ID> [-f]` | 查看任务日志（`-f` 实时跟踪） |
| `aicm task kill <任务ID>` | 终止任务 |
| `aicm task clean [N]` | 清理历史记录（保留最近 N 条，默认 30） |

---

## 配置文件详解

### config.conf

由 `aicm init` 自动生成，修改后执行 `aicm apply` 生效。

```conf
# 自建镜像名称和标签
IMAGE_NAME=ai-code-manager
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
# 格式：NAME=<名称>  TYPE=codex|claude  [AUTH=login|env] [ENV_FILE=路径] [PROXY=relay|off]
NAME=alice       TYPE=codex
NAME=bob         TYPE=claude
NAME=claude-api  TYPE=claude  AUTH=env  ENV_FILE=./accounts/claude-api/env
NAME=local       TYPE=claude  PROXY=off
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `NAME` | 是 | 账号名，只允许字母/数字/连字符 |
| `TYPE` | 是 | `codex` 使用 Codex CLI，`claude` 使用 Claude Code |
| `AUTH` | 否 | 认证方式，默认 `login`；Claude Code 可用 `env` 通过 API Key 认证 |
| `ENV_FILE` | 否 | Docker Compose env_file 路径；`AUTH=env` 时省略则默认 `./accounts/<名称>/env` |
| `PROXY` | 否 | 默认 `relay`（走代理中继）；`off` 表示不注入代理变量，连接普通网络 |

`AUTH=env` 用于 Claude Code API key 场景：

```bash
aicm account add claude-api TYPE=claude --auth env
# 然后编辑 ~/.aicm/accounts/claude-api/env
```

示例 env 文件：

```env
ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_BASE_URL=https://api.anthropic.com
```

> 注意：Claude Code 会优先使用 `ANTHROPIC_API_KEY`，`login all` 会自动跳过 `AUTH=env` 账号。

### projects.conf

```conf
# 格式：NAME=<名称>  ACCOUNT=<账号名>  PATH=<项目路径>
NAME=my-app      ACCOUNT=alice  PATH=~/projects/my-app
NAME=api-server  ACCOUNT=bob    PATH=~/projects/api-server
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `NAME` | 是 | 项目名，只允许字母/数字/连字符 |
| `ACCOUNT` | 是 | 使用的账号名 |
| `PATH` | 是 | 挂载进容器 `/workspace` 的宿主机目录，支持 `~` |

---

## 网络架构

```
[codex-alice]  ──┐
[claude-bob]   ──┤── intnet（internal=true，无公网路由）
[codex-carol]  ──┘         │
                            ▼
                    [aicm-proxy-relay]（gost）
                            │  HTTP → 宿主机代理
                            ▼
                    宿主机代理（Clash/v2ray）
                            │
                            ▼
                           公网
```

关键设计：
- `intnet` 设置 `internal: true`，Docker 不添加公网路由，容器物理上无法直连公网
- 所有出站流量必须经过 `aicm-proxy-relay` 才能到达公网
- `PROXY=off` 账号的项目接入普通外网网络，不经过中继（适合内网服务）

验证代理隔离：

```bash
aicm check-proxy
```

正常输出：

```
── 代理连通性（应全部通）
  claude-bob   → proxy-relay: 通
  codex-alice  → proxy-relay: 通

── 直连公网封锁（应全部封锁）
  claude-bob   → 8.8.8.8:443: 已封锁
  codex-alice  → 8.8.8.8:443: 已封锁

── 代理出口 IP
  claude-bob   出口 IP: x.x.x.x
  codex-alice  出口 IP: x.x.x.x
```

---

## 认证机制

两种 CLI 的认证目录挂载方式：

| CLI | 认证目录（容器内） | 本地存储 |
|-----|--------------------|----------|
| Codex | `/home/byclaw/.codex` | `accounts/<名称>/` |
| Claude Code | `/home/byclaw/.claude` | `accounts/<名称>/` |
| Claude Code API key | 同上 | `ENV_FILE` 指定文件 |

每个账号的认证数据独立存储，容器删除重建后无需重新登录。

---

## 项目结构

```
aicm/                      # Python 包
├── cli.py                 # Click 命令组入口
├── config.py              # 工作区路径解析、.conf 文件读写
├── config_cmds.py         # account / project 子命令
├── compose.py             # docker-compose.yml 生成器（pyyaml）
├── docker_ops.py          # build / apply / up / down / enter 等命令
├── login_cmd.py           # aicm login（TTY 直通，os.execvp）
├── task_cmds.py           # aicm task 子命令（HTTP 调用 server）
├── init_wizard.py         # aicm init 交互式向导
├── data/                  # 打包进 wheel 的资源文件
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── *.conf.example
└── server/                # FastAPI 调度服务
    ├── main.py            # REST API + WebSocket 终端
    ├── scheduler.py       # 任务队列与执行引擎
    ├── models.py          # Pydantic 数据模型
    ├── docker_mgr.py      # Docker exec 封装
    ├── terminal.py        # WebSocket ↔ docker exec -it 桥接
    └── static/            # Web UI（原生 JS，无构建步骤）
aicm.sh                    # 薄包装层，透传给 Python CLI（向后兼容）
pyproject.toml             # pip/pipx 打包配置
```

---

## 常见问题

**Q: 构建镜像时速度很慢怎么办？**

镜像需要下载 Node.js、Python 以及两个 CLI 包，确保宿主机代理正常且 Docker Desktop 已配置代理即可。

**Q: 登录时浏览器无法弹出？**

容器内没有浏览器，CLI 会输出一个 URL，复制到宿主机浏览器打开授权即可。

**Q: macOS Apple Silicon（M 系列芯片）能用吗？**

可以，`aicm init` 会自动检测并默认选择 `linux/arm64`。若需 amd64 模拟，修改 `config.conf` 中的 `BUILD_PLATFORM=linux/amd64` 后重新 `aicm build`。

**Q: 删除账号后认证数据还在吗？**

`aicm account remove <名称>` 只从 `accounts.conf` 移除配置，`accounts/<名称>/` 目录保留。需要彻底清除时手动执行：

```bash
rm -rf ~/.aicm/accounts/<名称>
```

**Q: 如何更新 CLI 版本？**

修改工作区 `Dockerfile` 中对应的 `npm install -g` 命令，然后重新构建：

```bash
aicm build
aicm restart
```

**Q: 老用户如何从 aicm.sh 迁移？**

无需迁移，`./aicm.sh` 已更新为薄包装层，自动透传命令给 Python CLI。也可直接安装后用 `aicm` 命令，两者完全等价：

```bash
pip install -e .           # 在仓库目录中
export AICM_WORKSPACE=/path/to/your/workspace   # 指向已有工作区
aicm status
```

**Q: 多个账号能同时工作吗？**

可以，每个账号是独立容器，互不影响。打开多个终端窗口，分别 `aicm enter <项目名>` 进入即可。

---

## 自定义镜像

如需在镜像中添加更多工具，编辑工作区的 `Dockerfile`（路径：`~/.aicm/Dockerfile`），然后重新构建：

```dockerfile
# 示例：添加 Java
RUN apt-get update && apt-get install -y openjdk-21-jdk
```

```bash
aicm build
aicm restart
```
