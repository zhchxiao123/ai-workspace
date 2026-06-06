# Web 控制台与调度服务

## 启动方式

**前台运行**（适合开发调试）：

```bash
coderfleet server
```

**后台守护进程**（推荐生产使用）：

```bash
coderfleet server --daemon    # 启动，写入 PID 文件
coderfleet server --status    # 查看运行状态
coderfleet server --stop      # 停止
```

**macOS 用户推荐使用系统托盘**，登录自动启动，无需手动管理 server：

```bash
coderfleet tray install
```

详见 [系统托盘](tray.md)。

默认地址：

```text
http://localhost:8765
```

## 主要页面

### AI 对话

用于提交开发任务、查看任务链上下文，并跟踪对话历史。

### 任务监控

展示运行中、已完成、失败和已终止的任务。支持查看实时日志、终止任务和清理历史记录。

### 工作流

用于组织更复杂的任务流程和模板化执行方式。

### 项目

展示项目状态、关联任务和项目终端。可以在浏览器里连接项目容器终端。

### 账号

展示账号类型、容器状态、忙碌状态和相关资源。支持 Codex、Claude Code、OpenCode、Hermes Agent、Grok Build、Kimi Code 全部类型。

## API 文档

开发环境下可以访问：

```text
http://localhost:8765/docs
```

这里是 FastAPI 自动生成的接口文档，面向二次开发和调试。
