# 平台网络入口

`envoy-values.yaml` 配置 Envoy Gateway 控制器；`production/` 创建两副本 Envoy 数据面、
LoadBalancer Gateway 和 Argo CD/Grafana HTTPRoute。GitLab chart 复用同一 Gateway。
前提是集群具备实际的 LoadBalancer 实现；裸机环境需先供应对应负载均衡，不会自动得到公网IP。

替换 route hostnames，准备 `gateway-system/platform-public-tls`，其 SAN 覆盖要发布的域名。
GatewayClass 为 `platform-envoy`，Gateway 为 `gateway-system/platform`，HTTPS listener 为 `https`。
只允许带 `platform.example.com/gateway-access=true` 标签的 namespace 接入。

证书可以来自企业PKI或cert-manager；Cloudflare DNS01只是可选示例。生产开放入口前验证
DNS、完整证书链、Gateway Programmed 与 Route Accepted/ResolvedRefs，以及实际登录/请求。
数据面副本数不保证跨节点调度，需按故障域补充适合目标集群的调度策略并做节点失效测试。

Argo CD 后端在私有集群网络使用HTTP，配有入口 NetworkPolicy；需要CNI实际执行策略。
端到端TLS或服务网格环境应改为受验证的后端TLS。Kibana保留ECK内部TLS，不直接套用HTTP路由。
Prometheus/Alertmanager管理API保持集群内部访问。完整步骤见 `docs/production.md`。
