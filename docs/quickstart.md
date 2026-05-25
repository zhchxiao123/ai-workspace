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

镜像包含 Ubuntu、Python、Node.js、Rust、Codex CLI、Claude Code、OpenCode 和 Hermes Agent。

## 4. 添加账号与项目

```bash
coderfleet account add alice TYPE=codex
coderfleet project add app-a alice ~/projects/app-a
```

也可以添加 Claude Code、OpenCode 或 Hermes Agent 账号：

```bash
coderfleet account add bob TYPE=claude
coderfleet account add carol TYPE=opencode
coderfleet account add dave TYPE=hermes
```

## 5. 应用配置并登录

```bash
coderfleet apply
coderfleet login alice
```

登录命令会输出授权 URL。把 URL 复制到宿主机浏览器打开，完成授权后把 code 粘贴回终端。

不同账号类型会调用对应 CLI 的登录或初始化流程：Codex 使用 `codex login --device-auth`，Claude Code 使用 `claude login`，OpenCode 使用 `opencode auth login`，Hermes Agent 使用 `hermes setup`。

## 6. 启动 Web 控制台

```bash
coderfleet server
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
