# 从 `./aicm.sh` 迁移到 `aicm` Python CLI

## 背景

`aicm.sh` 原本是一个 1300 行的 Shell 脚本，承担了所有功能。新版本将其重构为标准 Python 包（`uv tool install aicm` / `pipx install aicm`），`aicm.sh` 保留为薄包装层以维持向后兼容。

---

## 升级步骤

### 前置：安装 uv（推荐）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # 或重新打开终端
uv python install 3.12
```

### 已有仓库用户（git clone 方式）

在仓库目录中执行一次：

```bash
git pull

# 推荐：用 uv 创建隔离环境
uv venv && source .venv/bin/activate
uv pip install -e .

# 或直接用系统 pip
pip install -e . --break-system-packages
```

完成后，`./aicm.sh` 原有用法**一行不用改**，同时获得新的 `aicm` 命令。

### 新用户（全新安装）

```bash
uv tool install aicm    # 推荐，隔离环境，等价于 pipx install aicm
aicm init               # 交互式创建 ~/.aicm/ 工作区
aicm build
```

---

## 命令对照表

所有命令在功能上完全等价，仅前缀从 `./aicm.sh` 改为 `aicm`。

| 老命令 | 新命令 |
|--------|--------|
| `./aicm.sh build` | `aicm build` |
| `./aicm.sh apply` | `aicm apply` |
| `./aicm.sh up` | `aicm up` |
| `./aicm.sh down` | `aicm down` |
| `./aicm.sh restart` | `aicm restart` |
| `./aicm.sh status` | `aicm status` |
| `./aicm.sh logs [项目]` | `aicm logs [项目]` |
| `./aicm.sh enter <项目>` | `aicm enter <项目>` |
| `./aicm.sh login <账号\|all>` | `aicm login <账号\|all>` |
| `./aicm.sh check-proxy` | `aicm check-proxy` |
| `./aicm.sh server` | `aicm server` |
| `./aicm.sh account add <名称> TYPE=claude` | `aicm account add <名称> TYPE=claude` |
| `./aicm.sh account add <名称> TYPE=claude AUTH=env` | `aicm account add <名称> TYPE=claude --auth env` |
| `./aicm.sh account add <名称> TYPE=claude PROXY=off` | `aicm account add <名称> TYPE=claude --proxy off` |
| `./aicm.sh account remove <名称>` | `aicm account remove <名称>` |
| `./aicm.sh account list` | `aicm account list` |
| `./aicm.sh project add <名称> <账号> <路径>` | `aicm project add <名称> <账号> <路径>` |
| `./aicm.sh project remove <名称>` | `aicm project remove <名称>` |
| `./aicm.sh project list` | `aicm project list` |
| `./aicm.sh task run "<prompt>"` | `aicm task run "<prompt>"` |
| `./aicm.sh task list` | `aicm task list` |
| `./aicm.sh task logs <id> -f` | `aicm task logs <id> -f` |
| `./aicm.sh task kill <id>` | `aicm task kill <id>` |
| `./aicm.sh task clean` | `aicm task clean` |

### account add 参数变化

`AUTH=`、`PROXY=` 等从老版本的位置参数改为了标准 CLI 选项，两种写法对比：

```bash
# 老版本
./aicm.sh account add myacc TYPE=claude AUTH=env PROXY=off

# 新版本
aicm account add myacc TYPE=claude --auth env --proxy off

# TYPE= 前缀仍然支持，也可以省略
aicm account add myacc claude --auth env --proxy off
```

---

## 工作区说明

### 继续使用仓库目录作为工作区（无需迁移）

`./aicm.sh` 的薄包装会自动将工作区设为脚本所在目录：

```bash
export AICM_WORKSPACE="${AICM_WORKSPACE:-$SCRIPT_DIR}"
```

所以 `accounts.conf`、`projects.conf`、`config.conf`、`accounts/` 等文件**原地使用**，无需任何迁移。

### 可选：迁移到 `~/.aicm/`（标准工作区）

如果希望与 `pipx install aicm` 的默认路径对齐，把以下文件复制到 `~/.aicm/`：

```bash
mkdir -p ~/.aicm

# 配置文件
cp config.conf    ~/.aicm/
cp accounts.conf  ~/.aicm/
cp projects.conf  ~/.aicm/

# 运行时文件
cp Dockerfile     ~/.aicm/
cp entrypoint.sh  ~/.aicm/
cp -r scripts/    ~/.aicm/scripts/

# 认证数据（最重要，登录态在这里）
cp -r accounts/   ~/.aicm/accounts/
```

迁移后，`docker-compose.yml` 会在首次 `aicm apply` 时自动重新生成，`tasks/` 和 `conversations/` 目录由 server 自动创建，无需手动复制。

切换到新工作区后，不再需要 `./aicm.sh`，直接用 `aicm` 命令即可（工作区默认读 `~/.aicm/`）。

---

## 常见问题

**Q: 合并后 `./aicm.sh` 报 "Error: 'aicm' CLI not found."**

未安装 Python 包。在仓库目录执行：

```bash
pip install -e . --break-system-packages
```

**Q: `aicm` 命令找不到（command not found）**

安装路径不在 `PATH` 里。将以下行加入 `~/.bashrc` 或 `~/.zshrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

或直接用 `python3 -m aicm <子命令>`，无需配置 PATH。

**Q: 已有的容器、认证数据会受影响吗？**

不会。容器由 Docker 管理，与 CLI 工具无关。`accounts/` 目录和 `.conf` 文件格式完全不变，升级后无需重新登录。
