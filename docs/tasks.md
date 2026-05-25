# 异步任务

异步任务需要先启动调度服务：

```bash
coderfleet server
```

## 提交任务

```bash
coderfleet task run "在 auth.py 中添加 JWT 生成逻辑" --project app-a
```

调度器会按项目绑定账号选择对应 CLI 执行任务：

| 账号类型 | 后台任务命令 |
| --- | --- |
| `codex` | `codex exec --json` |
| `claude` | `claude -p --output-format stream-json` |
| `opencode` | `opencode run --format json` |
| `hermes` | `hermes chat -q` |

开启全自动模式：

```bash
coderfleet task run "运行并修复所有 lint 错误" --project app-a --auto
```

`--auto` 会映射到各 CLI 的非交互执行能力。对 Hermes Agent 来说，CoderFleet 会使用 `--yolo`，并在容器启动时关闭 Hermes 的交互式审批提示。

## 任务链

开启新的任务链：

```bash
coderfleet task run "开始实现支付接口" --project app-a --new-chain 支付功能
```

续接已有任务链：

```bash
coderfleet task run "增加退款支持" --conversation <任务链ID>
```

任务链会保存各 CLI 返回的原生会话 ID。Hermes Agent 的日志里会输出 `Session: <id>`，CoderFleet 会捕获该 ID 并在续接任务时传给 `hermes --resume <id> chat -q`。

## 查看任务

```bash
coderfleet task list
coderfleet task status <任务ID>
```

## 查看日志

```bash
coderfleet task logs <任务ID>
coderfleet task logs <任务ID> -f
```

## 终止任务

```bash
coderfleet task kill <任务ID>
```

## 清理历史

默认保留最近 30 条：

```bash
coderfleet task clean
```

指定保留数量：

```bash
coderfleet task clean 100
```
