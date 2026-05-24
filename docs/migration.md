# 从 aicm 迁移到 CoderFleet

本项目已更名为 CoderFleet，并从仓库内的 `./aicm.sh` 脚本切换为标准 Python CLI 命令 `coderfleet`。

旧的 `./aicm.sh` 包装脚本不再保留，后续所有操作都使用 `coderfleet`。

## 安装新命令

```bash
uv tool install coderfleet
```

开发环境：

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

## 命令替换

| 旧命令 | 新命令 |
| --- | --- |
| `./aicm.sh init` | `coderfleet init` |
| `./aicm.sh build` | `coderfleet build` |
| `./aicm.sh apply` | `coderfleet apply` |
| `./aicm.sh up` | `coderfleet up` |
| `./aicm.sh down` | `coderfleet down` |
| `./aicm.sh restart` | `coderfleet restart` |
| `./aicm.sh status` | `coderfleet status` |
| `./aicm.sh logs [项目]` | `coderfleet logs [项目]` |
| `./aicm.sh enter <项目>` | `coderfleet enter <项目>` |
| `./aicm.sh login <账号\|all>` | `coderfleet login <账号\|all>` |
| `./aicm.sh check-proxy` | `coderfleet check-proxy` |
| `./aicm.sh server` | `coderfleet server` |
| `./aicm.sh account list` | `coderfleet account list` |
| `./aicm.sh project list` | `coderfleet project list` |
| `./aicm.sh task run "<prompt>"` | `coderfleet task run "<prompt>"` |
| `./aicm.sh task logs <id> -f` | `coderfleet task logs <id> -f` |

## 工作区迁移

`coderfleet` 默认使用：

```text
~/.coderfleet
```

如果继续使用旧工作区：

```bash
export CODERFLEET_WORKSPACE=/path/to/old/repo
coderfleet status
```

迁移到标准工作区：

```bash
mkdir -p ~/.coderfleet
cp ./config.conf ~/.coderfleet/
cp ./accounts.conf ~/.coderfleet/
cp ./projects.conf ~/.coderfleet/
cp ./Dockerfile ~/.coderfleet/
cp ./entrypoint.sh ~/.coderfleet/
cp -r ./scripts ~/.coderfleet/scripts
cp -r ./accounts ~/.coderfleet/accounts
```

任务记录和任务链记录可按需复制：

```bash
cp -r ./tasks ~/.coderfleet/tasks
cp -r ./conversations ~/.coderfleet/conversations
```

迁移后重新生成编排文件：

```bash
coderfleet apply
```

## 环境变量变化

| 旧环境变量 | 新环境变量 |
| --- | --- |
| `AICM_WORKSPACE` | `CODERFLEET_WORKSPACE` |
| `AICM_PORT` | `CODERFLEET_PORT` |
| `AICM_API` | `CODERFLEET_API` |
