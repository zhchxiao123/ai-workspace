# 从 `./aicm.sh` 迁移到 `coderfleet`

本项目已更名为 **CoderFleet**，并从仓库内的 `./aicm.sh` 脚本切换为标准 Python CLI 命令 `coderfleet`。

旧的 `./aicm.sh` 包装脚本不再保留，后续所有操作都使用 `coderfleet`。

## 安装新命令

推荐使用 uv tool：

```bash
uv tool install coderfleet
```

开发环境中可以在仓库根目录安装：

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

安装后使用：

```bash
coderfleet --help
```

## 命令替换

| 旧命令 | 新命令 |
|--------|--------|
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
| `./aicm.sh account add <名称> TYPE=claude AUTH=env` | `coderfleet account add <名称> TYPE=claude --auth env` |
| `./aicm.sh account add <名称> TYPE=claude PROXY=off` | `coderfleet account add <名称> TYPE=claude --proxy off` |
| `./aicm.sh account remove <名称>` | `coderfleet account remove <名称>` |
| `./aicm.sh account list` | `coderfleet account list` |
| `./aicm.sh project add <名称> <账号> <路径>` | `coderfleet project add <名称> <账号> <路径>` |
| `./aicm.sh project remove <名称>` | `coderfleet project remove <名称>` |
| `./aicm.sh project list` | `coderfleet project list` |
| `./aicm.sh task run "<prompt>"` | `coderfleet task run "<prompt>"` |
| `./aicm.sh task list` | `coderfleet task list` |
| `./aicm.sh task logs <id> -f` | `coderfleet task logs <id> -f` |
| `./aicm.sh task kill <id>` | `coderfleet task kill <id>` |
| `./aicm.sh task clean` | `coderfleet task clean` |

## 工作区迁移

`./aicm.sh` 过去会默认把脚本所在仓库目录当作工作区；`coderfleet` 默认使用：

```bash
~/.coderfleet
```

如果你希望继续使用原仓库目录里的 `config.conf`、`accounts.conf`、`projects.conf` 和 `accounts/`，可以显式指定工作区：

```bash
export CODERFLEET_WORKSPACE=/path/to/old/repo
coderfleet status
```

如果你希望迁移到标准工作区，可以复制旧工作区文件：

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
|------------|------------|
| `AICM_WORKSPACE` | `CODERFLEET_WORKSPACE` |
| `AICM_PORT` | `CODERFLEET_PORT` |
| `AICM_API` | `CODERFLEET_API` |

例如：

```bash
export CODERFLEET_WORKSPACE=/path/to/workspace
coderfleet status
```

## 运行时文件变化

新版本会使用以下名称：

- 容器代理：`coderfleet-proxy-relay`
- 临时登录容器：`coderfleet-login-<type>-<account>`
- 容器任务目录：`.coderfleet-tasks/`
- 上传目录：`.coderfleet-uploads/`
- 用量脚本：`coderfleet-usage-status`

旧的 `.aicm-tasks/`、`.aicm-uploads/` 和已生成的旧 `docker-compose.yml` 不会自动迁移。执行 `coderfleet apply` 后会生成新的 `docker-compose.yml`。

## 常见问题

**旧的 `./aicm.sh` 还可用吗？**

不可用。本项目不再提供 `./aicm.sh` 包装脚本。

**旧账号需要重新登录吗？**

通常不需要。只要把 `accounts/` 目录复制到新的工作区，Codex 和 Claude Code 的认证数据会继续挂载到容器内原路径。

**旧容器需要手动删除吗？**

建议在旧版本目录或旧工作区中先执行一次停止操作。如果旧命令已不可用，可用 Docker 手动清理旧容器和旧的 `aicm-proxy-relay`。
