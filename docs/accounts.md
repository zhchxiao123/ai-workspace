# 账号管理

## 添加账号

```bash
coderfleet account add alice TYPE=codex
coderfleet account add bob TYPE=claude
coderfleet account add carol TYPE=opencode
```

账号名只允许字母、数字和连字符。

## 查看账号

```bash
coderfleet account list
```

## 删除账号

```bash
coderfleet account remove alice
```

删除账号只会移除配置，认证目录仍会保留在工作区中。如需彻底清理，需要手动删除对应目录。

## 登录账号

```bash
coderfleet login alice
```

登录所有需要网页登录的账号：

```bash
coderfleet login all
```

## API Key 认证

Claude Code / OpenCode 可以通过 env 文件注入 API key：

```bash
coderfleet account add claude-api TYPE=claude --auth env
coderfleet account add opencode-api TYPE=opencode --auth env
```

然后编辑：

```text
~/.coderfleet/accounts/<账号名>/env
```

示例：

```env
ANTHROPIC_API_KEY=sk-ant-...
```

## 代理模式

默认账号使用代理中继：

```bash
coderfleet account add alice TYPE=codex
```

如果某个账号或本地模型不需要代理：

```bash
coderfleet account add local TYPE=claude --proxy off
```
