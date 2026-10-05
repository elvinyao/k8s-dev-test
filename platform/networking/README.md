# 平台网络入口

`envoy-values.yaml` 配置 Envoy Gateway 控制器；`production/` 创建两副本 Envoy 数据面、
LoadBalancer Gateway 和 Argo CD/Grafana HTTPRoute。GitLab chart 复用同一 Gateway。
前提是集群具备实际的 LoadBalancer 实现；裸机环境需先供应对应负载均衡，不会自动得到公网IP。

替换 route hostnames，准备 `gateway-system/platform-public-tls`，其 SAN 覆盖要发布的域名。
GatewayClass 为 `platform-envoy`，Gateway 为 `gateway-system/platform`，HTTPS listener 为 `https`。
只允许带 `platform.example.com/gateway-access=true` 标签的 namespace 接入。

证书可以来自企业PKI或cert-manager；Cloudflare DNS01只是可选示例。生产开放入口前验证
DNS、完整证书链、Gateway Programmed 与 Route Accepted/ResolvedRefs，以及实际登录/请求。
数据面的 `EnvoyProxy` 使用 hostname 硬反亲和，两个副本必须分布在不同节点；
`envoyPDB.minAvailable: 1` 要求自愿驱逐保留至少一个 Ready 副本。
滚动升级设置 `maxUnavailable: 0`、`maxSurge: 1`，因此需要第三个满足调度条件且有余量的节点；
只有两个可用节点时，升级会等待新副本能够调度。先恢复容量，不要为绕过等待而删除旧副本。
PDB 限制 eviction API 发起的自愿中断，不能阻止节点突然失联、直接删除 Pod 或替代 Deployment 的升级策略。
hostname 隔离也不保证跨可用区；多可用区生产环境应增加针对实际 zone 标签的分布策略并验证剩余容量。
控制器的副本/PDB 在 `envoy-values.yaml` 中独立配置，不能替代这里的数据面配置。

Argo CD 后端在私有集群网络使用HTTP，配有入口 NetworkPolicy；需要CNI实际执行策略。
端到端TLS或服务网格环境应改为受验证的后端TLS。Kibana保留ECK内部TLS，不直接套用HTTP路由。
Prometheus/Alertmanager管理API保持集群内部访问。完整步骤见 `docs/production.md`。

## 入口维护演练

在隔离预生产环境执行。下面以 `/srv/platform/kubeconfig` 和 context `production` 为例，
先替换为目标环境；所有命令均通过 Docker runner。演练前确认至少三个可调度节点、
两份 Ready 数据面副本，以及集群中其他工作负载允许计划内维护。

```sh
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get pods -l platform.example.com/gateway=platform -o wide
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get pdb platform-envoy -o yaml
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get deployments -l gateway.envoyproxy.io/owning-gateway-name=platform,gateway.envoyproxy.io/owning-gateway-namespace=gateway-system -o yaml
```

验证 Pod 所在节点不同，PDB 的 selector 确实选中这两个数据面 Pod，
`currentHealthy`、`desiredHealthy`、`disruptionsAllowed` 分别为 `2`、`1`、`1`。
Deployment 应含上述反亲和与滚动升级参数。控制器生成实际 Deployment/PDB 后才能验证这些条件；
仅 CRD schema 校验不能证明 controller 已正确实现配置。

从集群外持续请求带有效 TLS 的实际业务域名，记录开始/结束时间、请求总数、错误数及延迟。
同时覆盖长连接和登录会话；仅请求健康端点不足以证明业务连续性。
选择其中一个数据面所在节点，替换下面的 `REPLACE_WITH_ENVOY_NODE`，每次只维护一个节点：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production drain REPLACE_WITH_ENVOY_NODE --ignore-daemonsets --timeout=10m
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get pods -l platform.example.com/gateway=platform -o wide
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get pdb platform-envoy -o yaml
```

`drain` 会影响所选节点的全部可驱逐工作负载。若受 PDB、本地临时数据或无法管理的 Pod 阻挡，
停止并排查对应负载，不添加强制删除或跳过 eviction 的参数。
确认剩余副本持续承接入口流量，新副本在第三个节点恢复 Ready，
LoadBalancer 健康检查及时摘除旧节点，并保存实际请求日志与观测到的中断时间。
本配置保留 Envoy Service 的默认 `externalTrafficPolicy: Local`；负载均衡器必须正确处理节点上的本地端点变化。
维护结束后恢复节点调度，再确认两副本 Ready、Gateway `Programmed=True`、
HTTPRoute `Accepted=True`/`ResolvedRefs=True`，且上述状态的 `observedGeneration` 对应当前配置：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production uncordon REPLACE_WITH_ENVOY_NODE
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production -n gateway-system get gateway platform -o yaml
bash .agent/run.sh --kubeconfig /srv/platform/kubeconfig kubectl --context production get httproute -A -o yaml
```

再单独演练数据面升级和非自愿节点故障。成功 drain 只证明这一种维护路径，
不能替代负载均衡器失效、可用区中断、控制器故障或后端服务故障测试。
本仓库对上述字段仅完成固定版本 CRD 的离线校验，尚未执行目标集群维护演练。

字段以仓库锁定的 Envoy Gateway v1.9.1 chart CRD 为准；参考
[EnvoyProxy API](https://gateway.envoyproxy.io/v1.9/api/extension_types/)
和 [EnvoyProxy 自定义方式](https://gateway.envoyproxy.io/v1.9/tasks/operations/customize-envoyproxy/)。
