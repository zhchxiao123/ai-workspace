# macOS 系统托盘

CoderFleet 提供原生 macOS 菜单栏 tray，通过 launchd LaunchAgent 实现登录自动启动，并在任务完成时推送系统通知。

## 安装

```bash
coderfleet tray install
```

这一条命令会：

1. 将当前 Python 路径和工作区写入 `~/Library/LaunchAgents/com.coderfleet.tray.plist`
2. 通过 `launchctl load` 立即启动 tray 进程
3. Tray 启动时若 server 未运行，自动以守护进程模式拉起 server

之后每次登录 macOS，launchd 会自动启动 tray；tray 崩溃时也会自动重启。

## 菜单功能

| 菜单项 | 说明 |
| --- | --- |
| `Server: Running (PID …)` | 当前 server 状态，仅显示 |
| `Open Web UI` | 在默认浏览器打开 `http://localhost:8765` |
| `Start Server` | 以守护进程模式启动 server |
| `Stop Server` | 停止 server（tray 继续运行，5 秒内自动重启） |
| `Check for Updates` | 对比 PyPI 最新版本，有更新时显示升级命令 |
| `Quit` | 退出 tray 进程，**server 继续运行，任务不中断** |

## 任务通知

Tray 每 5 秒轮询一次 server 的任务状态。任务进入以下状态时推送系统通知：

| 状态 | 通知标题 |
| --- | --- |
| `done` | CoderFleet: Task Done |
| `failed` | CoderFleet: Task Failed |
| `killed` | CoderFleet: Task Killed |

通知内容包含项目名称。若 macOS 通知权限未授权，请在「系统设置 → 通知」中为 Python 或 Script Editor 开启通知权限。

## 生命周期关系

Tray 和 server 是两个完全独立的进程，互不依赖：

```
登录
  └── launchd 自动启动 tray
          └── server 未运行时自动拉起 server --daemon

关闭 tray（Quit 菜单 / coderfleet tray stop）
  └── tray 退出
  └── server 继续运行，任务不中断 ✓

coderfleet server --stop
  └── server 停止
  └── tray 继续运行，watchdog 5 秒内自动重启 server ✓
```

## 管理命令

```bash
coderfleet tray install    # 安装 LaunchAgent + 立即启动
coderfleet tray uninstall  # 移除 LaunchAgent（server 不受影响）
coderfleet tray start      # 启动 tray（需已安装）
coderfleet tray stop       # 停止 tray（server 不受影响）
coderfleet tray status     # 查看 tray 和 server 的运行状态
coderfleet tray            # 前台运行（调试 / 首次测试）
```

## 卸载

```bash
coderfleet tray uninstall
```

移除 LaunchAgent plist 并停止 tray 进程。Server 和所有任务数据不受影响。

## 平台说明

`coderfleet tray` 目前仅支持 macOS，依赖 `rumps`（macOS 菜单栏框架）。在 Linux 或 Windows 上运行 `coderfleet tray install` 会提示不支持。
