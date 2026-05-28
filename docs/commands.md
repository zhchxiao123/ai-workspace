# 命令速查

## 生命周期

| 命令 | 说明 |
| --- | --- |
| `coderfleet init` | 初始化工作区 |
| `coderfleet build` | 构建自定义 Docker 镜像 |
| `coderfleet apply` | 重新生成 compose 并重启容器 |
| `coderfleet up` | 启动所有容器 |
| `coderfleet down` | 停止所有容器 |
| `coderfleet restart` | 重启所有容器 |
| `coderfleet status` | 查看容器、代理和镜像状态 |
| `coderfleet server` | 前台启动调度服务和 Web 控制台（默认端口 8765） |
| `coderfleet server --daemon` | 后台守护进程模式启动 server |
| `coderfleet server --stop` | 停止后台 server |
| `coderfleet server --status` | 查看 server 运行状态 |

## 账号

| 命令 | 说明 |
| --- | --- |
| `coderfleet account add <名称> TYPE=codex\|claude\|opencode\|hermes\|grok` | 添加账号 |
| `coderfleet account remove <名称>` | 删除账号配置 |
| `coderfleet account list` | 查看账号列表 |
| `coderfleet login <账号名\|all>` | 登录账号（`AUTH=env` 账号自动跳过） |

`--auth env` 适用于 `claude`、`opencode`、`hermes` 和 `grok`：

```bash
coderfleet account add grok-api TYPE=grok --auth env
coderfleet account add hermes-api TYPE=hermes --auth env
```

## 项目

| 命令 | 说明 |
| --- | --- |
| `coderfleet project add <名称> <账号名> <项目路径>` | 添加项目 |
| `coderfleet project remove <名称>` | 删除项目 |
| `coderfleet project list` | 查看项目列表 |
| `coderfleet enter <项目名>` | 进入项目容器 shell |

## 任务

| 命令 | 说明 |
| --- | --- |
| `coderfleet task run "<prompt>" [--project 名称] [--auto]` | 提交异步任务 |
| `coderfleet task list [--status 状态] [--account 账号]` | 查看任务列表 |
| `coderfleet task status <任务ID>` | 查看任务详情 |
| `coderfleet task logs <任务ID> [-f]` | 查看任务日志 |
| `coderfleet task kill <任务ID>` | 终止任务 |
| `coderfleet task clean [N]` | 清理历史记录 |

## macOS 系统托盘

| 命令 | 说明 |
| --- | --- |
| `coderfleet tray install` | 安装 LaunchAgent，登录自动启动，立即生效 |
| `coderfleet tray uninstall` | 移除 LaunchAgent（server 继续运行） |
| `coderfleet tray start` | 启动 tray 进程 |
| `coderfleet tray stop` | 停止 tray 进程（server 继续运行） |
| `coderfleet tray status` | 查看 tray 和 server 状态 |
| `coderfleet tray` | 前台调试运行 |

详细说明见 [系统托盘](tray.md)。

## 诊断

| 命令 | 说明 |
| --- | --- |
| `coderfleet logs [项目名]` | 查看容器日志 |
| `coderfleet check-proxy` | 验证代理隔离和出口 IP |
