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
NAME=claude-api TYPE=claude AUTH=env ENV_FILE=./accounts/claude-api/env
NAME=hermes-api TYPE=hermes AUTH=env ENV_FILE=./accounts/hermes-api/env
NAME=local TYPE=claude PROXY=off
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 账号名 |
| `TYPE` | 是 | `codex`、`claude`、`opencode` 或 `hermes` |
| `AUTH` | 否 | `login` 或 `env`。`env` 适用于 `claude`、`opencode` 和 `hermes` |
| `ENV_FILE` | 否 | env 文件路径，默认可放在 `./accounts/<账号名>/env` |
| `PROXY` | 否 | `relay` 或 `off` |

Hermes Agent 会额外设置 `HERMES_HOME=/home/byclaw/.hermes`。如果使用 `AUTH=env`，需要在 env 文件中配置所选 provider 的 API key，并通过 `hermes config set model.provider <provider>` 完成 provider 初始化。

## projects.conf

```conf
NAME=my-app ACCOUNT=alice PATH=~/projects/my-app
NAME=api-server ACCOUNT=bob PATH=~/projects/api-server
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 项目名 |
| `ACCOUNT` | 是 | 绑定账号 |
| `PATH` | 是 | 宿主机项目路径 |
