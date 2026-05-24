# 异步任务

异步任务需要先启动调度服务：

```bash
coderfleet server
```

## 提交任务

```bash
coderfleet task run "在 auth.py 中添加 JWT 生成逻辑" --project app-a
```

开启全自动模式：

```bash
coderfleet task run "运行并修复所有 lint 错误" --project app-a --auto
```

## 任务链

开启新的任务链：

```bash
coderfleet task run "开始实现支付接口" --project app-a --new-chain 支付功能
```

续接已有任务链：

```bash
coderfleet task run "增加退款支持" --conversation <任务链ID>
```

## 查看任务

```bash
coderfleet task list
coderfleet task status <任务ID>
```

## 查看日志

```bash
coderfleet task logs <任务ID>
coderfleet task logs <任务ID> -f
```

## 终止任务

```bash
coderfleet task kill <任务ID>
```

## 清理历史

默认保留最近 30 条：

```bash
coderfleet task clean
```

指定保留数量：

```bash
coderfleet task clean 100
```
