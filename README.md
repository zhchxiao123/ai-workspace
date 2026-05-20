# AI Code Manager (aicm)

**更适合中国开发者（CN）体质的多账号 AI 辅助开发方案。**

<video src="https://github.com/user-attachments/assets/95d5b7be-c54d-45d9-a9f7-c509759b2bba" controls width="100%"></video>

### 💡 为什么需要 AICM？

在重度 AI 编程开发中，大模型 CLI（如 Claude Code、Codex）经常面临尴尬的使用瓶颈：
* **单账号（Plus 会员）的额度不够用**，频繁遭遇大模型服务商的 Rate Limit 或当日使用上限；
* **高倍数套餐（如 5x/10x/20x 的账号会员）又极其昂贵**，在周期内往往使用量根本用不完，性价比极低；
* 最优解通常是**自己申请 2 个或多个普通/Plus 账号轮流使用**，但在单台机器上频繁切换账号、管理认证数据、以及维护隔离的网络环境极其繁杂。

**AICM 正是为此而生！**

它允许你在单台宿主机上同时运行多个 **Codex CLI** 和 **Claude Code** 账号。每个账号采用独立容器隔离、独立会话认证，并设计了严密的**物理内网中继代理（gost）**网络，确保所有网络出站流量强制且唯一地走宿主机代理出口，完美解决国内开发者在调用 Claude / OpenAI 服务时遭遇的网络封锁和封号风险，实现“多账号额度无缝叠加、轮询提效”。

---

## 目录结构

```
ai-workspace/
├── aicm.sh           # 主控脚本（唯一入口）
├── Dockerfile        # 自建统一镜像
├── entrypoint.sh     # 容器启动脚本
├── config.conf       # 全局配置（镜像、代理、网络）
├── accounts.conf     # 账号配置
├── docker-compose.yml  # 自动生成，勿手动编辑
└── accounts/
    ├── alice/        # 账号 alice 的认证数据（自动创建）
    └── bob/
```

## 环境要求

- Docker Desktop（macOS / Linux）或 Docker Engine + docker-compose
- Python 3（系统自带，用于生成 compose 文件）
- 宿主机已运行代理软件（Clash / v2ray 等），并开启 allow-lan

---

## 快速开始

### 1. 赋权

```bash
chmod +x aicm.sh entrypoint.sh
```

### 2. 配置代理端口

编辑 `config.conf`，填入你的代理工具实际端口：

```conf
PROXY_HTTP_PORT=7890    # Clash HTTP 端口
PROXY_SOCKS5_PORT=7891  # Clash SOCKS5 端口
```

> **重要**：宿主机代理必须监听 `0.0.0.0`（不能只监听 127.0.0.1）。
> Clash 配置里设置 `allow-lan: true` 即可。

### 3. 构建镜像

```bash
./aicm.sh build
```

首次构建约需 5~10 分钟，镜像包含：

- Ubuntu 24.04
- Python 3.12（含 uv / ruff / black / mypy）
- Node.js 20
- Rust 1.93.0
- Codex CLI（`@openai/codex`）
- Claude Code（`@anthropic-ai/claude-code`）

### 4. 添加账号

```bash
# Codex 账号
./aicm.sh account add alice TYPE=codex
./aicm.sh project add app-a alice ~/projects/app-a

# Claude Code 账号
./aicm.sh account add bob TYPE=claude
./aicm.sh project add app-b bob ~/projects/app-b
```

### 5. 生成配置并启动

```bash
./aicm.sh apply
```

### 6. 登录账号

```bash
./aicm.sh login alice
```

每个账号会输出一个授权 URL，在宿主机浏览器打开 → 完成授权 → 复制 code → 粘贴回终端。

如果账号容器还没有启动，`login` 会自动启动一个临时认证容器，只挂载 `accounts/<账号名>` 目录；登录结束后容器删除，认证文件会保留。

### 7. 进入容器工作

```bash
# 打开多个终端
./aicm.sh enter app-a   # 进入 app-a 项目容器（使用 Codex 账号）
./aicm.sh enter app-b   # 进入 app-b 项目容器（使用 Claude Code 账号）
```

进入后使用对应 CLI：

```bash
# Codex 容器内
codex "帮我实现用户认证模块"

# Claude Code 容器内
claude "帮我重构这个函数"
```

### 8. 启动调度服务与 Web 交互控制台

除了在容器内以交互方式工作外，AICM 还提供了一个 **FastAPI 任务调度服务与 Web 聊天控制台**，使用户能够在宿主机直接触发和监控隔离容器内的异步开发任务。

运行以下命令启动服务：

```bash
./aicm.sh server
```

启动后，可在宿主机浏览器中访问：
* **Web UI 对话控制台**：[http://localhost:8765](http://localhost:8765)（提供多任务聊天界面、任务日志流式追踪、以及 WebSocket 实现的容器直连 Web 终端）
* **API 交互式文档**：[http://localhost:8765/docs](http://localhost:8765/docs)

### 9. 命令行异步任务 (Task)

在服务运行期间，你也可以在宿主机直接通过命令行提交后台异步任务，任务结果与对话链会自动被调度器维护和续接：

```bash
# 提交一个一次性异步开发任务（指定项目）
./aicm.sh task run "在 auth.py 中添加 JWT 生成逻辑" --project app-b

# 提交全自动模式任务（跳过 CLI 的确认提问，直接接受修改）
./aicm.sh task run "运行并修复所有 lint 错误" --project app-b --auto

# 开启一个新的对话链（维持上下文）
./aicm.sh task run "开始实现支付接口" --project app-b --new-chain 支付功能

# 续接已有的对话上下文
./aicm.sh task run "在刚才的支付接口上增加退款支持" --conversation <任务链ID>

# 查看历史任务列表与实时流日志追踪
./aicm.sh task list
./aicm.sh task logs <任务ID> -f
```

---


## 命令速查

| 命令 | 说明 |
|------|------|
| `./aicm.sh build` | 构建自定义镜像 |
| `./aicm.sh account add <名称> TYPE=codex\|claude [AUTH=login\|env] [ENV_FILE=路径] [PROXY=relay\|off]` | 添加账号 |
| `./aicm.sh account remove <名称>` | 删除账号 |
| `./aicm.sh account list` | 列出所有账号及状态 |
| `./aicm.sh project add <名称> <账号名> <项目路径>` | 添加项目 |
| `./aicm.sh project remove <名称>` | 删除项目 |
| `./aicm.sh project list` | 列出所有项目 |
| `./aicm.sh apply` | 重新生成配置并重启所有容器 |
| `./aicm.sh up` | 启动所有容器 |
| `./aicm.sh down` | 停止所有容器 |
| `./aicm.sh restart` | 重启所有容器 |
| `./aicm.sh status` | 查看容器和镜像状态 |
| `./aicm.sh logs [项目名]` | 查看日志（不加参数看全部） |
| `./aicm.sh enter <项目名>` | 进入项目容器 shell |
| `./aicm.sh login <账号名\|all>` | 登录账号并持久化认证文件 |
| `./aicm.sh check-proxy` | 验证代理隔离是否生效 |
| `./aicm.sh task <子命令>` | 在宿主机发起、查看、管理异步任务（具体子命令运行 `./aicm.sh task` 查看） |
| `./aicm.sh server` | 启动 FastAPI 调度服务及 Web UI 界面（默认端口 8765） |


---

## 配置文件详解

### config.conf

```conf
# 自建镜像名称和标签
IMAGE_NAME=ai-code-manager
IMAGE_TAG=latest

# 宿主机代理（容器通过 host.docker.internal 访问）
PROXY_HOST=host.docker.internal
PROXY_HTTP_PORT=7890      # HTTP 代理端口（优先）
PROXY_SOCKS5_PORT=7891    # SOCKS5 端口（备用）

# 内部网络
INTERNAL_SUBNET=172.21.0.0/16   # 隔离网络网段
RELAY_IP=172.21.0.2             # 代理中继容器固定 IP
RELAY_LISTEN_PORT=7890          # 中继对内网监听端口

# 代理中继镜像（gost 支持 HTTP/SOCKS5 协议转换）
RELAY_IMAGE=gogost/gost:3
```

修改 `config.conf` 后执行 `./aicm.sh apply` 生效。

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
| `AUTH` | 否 | 认证方式，默认 `login`；Claude Code 可用 `env` 通过环境变量认证 |
| `ENV_FILE` | 否 | 传给 Docker Compose 的环境变量文件；`AUTH=env` 时省略则默认 `./accounts/<名称>/env` |
| `PROXY` | 否 | 默认 `relay`，该账号下项目使用代理中继；`off` 表示该账号下项目不注入代理变量，并连接普通外网网络 |

`AUTH=env` 目前用于 Claude Code API key 场景。可以先添加账号，后续再编辑默认 env 文件：

```bash
./aicm.sh account add claude-api TYPE=claude AUTH=env
# 默认配置文件：./accounts/claude-api/env
```

示例 `accounts/claude-api/env`：

```env
ANTHROPIC_API_KEY=sk-ant-...
# 可选：通过网关或代理转发 Claude API
# ANTHROPIC_BASE_URL=https://api.anthropic.com
# ANTHROPIC_MODEL=claude-sonnet-4-5
```

注意：Claude Code 会优先使用 `ANTHROPIC_API_KEY`，即使该账号也有订阅登录态，仍可能走 API 计费。执行 `/status` 可确认当前认证方式。

修改 `accounts.conf` 后执行 `./aicm.sh apply` 生效。

`PROXY=off` 适合只访问内网、局域网服务，或希望某个账号关联的项目完全不经过代理的场景。使用该账号的项目不会等待 `aicm-proxy-relay`，也不会设置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 等变量。

### projects.conf

```conf
# 格式：NAME=<名称>  ACCOUNT=<账号名>  PATH=<项目路径>
NAME=my-app      ACCOUNT=alice  PATH=~/projects/my-app
NAME=api-server  ACCOUNT=bob    PATH=~/projects/api-server
NAME=local-app   ACCOUNT=local  PATH=~/projects/local-app
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `NAME` | 是 | 项目名，只允许字母/数字/连字符 |
| `ACCOUNT` | 是 | 运行这个项目时使用的账号名 |
| `PATH` | 是 | 挂载进容器 `/workspace/<项目名>` 的宿主机项目目录，支持 `~` |

修改 `projects.conf` 后执行 `./aicm.sh apply` 生效。

---

## 网络架构

```
[codex-alice]  ──┐
[claude-bob]   ──┤── intnet（internal=true，无公网路由）
[codex-carol]  ──┘         │
                            ▼
                    [aicm-proxy-relay]（gost）
                            │  HTTP 优先，SOCKS5 备用
                            ▼
                    宿主机代理（Clash/v2ray）
                            │
                            ▼
                           公网
```

关键设计：

- `intnet` 设置 `internal: true`，Docker 不添加公网路由，容器物理上无法直连公网
- 所有出站流量必须经过 `aicm-proxy-relay` 才能到达公网
- gost 对内网提供统一 HTTP 代理接口，上游优先 HTTP，自动 fallback SOCKS5

验证代理隔离：

```bash
./aicm.sh check-proxy
```

正常输出：

```
── 代理连通性（应全部通）
  codex-alice  → proxy-relay: ✓ 通
  claude-bob   → proxy-relay: ✓ 通

── 直连公网封锁（应全部封锁）
  codex-alice  → 8.8.8.8:443: ✓ 已封锁
  claude-bob   → 8.8.8.8:443: ✓ 已封锁

── 代理出口 IP
  codex-alice  出口 IP: x.x.x.x
  claude-bob   出口 IP: x.x.x.x
```

---

## 认证机制

两种 CLI 的认证目录不同，挂载方式也不同：

| CLI | 认证目录（容器内） | 环境变量 | 本地存储位置 |
|-----|--------------------|----------|-------------|
| Codex | `/home/byclaw/.codex` | `CODEX_HOME` | `accounts/<名称>/` |
| Claude Code | `/home/byclaw/.claude` | `CLAUDE_CONFIG_DIR` | `accounts/<名称>/` |
| Claude Code API key | 同上 | `ANTHROPIC_API_KEY` 等 | `ENV_FILE` 指定的文件 |

每个账号的认证数据独立存储在 `accounts/<名称>/` 目录中，容器删除重建后无需重新登录。
使用 `AUTH=env` 的 Claude Code 账号不需要执行 `./aicm.sh login`，`login all` 会自动跳过这类账号。

---

## 常见问题

**Q: 构建镜像时速度很慢怎么办？**

镜像构建需要下载 Node.js、Python 以及两个 CLI 包，确保代理正常即可。构建时 Docker 也会使用宿主机的代理（需在 Docker Desktop 设置中配置代理）。

**Q: 登录时浏览器无法弹出？**

容器内没有浏览器，CLI 会输出一个 URL，复制到宿主机浏览器打开授权即可。

**Q: macOS Apple Silicon（M 系列芯片）能用吗？**

可以，镜像构建时指定 `linux/amd64` 平台，Docker Desktop 会通过 Rosetta 模拟运行。如需原生 arm64 支持，修改 `config.conf` 中的 `BUILD_PLATFORM=linux/arm64`（需确认 CLI 支持该平台）。

**Q: 删除账号后认证数据还在吗？**

`./aicm.sh account remove <名称>` 只从 `accounts.conf` 移除配置，`accounts/<名称>/` 目录保留。需要彻底清除时手动执行：

```bash
rm -rf accounts/<名称>
```

**Q: 如何更新 CLI 版本？**

修改 Dockerfile 中对应的 `npm install -g` 命令（加版本号），然后重新构建：

```bash
./aicm.sh build
./aicm.sh restart
```

**Q: 多个账号能同时工作吗？**

可以，每个账号是独立容器，互不影响。打开多个终端窗口，分别 `./aicm.sh enter <项目名>` 进入即可。

---

## 自定义镜像

如需在镜像中添加更多工具，编辑 `Dockerfile`，然后重新构建：

```dockerfile
# 示例：添加 Java
RUN apt-get update && apt-get install -y openjdk-21-jdk
```

```bash
./aicm.sh build
./aicm.sh restart
```

---

## 项目文件说明

| 文件 | 说明 |
|------|------|
| `aicm.sh` | 主控脚本，所有操作的入口 |
| `Dockerfile` | 自建镜像定义，修改后需重新 build |
| `entrypoint.sh` | 容器启动脚本，负责代理等待和目录初始化 |
| `config.conf` | 全局配置，修改后需 apply |
| `accounts.conf` | 账号配置，修改后需 apply |
| `docker-compose.yml` | 自动生成，**不要手动编辑** |
| `accounts/` | 各账号认证数据目录（自动管理） |
| `server/` | FastAPI 后台调度服务、WebSocket 终端以及 Web UI 对话界面的源码与资源 |
| `tasks/` | 自动创建，存储后台异步任务的状态 JSON 与对应执行日志 |

