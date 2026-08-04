# 网络与代理

CoderFleet 的默认网络模型是：账号容器不直接访问公网，而是通过内部网络连接到代理中继，再由代理中继访问宿主机代理。

```text
[codex-alice]  ---+
[claude-bob]   ---+--> internal docker network
[opencode-c]   ---+          |
[hermes-dave]  ---+
                              v
                       coderfleet-proxy-relay
                              |
                              v
                         host proxy
                              |
                              v
                           internet
```

## 关键配置

`config.conf` 中的代理相关字段：

```conf
PROXY_HOST=host.docker.internal
PROXY_HTTP_PORT=7890
PROXY_SOCKS5_PORT=7891
INTERNAL_SUBNET=172.21.0.0/16
RELAY_IP=172.21.0.2
RELAY_LISTEN_PORT=7890
RELAY_IMAGE=gogost/gost:3
```

## DNS 解析

`intnet` 是 `internal: true` 的网络，没有到公网的路由，容器自身没法直接发出 DNS 查询。为此 relay（gost）除了转发 HTTP(S) 之外，还内置了一个 DNS 服务：容器的 `dns:` 字段指向 `RELAY_IP`，relay 收到 DNS 查询后走同一条 chain（转发给宿主机代理/Xray）把查询转成 DNS-over-HTTPS 请求发给上游解析器，再把结果返回给容器。

```conf
DNS_LISTEN_PORT=53
DNS_UPSTREAM=https://1.1.1.1/dns-query
```

这只覆盖走系统 resolver（`/etc/resolv.conf`）的标准查询——git / npm / pip / curl / claude / codex 等常见工具都走这条路径。如果某个工具硬编码了 DNS 服务器地址或用非标准协议查询，仍然会解析失败。

## 检查代理

```bash
coderfleet check-proxy
```

正常情况下：

- 容器可以访问 proxy relay
- 容器直连公网应被阻断
- DNS 解析（经 relay 转发）应全部成功
- 代理出口 IP 与宿主机代理一致

## 关闭代理

适合内网服务、本地模型或不需要代理的账号：

```bash
coderfleet account add local TYPE=claude --proxy off
```

## `--runtime local` 账号的代理支持范围

以上整套 relay 模型（`internal` docker 网络 + gost 中继）是靠 Docker 网络层强制的：账号容器物理上无法绕开 relay 直连公网。`--runtime local` 账号不跑在容器里，这层网络层强制天然不存在——是否真的走了代理，完全取决于目标 CLI 自己是否尊重 `HTTPS_PROXY`/`ALL_PROXY` 等环境变量。

- **`TYPE=claude`**：官方文档确认标准代理环境变量对纯终端子进程调用生效，`--runtime local --proxy relay` 受支持，CoderFleet 会把与 container 场景相同的 `HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY` 注入到子进程环境变量里。
- **`TYPE=codex`**：Codex 自己的官方文档没有确认它的所有 HTTP client 都会尊重代理环境变量，上游有一个仍然开放的 issue（[`openai/codex#4242`](https://github.com/openai/codex/issues/4242)）把"让所有 HTTP client 尊重 HTTPS_PROXY"描述成待实现的功能，而不是既有行为。CoderFleet 因此直接拒绝 `TYPE=codex --runtime local --proxy relay` 这个组合（`coderfleet account add`/`account edit` 都会报错），避免用户误以为流量真的经过了 relay，实际却从宿主机直连出去。Codex 的 local 账号只能用 `--proxy off`，或改用 `--runtime container`。

完整调研依据（官方文档原文引用、CLI `--help` 输出）见 `docs/research/local-execution-mode.md`。
