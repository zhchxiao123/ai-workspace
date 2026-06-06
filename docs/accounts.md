# 账号管理

## 添加账号

```bash
coderfleet account add alice TYPE=codex
coderfleet account add bob TYPE=claude
coderfleet account add carol TYPE=opencode
coderfleet account add dave TYPE=hermes
coderfleet account add eve TYPE=grok --auth env
coderfleet account add frank TYPE=kimi
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

登录命令会根据账号类型执行对应的 CLI 认证流程：

| 类型 | 容器内命令 | 认证数据目录 |
| --- | --- | --- |
| `codex` | `codex login --device-auth` | `/home/byclaw/.codex` |
| `claude` | `claude login` | `/home/byclaw/.claude` |
| `opencode` | `opencode auth login` | `/home/byclaw/.opencode` |
| `hermes` | `hermes setup` | `/home/byclaw/.hermes` |
| `grok` | 仅支持 `AUTH=env`，无需登录 | `/home/byclaw/.grok` |
| `kimi` | `kimi login` | `/home/byclaw/.kimi-code` |

登录所有需要网页登录的账号（`AUTH=env` 账号自动跳过）：

```bash
coderfleet login all
```

## API Key 认证

Claude Code、OpenCode、Hermes Agent、Grok Build 和 Kimi Code 可以通过 env 文件注入 API key：

```bash
coderfleet account add claude-api TYPE=claude --auth env
coderfleet account add opencode-api TYPE=opencode --auth env
coderfleet account add hermes-api TYPE=hermes --auth env
coderfleet account add grok-api TYPE=grok --auth env
coderfleet account add kimi-api TYPE=kimi --auth env
```

然后编辑：

```text
~/.coderfleet/accounts/<账号名>/env
```

各类型示例：

```env
# Claude Code
ANTHROPIC_API_KEY=sk-ant-...

# Grok Build
XAI_API_KEY=xai-...

# Kimi Code
KIMI_MODEL_NAME=kimi-for-coding
KIMI_MODEL_API_KEY=sk-...
KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1
```

Hermes Agent 需要根据实际 provider 配置对应 API key，例如 `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`。添加 `TYPE=hermes --auth env` 后，还需要在容器里完成 Hermes provider 初始化：

```bash
coderfleet enter <绑定该账号的项目名>
hermes config set model.provider anthropic
```

CoderFleet 会在 Hermes 容器启动时设置 `HERMES_HOME=/home/byclaw/.hermes`，并关闭交互式审批提示，便于后台任务执行。

## Grok Build

Grok Build 只支持 `AUTH=env` 认证，不支持交互式登录：

```bash
coderfleet account add my-grok TYPE=grok --auth env
coderfleet project add my-project my-grok ~/projects/my-project
coderfleet apply
```

在 `~/.coderfleet/accounts/my-grok/env` 中配置：

```env
XAI_API_KEY=xai-...
```

API Key 在 [console.x.ai](https://console.x.ai/) 获取。CoderFleet 会在 Grok 容器启动时设置 `GROK_HOME=/home/byclaw/.grok` 和 `GROK_NO_AUTO_UPDATE=1`。

## Kimi Code

Kimi Code 支持 `login` 和 `env` 两种认证方式：

```bash
coderfleet account add my-kimi TYPE=kimi
coderfleet login my-kimi
```

如果使用 `AUTH=env`，需要配置 `KIMI_MODEL_*`。新版 Kimi Code 不会直接从 shell 环境读取 `KIMI_API_KEY`：

```bash
coderfleet account add my-kimi-api TYPE=kimi --auth env
```

在 `~/.coderfleet/accounts/my-kimi-api/env` 中配置：

```env
KIMI_MODEL_NAME=kimi-for-coding
KIMI_MODEL_API_KEY=sk-...
KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1
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
