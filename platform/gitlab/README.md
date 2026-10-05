# GitLab Kubernetes：Cloud Native Hybrid 示例

`values-production.yaml.example` 使用官方 **GitLab chart 10.4.1 / GitLab 19.4.1**。
Webservice、Sidekiq、Shell、Toolbox 等应用服务运行在 Kubernetes；PostgreSQL、Redis、
对象存储和 Gitaly 位于集群外。本配置与 `compose/gitlab/` 是两种部署选择，
不能让两个活跃 GitLab 同时写同一数据库或仓库存储。现在只提供配置和离线渲染，未部署实例。
GitLab 的安装和升级由 Helm 管理；GitOps 生成器不再为它创建 Application。
这是为了保留 chart 区分首次安装与升级的 hook 行为，尤其依赖历史 chart-info 的升级检查。
不要永久关闭 upgradeCheck 来绕过首次同步错误，也不要同时让 Helm 和 Argo CD 管理本实例。

## 依赖与实际配置

- PostgreSQL：准备受 GitLab 19.4 支持的版本、数据库 `gitlabhq_production` 和 `gitlab`
  用户，DBA 预建所需扩展；模板采用密码加客户端证书的 mTLS。Chart 10.4.1 的此配置渲染为
  `sslmode: verify-ca`，不执行 `verify-full` 主机名验证，应使用受控专用 CA 和受限网络；
  需要强制主机名验证的环境须另行审查连接方案。
- Redis：专用受支持实例及 TLS endpoint，采用 `rediss`、密码和 CA 验证；外部故障转移
  由后端服务负责。此处一个 endpoint 并不保证该服务 HA。
- Gitaly：外部 Linux package 部署，版本与 GitLab 对齐，存储名必须含 `default`。
  配置 TLS 8076、匹配的 Gitaly token / Shell secret，并允许回调 GitLab API。
  每个外部 endpoint 使用 `tlsEnabled: true`，由 `gitlab-backend-ca` 提供 CA 信任。
  保持 `global.gitaly.tls.enabled: false`：chart 10.4.1 的全局开关还会挂载内部
  `gitlab-gitaly-tls` Secret，即使内部 Gitaly 已关闭。逐 endpoint 开关仍渲染 `tls://` 连接。
  HA 场景需进一步设计 Praefect、数据库和存储副本。
- S3 兼容对象存储：TLS、最小权限身份、独立 bucket 和异地备份。先创建 values 中全部
  bucket，业务数据、备份和备份临时 bucket 不能混用。
- Kubernetes：至少两个可调度 worker 满足硬反亲和；已有 Envoy Gateway 1.9.1、Gateway
  API CRD、Metrics Server，以及真正的 `production-rwo` 存储类。Toolbox PVC 和备份临时
  PVC 都是处理空间，容量须覆盖备份展开量；它们不是业务数据的唯一持久存储。

将示例复制到私有环境配置，替换 `example.invalid`、bucket、存储类、SMTP 和资源预算。
示例应用 Pod 内存请求已超过 10 GiB，后端资源另算；这不是笔记本最小配置，副本数也不等于
整套 GitLab 已实现 HA。没有自动供应外部后端、证书或账号。

参考：[外部 PostgreSQL](https://docs.gitlab.com/charts/advanced/external-db/)、
[Redis](https://docs.gitlab.com/charts/advanced/external-redis/)、
[Gitaly](https://docs.gitlab.com/charts/advanced/external-gitaly/)、
[对象存储](https://docs.gitlab.com/charts/advanced/external-object-storage/)。

## 先供应 Secret

在 `gitlab` namespace 通过秘密管理系统提供下列内容，不把真实值提交到 Git。
Secret 的 base64 不等于加密；应限制读取并开启集群静态加密。
名称/key 的机器可读清单见 [required-secrets.yaml](required-secrets.yaml)，由 preflight 和
渲染集成检查共用。可选 Registry 的额外依赖单独列出；chart hook 生成项不需要管理员预造，
但必须核实安装时 hook 成功，并将生成的秘密纳入备份。

| Secret | key / 内容 |
|---|---|
| `gitlab-initial-root-password` | `password`，初始 root 密码 |
| `gitlab-postgresql` | `password`，数据库用户密码 |
| `gitlab-postgresql-tls` | `ca.crt`、`tls.crt`、`tls.key`，数据库 CA 与客户端身份 |
| `gitlab-redis` | `password`，Redis 密码 |
| `gitlab-backend-ca` | `backend-ca.crt`，Gitaly/Redis/私有对象存储 CA bundle |
| `gitlab-gitaly-token` | `token`，与外部 Gitaly 一致 |
| `gitlab-shell-token` | `secret`，与外部 Gitaly Shell 一致 |
| `gitlab-object-storage` | `connection`，Rails 的 YAML 连接配置 |
| `gitlab-backup-storage` | `config`，Toolbox 的 s3cmd INI 配置 |
| `gitlab-smtp` | `password`，SMTP 密码 |

`gitlab-object-storage` 的 `connection` 示意；真实内容仅放秘密系统：

```yaml
provider: AWS
region: us-east-1
aws_access_key_id: REPLACE_PRIVATELY
aws_secret_access_key: REPLACE_PRIVATELY
endpoint: https://s3.example.invalid
path_style: true
```

`gitlab-backup-storage` 的 `config` 示意：

```ini
[default]
access_key = REPLACE_PRIVATELY
secret_key = REPLACE_PRIVATELY
host_base = s3.example.invalid
host_bucket = s3.example.invalid
use_https = True
check_ssl_certificate = True
check_ssl_hostname = True
```

以上面向 S3 兼容端点，原生 AWS/IAM 应替换为相应连接方案。Chart 会生成额外内部 Secret；
首次安装后安全备份这些秘密，尤其 Rails 加密密钥，不能靠重新安装生成的随机值恢复原数据。

## 共享 Gateway 与 SSH

模板生成的 HTTPRoute 连接 `gateway-system/platform` Gateway 的 `https` listener，
不安装第二套 Envoy、Ingress controller 或 cert-manager。平台必须使该 listener 的证书
覆盖真实 GitLab 域名，并允许来自 `gitlab` namespace 的 HTTPRoute。跨 namespace 的
parentRefs 通过 listener `allowedRoutes` 控制，Secret 通常位于 Gateway 自身的 namespace。
DNS 指向入口，确认 Route 状态 `Accepted=True`、`ResolvedRefs=True` 以及 HTTPS 可访问。

默认仅发布 HTTPS，SSH Service 留在集群内。需要 SSH 时，在私有 overlay 中选择一种方案：
给平台 Gateway 增加 `gitlab-ssh` TCP listener（2222），开启 GitLab Shell 的
`gatewayRoute.enabled: true`、`sectionName: gitlab-ssh`；或将 `gitlab.gitlab-shell.service.type`
设为 `LoadBalancer`，以防火墙限制来源。对应 DNS/NAT 和 `global.shell.port` 必须一致。
这两种方案都需平台先提供真实网络入口，不能只改 clone URL。

## 可选 Registry 与 Runner

基础 values 同时关闭 Registry Deployment 与 Rails Registry 集成，并关闭 KAS、Pages
和内置 Runner；可选 overlay 同时打开 Registry 部署和 Rails 集成。Runner 独立启用方法、受限
Kubernetes SA/RBAC 和注册步骤见 `../../docs/gitlab-production.md`，可连接这里的 GitLab
HTTPS endpoint；不会默认启用 privileged 或 Docker socket。KAS/Pages 如需启用，另行供应
域名、路由、TLS 以及相关存储后再进行集成验证。

启用 Registry 时叠加 `values-registry.yaml.example`，先创建专用
`platform-gitlab-registry` bucket、`registry.example.invalid` 的真实 DNS，并让共享 HTTPS
listener 证书覆盖它。在 `gitlab` namespace 供应 `gitlab-registry-storage` Secret，
其 `config` key 内容如下，真实凭据不入 Git：

```yaml
s3:
  accesskey: REPLACE_PRIVATELY
  secretkey: REPLACE_PRIVATELY
  region: us-east-1
  regionendpoint: https://s3.example.invalid
  bucket: platform-gitlab-registry
  secure: true
  v4auth: true
```

Registry overlay 初始不启用可选 metadata database；需要在线 GC 等数据库能力时，另建
外部数据库、角色、凭据及迁移/备份流程。对象 bucket 必须有独立备份，配置变化和上传数据要
保持一致恢复点。验收镜像 login、push、pull 和恢复，不以 Pod Ready 作为完成条件。

采用 Registry overlay 时，先在部署用 checkout 中填写真实配置，preflight 加
`--gitlab-registry`，才会同时检查 Registry 域名、bucket 和 `gitlab-registry-storage/config`：

```sh
bash .agent/run.sh --toolbox --kubeconfig /srv/platform/admin.kubeconfig python scripts/preflight-production.py --component gitlab --gitlab-registry --context production
```

此检查读取 checkout 的 values，不能代替审查最后传给 Helm 的私有 values 文件。

## 渲染与安装

仅离线渲染，不连接集群或验证 Secret、DNS、后端连接：

```sh
bash .agent/run.sh --toolbox python scripts/verify.py
```

该命令校验锁定的 chart 归档、默认 GitLab 配置，以及可选 Registry overlay 的实际渲染。
它还核对 Pod、Job、CronJob 的必需 Secret 引用及外部 key 是否在供应清单中，并确认四处
Rails Gitaly 地址仍使用 TLS；这不证明 Secret 实际存在、证书有效或 hook 已运行。
依赖、Secret、Gateway、DNS、存储就绪后，将已审核的真实完整 values 放在私有
`/srv/platform/gitlab-values.yaml`，按明确上下文安装；`gitlab` namespace 须由平台先创建：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig --host-dir /srv/platform helm upgrade --install gitlab .cache/charts/gitlab-10.4.1/gitlab-10.4.1.tgz --namespace gitlab --kube-context production -f /srv/platform/gitlab-values.yaml --timeout 30m --wait
```

安装会执行数据库迁移；失败先检查迁移/依赖，不能盲目 Helm rollback 后继续使用已迁移的库。
验收登录、HTTPS push、LFS、制品、SMTP、测试流水线；开启对应入口后再验收 SSH 与 Registry。
Toolbox Pod 中使用 runner 包装的 `kubectl exec` 执行 `gitlab-rake gitlab:gitaly:check`
和 `gitlab-rake gitlab:check SANITIZE=true`，检验后端连接。

## 备份与升级

模板每天 02:00 按集群 CronJob 时区运行 Toolbox 备份，使用独立临时 PVC。监控任务失败、
备份 bucket 最近成功时间和恢复结果；存储端管理保留期限和异地副本。先完成一次手动
`backup-utility` 和隔离恢复，再把定时任务视为有效保障。Chart Pod 使用 `backup-utility`，
不能照搬 Compose 的 `gitlab-backup` 命令。

外部数据库、Gitaly、对象 bucket、Rails Secret、values 和证书都需要备份，并定义一致的
恢复点。升级先在恢复副本演练，遵循 GitLab 必经版本路径，后台迁移完成再前进；协调 Gitaly
版本。降级镜像不是数据库回退，回退要恢复匹配备份。
[Chart 备份与恢复](https://docs.gitlab.com/charts/backup-restore/)、
[Chart 升级](https://docs.gitlab.com/charts/installation/upgrade/)。
