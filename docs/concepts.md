# 核心概念

## 工作区

工作区是 CoderFleet 保存配置、认证、任务记录和生成文件的位置。默认路径是：

```text
~/.coderfleet/
```

## 账号

账号代表一个 AI CLI 的运行身份。当前支持：

- `codex`
- `claude`
- `opencode`

每个账号有独立认证目录，容器删除重建后通常不需要重新登录。

## 项目

项目是宿主机上的代码目录。CoderFleet 会把项目挂载到对应账号容器的 `/workspace`。

一个项目绑定一个账号：

```bash
coderfleet project add app-a alice ~/projects/app-a
```

## 容器

每个账号对应独立容器。容器内预装开发工具和 AI CLI，用于执行交互式命令或后台任务。

## 调度服务

`coderfleet server` 会启动 FastAPI 服务和 Web 控制台。调度服务负责：

- 接收任务
- 匹配项目和空闲账号
- 执行容器内命令
- 记录任务状态
- 推送实时日志

## 任务链

任务链用于保留上下文。你可以开启新的任务链，也可以续接已有任务链，让 AI CLI 继续基于前序上下文工作。
