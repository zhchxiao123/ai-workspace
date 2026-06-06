# 配置文件

配置文件位于 CoderFleet 工作区，默认是 `~/.coderfleet/`。

## config.conf

```conf
IMAGE_NAME=coderfleet
IMAGE_TAG=latest
BUILD_PLATFORM=linux/amd64

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
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 项目名 |
| `ACCOUNT` | 是 | 绑定账号 |
| `PATH` | 是 | 宿主机项目路径 |
