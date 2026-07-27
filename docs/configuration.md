# 配置文件

配置文件位于 CoderFleet 工作区，默认是 `~/.coderfleet/`。

## config.conf

```conf
IMAGE_NAME=coderfleet
IMAGE_TAG=latest
BUILD_PLATFORM=linux/amd64
CONTAINER_TIMEZONE=Asia/Shanghai

DOCKER_SOCKET=off
DOCKER_SOCKET_TARGET=/var/run/docker.sock

PROXY_HOST=host.docker.internal
PROXY_HTTP_PORT=7890
PROXY_SOCKS5_PORT=7891

INTERNAL_SUBNET=172.21.0.0/16
RELAY_IP=172.21.0.2
RELAY_LISTEN_PORT=7890

RELAY_IMAGE=gogost/gost:3
```

修改后执行：

```bash
coderfleet apply
```

`CONTAINER_TIMEZONE` 使用 IANA 时区名称，例如 `Asia/Shanghai`、`UTC` 或
`America/New_York`。修改后执行 `coderfleet apply` 重新创建持久容器；新启动的
临时任务容器和登录容器也会使用该时区。未配置时默认使用 `Asia/Shanghai`。

### Docker socket

如需在项目容器里执行 `docker` 并控制宿主机 Docker，可开启 Docker socket 挂载：

```bash
coderfleet config set DOCKER_SOCKET auto
coderfleet apply
```

`auto` 会识别当前 Docker context / `DOCKER_HOST`，覆盖 Colima、Docker Desktop、Linux Docker、rootless Docker 的常见 Unix socket。Colima / Docker Desktop 下建议使用 `auto`。如果需要手动指定，应填写 Docker daemon 所在环境可见的 socket 路径，通常是：

```bash
coderfleet config set DOCKER_SOCKET /var/run/docker.sock
coderfleet config set DOCKER_SOCKET_TARGET /var/run/docker.sock
coderfleet apply
```

不要把 `/Users/<you>/.colima/default/docker.sock` 当作容器挂载源；那是 macOS 侧 Docker CLI 连接 Colima 的客户端 socket，不是 Colima VM 内 Docker daemon 的 bind mount 源路径。

开启后项目容器内会得到 `DOCKER_HOST=unix:///var/run/docker.sock`，并注入 `CODERFLEET_HOST_WORKSPACE` 指向宿主机可见的项目路径。二级容器挂载项目目录时优先使用这个变量，例如：

```bash
docker run --rm -v "$CODERFLEET_HOST_WORKSPACE:/workspace" -w /workspace alpine pwd
```

挂载 Docker socket 等价于让项目容器拥有宿主机 Docker 控制权，只给可信镜像开启。Web 界面也可在「系统设置 → Docker」中配置，保存后执行 `coderfleet apply` 生效。

### Telegram 通知

任务结束后向 Telegram 播报结果，并支持在 Telegram 里回复播报消息续聊任务链：

```bash
coderfleet config set TELEGRAM_BOT_TOKEN <向 @BotFather 申请的 token>
coderfleet config set TELEGRAM_CHAT_ID <向 @userinfobot 发消息获取>
coderfleet config set TELEGRAM_PROXY http://127.0.0.1:7890   # 国内环境必填
coderfleet config set TELEGRAM_NOTIFY_MODE text              # off | text | voice
coderfleet telegram test    # 发送测试消息验证连通性
```

改动即时生效，无需 `coderfleet apply`。Web 界面在「系统设置 → Telegram 通知」中配置。

`voice` 模式会先用系统级 LLM 把结果压成口语摘要，再经 edge-tts 合成语音播报，
**需要宿主机安装 ffmpeg**（`brew install ffmpeg` / `apt install ffmpeg`），
且已配置 SYSTEM_LLM_*。任一环节不可用时自动降级为文本播报。

配置 `TELEGRAM_ASR_API_KEY` / `TELEGRAM_ASR_BASE_URL` / `TELEGRAM_ASR_MODEL`
（OpenAI 兼容 `/audio/transcriptions` 接口，如 OpenAI whisper-1）后，
还可以直接在 Telegram 里发语音下发指令。

**Topics 分频道（多项目推荐）**：想让每个项目有独立的消息频道，可绑定一个
论坛型群组——每个项目自动对应一个话题（topic），播报进项目话题、话题内
说话自动路由到该项目，且每个话题可独立静音：

1. 新建 Telegram 群 → 群设置开启「话题（Topics）」；
2. 把 bot 拉进群并设为管理员，勾选「管理话题」权限；
3. 在群里发送 `/id` 获取群 id（形如 `-100…`）；
4. `coderfleet config set TELEGRAM_TOPIC_GROUP_ID <群id>`。

配置后播报只发话题群（不再发私聊白名单）；项目话题在首次播报时自动创建。
在项目话题里 `/new <指令>` 免项目参数、`/chats` 只列本项目会话；General
和私聊保持全局语义。不配置该项则一切保持私聊模式。

## GitHub Webhook

设置用于 PR checks Continuation 的 webhook secret：

```bash
coderfleet config set GITHUB_WEBHOOK_SECRET '<高熵随机字符串>'
```

在 GitHub 仓库或组织中创建 webhook，Payload URL 指向
`https://<coderfleet-host>/api/integrations/github/webhook`。Content type 选择
`application/json`，Secret 使用相同值，并订阅 `check_suite` 与
`workflow_run` 事件。触发时会同时匹配 repository、PR number 和 head SHA。

## accounts.conf

```conf
NAME=alice TYPE=codex
NAME=bob TYPE=claude
NAME=carol TYPE=opencode
NAME=dave TYPE=hermes
NAME=eve TYPE=grok AUTH=env
NAME=frank TYPE=kimi
NAME=claude-api TYPE=claude AUTH=env ENV_FILE=./accounts/claude-api/env
NAME=hermes-api TYPE=hermes AUTH=env ENV_FILE=./accounts/hermes-api/env
NAME=kimi-api TYPE=kimi AUTH=env ENV_FILE=./accounts/kimi-api/env
NAME=local TYPE=claude PROXY=off
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 账号名 |
| `TYPE` | 是 | `codex`、`claude`、`opencode`、`hermes`、`grok` 或 `kimi` |
| `AUTH` | 否 | `login` 或 `env`。`env` 适用于 `claude`、`opencode`、`hermes`、`grok` 和 `kimi`；`grok` 仅支持 `env` |
| `ENV_FILE` | 否 | env 文件路径，默认 `./accounts/<账号名>/env` |
| `PROXY` | 否 | `relay` 或 `off` |

各类型注入的容器环境变量：

| 类型 | 自动注入变量 |
| --- | --- |
| `hermes` | `HERMES_HOME=/home/byclaw/.hermes` |
| `grok` | `GROK_HOME=/home/byclaw/.grok`、`GROK_NO_AUTO_UPDATE=1` |
| `kimi` | `KIMI_CODE_HOME=/home/byclaw/.kimi-code`、`KIMI_CODE_NO_AUTO_UPDATE=1`、`KIMI_DISABLE_TELEMETRY=1` |

Kimi Code 使用 `AUTH=env` 时需要配置 `KIMI_MODEL_*`：

```env
KIMI_MODEL_NAME=kimi-for-coding
KIMI_MODEL_API_KEY=sk-...
KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1
```

## projects.conf

```conf
NAME=my-app ACCOUNT=alice PATH=~/projects/my-app
NAME=api-server ACCOUNT=bob PATH=~/projects/api-server
NAME=grok-app ACCOUNT=eve PATH=~/projects/grok-app
NAME=docker-app ACCOUNT=alice PATH=~/projects/docker-app DOCKER_SOCKET=auto
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 项目名 |
| `ACCOUNT` | 是 | 绑定账号 |
| `PATH` | 是 | 宿主机项目路径 |
| `DOCKER_SOCKET` | 否 | 项目级 Docker socket 覆盖；留空继承全局，`off` 关闭，`auto` 自动识别，也可填绝对路径 |

命令行配置项目级 Docker socket：

```bash
coderfleet project add docker-app alice ~/projects/docker-app --docker-socket auto
coderfleet project set-docker-socket docker-app off
coderfleet project set-docker-socket docker-app -   # 清除覆盖，继承全局配置
coderfleet apply
```
