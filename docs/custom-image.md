# 自定义镜像

CoderFleet 的默认镜像定义随包提供，`coderfleet build` 会使用它构建本地工作镜像。镜像内预装 Codex CLI、Claude Code、OpenCode 和 Hermes Agent。

Hermes Agent 安装在独立虚拟环境：

```text
/opt/hermes-venv
```

并通过 `/usr/local/bin/hermes` 暴露命令。镜像还包含 `ripgrep` 以及常见 LLM provider SDK，用于支持 Hermes 的代码检索和 provider 调用。

如果需要增加项目依赖或开发工具，可以在自定义镜像流程中追加系统包或语言工具，然后重新构建。

## 添加系统包

```dockerfile
RUN apt-get update && apt-get install -y openjdk-21-jdk
```

## 添加 Node.js 工具

```dockerfile
RUN npm install -g pnpm
```

## 重新构建

```bash
coderfleet build
coderfleet restart
```

## 注意事项

- 尽量把通用工具放进镜像，把项目专用依赖留在项目目录。
- 修改镜像定义后需要重新构建镜像。
- 如果账号容器已经启动，构建后执行 `coderfleet restart`。
