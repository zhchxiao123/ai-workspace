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
| `coderfleet server [--port N]` | 启动调度服务和 Web 控制台 |

## 账号

| 命令 | 说明 |
| --- | --- |
| `coderfleet account add <名称> TYPE=codex\|claude\|opencode\|hermes` | 添加账号 |
| `coderfleet account remove <名称>` | 删除账号配置 |
| `coderfleet account list` | 查看账号列表 |
| `coderfleet login <账号名\|all>` | 登录账号 |

`--auth env` 适用于 `claude`、`opencode` 和 `hermes`：

```bash
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

## 诊断

| 命令 | 说明 |
| --- | --- |
| `coderfleet logs [项目名]` | 查看容器日志 |
| `coderfleet check-proxy` | 验证代理隔离和出口 IP |
