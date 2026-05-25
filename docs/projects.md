# 项目管理

## 添加项目

```bash
coderfleet project add app-a alice ~/projects/app-a
```

参数含义：

| 参数 | 说明 |
| --- | --- |
| `app-a` | CoderFleet 内部项目名 |
| `alice` | 绑定的账号名 |
| `~/projects/app-a` | 宿主机项目目录 |

## 查看项目

```bash
coderfleet project list
```

## 删除项目

```bash
coderfleet project remove app-a
```

## 进入项目容器

```bash
coderfleet enter app-a
```

进入后可以直接使用对应 CLI：

```bash
codex "帮我实现用户认证模块"
claude "帮我重构这个函数"
opencode run "帮我修复测试"
hermes chat -q "帮我分析这个项目"
```

## 项目与任务

提交异步任务时可以指定项目：

```bash
coderfleet task run "修复 lint 错误" --project app-a --auto
```

调度器会优先使用该项目绑定的账号执行任务。
