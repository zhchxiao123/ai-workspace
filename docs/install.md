# 安装

## 环境要求

- Docker Desktop macOS / Linux，或 Docker Engine + Compose V2
- Python 3.10+
- 推荐使用 uv 管理 Python 工具
- 如果需要访问 Claude / OpenAI 服务，宿主机应准备可用代理，并开启局域网访问能力

## 使用 uv tool 安装

```bash
uv tool install coderfleet
```

安装后确认命令可用：

```bash
coderfleet --help
```

升级：

```bash
uv tool upgrade coderfleet
```

## 使用 pipx 安装

```bash
pipx install coderfleet
```

## 从源码安装

适合参与开发或测试本地改动：

```bash
git clone https://github.com/zhchxiao123/coderfleet.git
cd coderfleet
uv venv
source .venv/bin/activate
uv pip install -e .
```

## 初始化后的位置

`coderfleet init` 默认创建：

```text
~/.coderfleet/
```

如需指定工作区：

```bash
export CODERFLEET_WORKSPACE=/path/to/workspace
```
