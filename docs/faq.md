# 常见问题

## 构建镜像很慢怎么办？

镜像需要下载系统包、Node.js 工具、Python 包和 AI CLI。请确认宿主机代理正常，并且 Docker Desktop 已正确配置代理。Hermes Agent 会安装在独立 Python 虚拟环境中，首次构建会额外下载 provider SDK。

## 登录时为什么没有自动打开浏览器？

容器内没有浏览器。登录命令会输出授权 URL，需要复制到宿主机浏览器打开。

## macOS Apple Silicon 可以用吗？

可以。`coderfleet init` 会尝试选择合适的平台。如果需要调整，可修改 `config.conf` 中的 `BUILD_PLATFORM`。

## 删除账号后认证数据还在吗？

还在。`coderfleet account remove` 只移除配置，不删除认证目录。彻底删除需要手动清理：

```bash
rm -rf ~/.coderfleet/accounts/<账号名>
```

## 多个账号能同时工作吗？

可以。每个账号对应独立容器，可以并行进入多个项目容器或提交多个异步任务。

## Hermes 账号应该怎么配置？

交互式认证：

```bash
coderfleet account add dave TYPE=hermes
coderfleet login dave
```

API key 认证：

```bash
coderfleet account add hermes-api TYPE=hermes --auth env
```

然后在 `accounts/hermes-api/env` 中写入对应 provider 的 API key，并在绑定项目容器里执行 `hermes config set model.provider <provider>`。Hermes 的配置和会话数据保存在该账号目录，对应容器内路径是 `/home/byclaw/.hermes`。

## Grok Build 账号如何配置？

Grok Build 只支持 API key 认证：

```bash
coderfleet account add my-grok TYPE=grok --auth env
coderfleet project add my-project my-grok ~/projects/my-project
coderfleet apply
```

在 `~/.coderfleet/accounts/my-grok/env` 中填入：

```env
XAI_API_KEY=xai-...
```

API key 在 [console.x.ai](https://console.x.ai/) 获取。无需执行 `coderfleet login`。

## macOS 系统托盘怎么安装？

```bash
coderfleet tray install
```

安装后 tray 进程由 launchd 管理，登录自动启动。Tray 会在 server 未运行时自动拉起 server，任务完成时推送系统通知。详见 [系统托盘](tray.md)。

## 关闭 tray 后任务会中断吗？

不会。Tray 和 server 是两个独立进程。从菜单点击 Quit 只退出 tray，server 继续运行，正在执行的任务不受影响。

## Web 控制台可以部署到公网吗？

不建议直接公网暴露。控制台会操作本机 Docker 容器和项目目录，更适合本机或可信内网使用。如果必须远程访问，建议放在受保护的 VPN 或认证网关后面。
