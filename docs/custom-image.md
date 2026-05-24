# 自定义镜像

CoderFleet 会在工作区生成 Dockerfile。默认位置：

```text
~/.coderfleet/Dockerfile
```

如果需要增加项目依赖或开发工具，可以编辑该文件。

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
- 修改 Dockerfile 后需要重新构建镜像。
- 如果账号容器已经启动，构建后执行 `coderfleet restart`。
