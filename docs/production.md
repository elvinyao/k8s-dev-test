# 生产 Kubernetes 部署手册

本手册面向**已有多节点 Kubernetes**，使用固定版本的 Helm chart 与 GitOps 配置。
不把 kind 当作生产集群，也不自动创建云资源、数据库、DNS 或秘密。
Compose 独立部署见各组件目录；不要让两套 GitLab/数据库同时写同一份数据。

## 1. 环境合同

部署者先确认以下实际输入，并把非敏感配置提交到自己的环境仓库：

| 输入 | 本仓库约定 |
| --- | --- |
| 集群与身份 | Kubernetes 1.35 系列基线；明确 kubeconfig 与 context `production` |
| 节点 | 至少 3 个可调度 worker；生产控制平面由托管服务或独立 HA 方案保障 |
| 故障域 | worker 和有状态后端跨可接受的主机/可用区；当前硬反亲和只按 hostname |
| 存储 | 已验收 CSI，生产 values 中统一为 `production-rwo`，可按环境修改 |
| 网络 | CNI 真正执行 NetworkPolicy；LoadBalancer 实现；DNS；指标 API/Metrics Server |
| 域名 | 替换所有 `example.invalid`，同步修改 OIDC、证书与 HTTPRoute |
| Git | Argo CD 能独立访问的 Git 源、只读凭据、经过审核的 commit SHA |
| 秘密 | 从秘密管理系统或受控文件注入；不提交明文、base64 Secret 或 kubeconfig |
| GitLab 外部服务 | PostgreSQL、Redis、Gitaly、S3、SMTP；见组件手册 |
| 恢复 | 定义 RPO/RTO、独立备份位置和恢复演练环境 |

3 个 worker 只是调度前提，不能替代容量评估。ES 请求 12Gi、Logstash 请求 4Gi、GitLab
应用请求超过 10Gi，监控/Argo CD/系统开销另算。先按业务数据量测算，再逐组件开启。
云 CSI、容器镜像架构、内核参数、证书和插件需与实际集群版本匹配。

## 2. 校验配置与建立 namespace

按 [tooling.md](tooling.md) 构建工具箱，运行 Compose/YAML/Helm/Kustomize 校验。
在配置仓库中修改 `platform/*/values-production*`、入口 hostnames、域名和存储类。
`platform/releases.yaml` 是 chart/values 索引；不改变它的路径时，直接修改对应文件即可。

```sh
bash .agent/run.sh --toolbox python scripts/preflight-production.py
```

原始示例应因保留域名占位符而失败；这不是语法错误。此命令不会访问集群。
填写配置后，将 `/srv/platform/admin.kubeconfig` 和 `production` 替换为真实输入：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production get nodes
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -k clusters/production
```

供应已经验收的 CSI，检查 PVC 读写/重挂；`platform/storage/production` 中 EBS 示例仅适用于 AWS。
业务 namespace 的 ResourceQuota/NetworkPolicy 可在审核 CIDR、DNS 与应用流量后应用
`platform/security/apps-prod.yaml`。它默认拒绝应用流量，必须另加准确的应用允许规则；
NodeLocal DNS 的环境也需要对应 DNS 规则。

## 3. 供应秘密并安装 Argo CD

生产 Argo CD 关闭内置 admin，使用外部 OIDC。先修改 issuer、clientID、域名和 group
映射，并在 `argocd` namespace 供应 `argocd-secret`：

- `server.secretkey`：稳定的强随机 signing key；更换会影响会话。
- `oidc.platform.clientSecret`：IdP 实际 client secret。

Helm values 设置 `configs.secret.createSecret: false`，因此 Secret 由平台秘密流程管理。
从受控文件创建的示例（真实文件不进入 Git）：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig --host-dir /srv/platform/secrets kubectl --context production -n argocd create secret generic argocd-secret --from-file=server.secretkey=/srv/platform/secrets/argocd-server-key --from-file=oidc.platform.clientSecret=/srv/platform/secrets/argocd-oidc-client-secret
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig helm upgrade --install argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.9.2 --namespace argocd --kube-context production -f platform/argocd/values-production.yaml --wait --timeout 15m
```

不要同时应用 `bootstrap/argocd` 的原始清单，也不要让另一个 Application 管理同一 Argo CD。
本方案让 Argo CD 自身由明确的 Helm release 管理，其余组件进入 GitOps；升级 Argo CD 同样审核
版本/values 和回退路径。保留独立受控的 Kubernetes 管理通道，避免 IdP 故障锁死恢复操作。

Argo CD server 在集群内使用 HTTP，外部 TLS 由 Gateway 终结。必须启用入口网络策略并验证
CNI 执行；需要端到端 TLS 的环境应另配服务端证书与 BackendTLSPolicy，不能直接公开 HTTP。

## 4. 接入 GitOps 与分阶段同步

先推送环境配置，再生成 AppProject/Application。使用实际 URL 与审核后的完整 commit SHA：

```sh
bash .agent/run.sh --toolbox python scripts/generate-gitops.py --repo-url https://git.example.invalid/platform/config.git --revision REVIEWED_COMMIT_SHA
```

以上 URL/SHA 是占位符，使用前必须替换。输出 `.local/gitops/infrastructure.yaml` 不包含密码；
检查 sourceRepos、版本、destinations 后再 apply。私有 Git 仓库凭据通过 Argo CD repository
Secret 或秘密管理系统配置；没有连接凭据时 Application 不可能成功同步。
Envoy OCI chart 使用 `registry-1.docker.io/envoyproxy`，应注册为 Helm repository，
`enableOCI: "true"`。匿名 public registry 不需要伪造用户名密码。

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd create secret generic envoy-helm-repository --from-literal=type=helm --from-literal=enableOCI=true --from-literal=url=registry-1.docker.io/envoyproxy
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd label secret envoy-helm-repository argocd.argoproj.io/secret-type=repository
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -f .local/gitops/infrastructure.yaml
```

生成器不应用资源，不自动 sync/prune。`platform-infra` 可以管理 Argo CD namespace 和平台
集群资源，视为管理员项目；业务仅使用 `argocd-app-settings/project-production.yaml.example`
中的独立受限项目。跨目录不是安全边界，生产应拆分平台库与业务部署库。

先同步 `cert-manager`、`envoy-gateway`；没有 UI 时，管理员可请求一次受控同步：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd patch application cert-manager --type merge -p '{"operation":{"sync":{"prune":false}}}'
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd get application cert-manager -o jsonpath='{.status.operationState.phase}'
```

等待该次 operation 为 Succeeded，确认 sync/health 状态和实际 Deployment/CRD 就绪后再进入下一项。
不得只依据 Application 对象已创建或父应用 sync wave 判断依赖已经可用。

## 5. 建立 TLS 入口

在 `gateway-system` 中供应覆盖实际 Argo CD/Grafana/GitLab/Registry 域名的
`platform-public-tls` Secret。可以使用现有企业 PKI，或参考
`platform/security/clusterissuer-cloudflare.yaml.example` 与 `public-certificate.yaml.example`。
DNS 提供商示例不能直接用于其他 DNS 系统；ACME 先测试 staging，正式证书 Ready 后再开放入口。

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -k platform/networking/production
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n gateway-system get gateway platform
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production get httproute -A
```

确认 Gateway Programmed、Route Accepted/ResolvedRefs、LoadBalancer 地址、DNS、证书链和 SSO
登录均正常。Gateway 只允许带指定标签的 namespace 接入；GitLab chart 复用该 Gateway。
Prometheus/Alertmanager 不直接对公网发布。Argo CD CLI 经过该 HTTPRoute 时使用 grpc-web。
基础验收后可手动同步生成的 `platform-networking` Application 接管这些资源；接管后通过 Git
修改，不再由另一套自动脚本或 Helm release 持续管理同一资源。

## 6. 监控、日志与 GitLab

| 顺序 | 前提与动作 | 验收 |
| --- | --- | --- |
| Monitoring | `grafana-admin`、`alertmanager-config`、CSI 就绪；手动同步 monitoring | targets、Grafana 登录、真实告警 firing/resolved、数据重挂 |
| ECK Operator | 手动同步 elastic-operator；等待 CRD Established 与控制器 Ready | CRD/Webhook/Operator 健康 |
| ELK 数据面 | 按 `platform/logging/README.md` 供应秘密、分阶段初始化索引与采集 | ES green、日志可检索、TLS、队列和恢复 |
| GitLab | 按组件手册准备全部外部后端和 Secret；再同步 gitlab | 登录、HTTPS push、制品、SMTP、CI、备份恢复 |
| Registry/Runner | 审核可选 overlay 与独立 Runner 权限后启用 | 镜像 push/pull、流水线、隔离与凭据轮换 |

同步前执行指定组件的只读 live preflight，例如：

```sh
bash .agent/run.sh --toolbox --kubeconfig /srv/platform/admin.kubeconfig python scripts/preflight-production.py --context production --component monitoring
```

这个脚本只检查占位符、节点数量、StorageClass 和必要 Secret key，不校验秘密内容、后端连通性、
证书有效性、真实调度容量或备份质量。部署验收按组件手册完成，不能将 preflight 通过等同上线成功。

升级、数据库迁移和恢复流程见 [operations.md](operations.md)。
