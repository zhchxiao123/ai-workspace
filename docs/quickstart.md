# 快速开始

## 1. 安装 CoderFleet

推荐使用 uv tool 安装：

```bash
uv tool install coderfleet
```

如果尚未安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv python install 3.12
```

## 2. 初始化工作区

```bash
coderfleet init
```

向导会生成默认工作区，通常位于：

```text
~/.coderfleet/
```

主要文件包括：

```text
config.conf
accounts.conf
projects.conf
docker-compose.yml
accounts/
```

## 3. 构建镜像

```bash
coderfleet build
```

镜像包含 Ubuntu、Python、Node.js、Rust、Codex CLI、Claude Code、OpenCode、Hermes Agent、Grok Build 和 Kimi Code。

## 4. 添加账号与项目

```bash
coderfleet account add alice TYPE=codex
coderfleet project add app-a alice ~/projects/app-a
```

也可以添加其他类型账号：

```bash
coderfleet account add bob TYPE=claude
coderfleet account add carol TYPE=opencode
coderfleet account add dave TYPE=hermes
coderfleet account add eve TYPE=grok --auth env   # Grok 只支持 API key
coderfleet account add frank TYPE=kimi
```

Grok Build 账号需要在 `~/.coderfleet/accounts/eve/env` 中配置 `XAI_API_KEY=xai-...`，无需执行 login。

Kimi Code 账号可以使用 `coderfleet login frank` 完成 OAuth 登录；也可以用 `TYPE=kimi --auth env`，在 env 文件里配置 `KIMI_MODEL_NAME` 和 `KIMI_MODEL_API_KEY`。

## 5. 应用配置并登录

```bash
coderfleet apply
coderfleet login alice   # AUTH=env 账号自动跳过
```

登录命令会输出授权 URL。把 URL 复制到宿主机浏览器打开，完成授权后把 code 粘贴回终端。

## 6. 启动服务

**macOS — 推荐使用系统托盘**（登录自动启动，后台守护）：

```bash
coderfleet tray install
```

**其他平台或手动启动**：

```bash
coderfleet server              # 前台运行
coderfleet server --daemon     # 后台守护进程
```

默认访问：

- Web 控制台：http://localhost:8765
- API 文档：http://localhost:8765/docs

## 7. 提交第一个任务

```bash
coderfleet task run "运行测试并修复失败用例" --project app-a --auto
coderfleet task list
```

查看实时日志：

```bash
coderfleet task logs <任务ID> -f
```

macOS 用户安装 tray 后，任务完成时会自动推送系统通知。
