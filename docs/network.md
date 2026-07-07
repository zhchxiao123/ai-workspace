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
