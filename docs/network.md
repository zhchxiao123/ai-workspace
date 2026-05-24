# 网络与代理

CoderFleet 的默认网络模型是：账号容器不直接访问公网，而是通过内部网络连接到代理中继，再由代理中继访问宿主机代理。

```text
[codex-alice]  ---+
[claude-bob]   ---+--> internal docker network
[opencode-c]   ---+          |
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

## 检查代理

```bash
coderfleet check-proxy
```

正常情况下：

- 容器可以访问 proxy relay
- 容器直连公网应被阻断
- 代理出口 IP 与宿主机代理一致

## 关闭代理

适合内网服务、本地模型或不需要代理的账号：

```bash
coderfleet account add local TYPE=claude --proxy off
```
