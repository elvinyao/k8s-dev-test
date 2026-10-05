# 生产业务 GitOps 接入示例

这个内部静态 HTTP 服务用于验证生产业务 Project、同步与 Service 路由。它不承载业务数据，
不创建 Namespace、RBAC、Secret 或存储；PVC 持久化验收见[存储示例](../storage-demo/README.md)。
Kustomize 输出 4 个对象：Deployment、Service、PDB 和带内容摘要名称的 ConfigMap。

Deployment 使用固定 BusyBox 1.37.0 多架构镜像摘要，UID/GID 65532、只读根文件系统、
RuntimeDefault seccomp、禁止提权、删除全部 capabilities，且不挂载 ServiceAccount token。
两副本按 hostname 硬反亲和；正常运行至少需要两个合格 worker，滚动发布的一个 surge Pod
还需要第三个 worker。资源请求和限制均显式指定，HTTP 监听非特权端口 8080。
静态站点只是小型接入探针；真实业务应使用自己的受支持镜像及实际健康端点。

## 离线校验与镜像检查

```sh
bash .agent/run.sh --toolbox python apps/production-demo/check.py --example
bash .agent/run.sh --toolbox python -m unittest discover -s scripts/tests -p test_business_gitops.py -v
```

校验器读取实际 Kustomize 输出，核对 Project 允许的仓库、目的地、资源种类和每个对象的
namespace。负例验证 PV、Namespace、StorageClass、ClusterRole、Role、RoleBinding、
Secret、裸 Pod、NetworkPolicy 和跨 namespace 资源均被当前业务 Project 拒绝。
它只检查这里显式配置的权限合同，不代替 Argo CD RBAC 或 Kubernetes admission。

下面命令只运行一次性镜像检查，不发布端口、不部署集群；仅挂载只读 `index.html`，无网络
出口。它在 UID 65532、只读根文件系统和删除 capabilities 的条件下启动 HTTPD，再用容器内
回环请求确认页面标记。结束时自动移除检查容器。Docker 首次使用时可能需下载镜像：

```sh
bash .agent/run.sh --docker --toolbox python apps/production-demo/smoke-image.py
```

2026-10-05 在当前 Docker 主机实测通过。该结果仅证明当前主机架构上的镜像/命令兼容性，
不证明 Kubernetes 部署、所有镜像架构或 Service、网络策略已验证。

另一个[实际 GitOps 验收](../../docs/gitops-validation.md)已在临时三节点 kind 中通过：
Argo CD 从真实测试 Git commit 部署本示例，两副本在不同节点 Ready，Service 返回页面，
缩容漂移经手工同步恢复；同一 Project 拒绝 Secret/ClusterRole。该检查未验证真实用户身份、
生产入口或 CNI 网络隔离，命令为 `bash .agent/run.sh --docker --toolbox python scripts/smoke-gitops.py`。

## 由管理员接入业务仓库

1. 按[业务 GitOps 手册](../../argocd-app-settings/README.md)将本目录复制到受限业务部署库，
   保持源路径 `apps/production-demo`。审核后提交并推送；不把平台管理配置一并授予业务用户。
2. 在私有 `.local/gitops/` 中保存已配置的 Project/Application，替换双方仓库 URL 和完整
   commit SHA。示例中的全零 SHA 是故意不可部署的占位符，不是已存在或已经验证的提交。
3. 管理员预先建立 `apps-prod`，确保 Restricted Pod Security 与
   `platform/security/apps-prod.yaml` 中的配额、默认拒绝和 DNS 规则已按集群配置生效。
4. 若启用了默认拒绝，管理员审核并应用下面的最小网络策略。它仅允许同 namespace 中本示例
   Pod 相互访问 TCP 8080；DNS 出站由平台 DNS 规则负责。它不会开放公共访问，也不会影响
   业务 AppProject 的资源授权。若要接入真实调用者，由管理员单独审核允许规则。

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -f apps/production-demo/admin-networkpolicy.yaml.example
```

网络策略示例刻意不加入 Kustomization：业务 Project 没有创建 NetworkPolicy 的权限。
只应用允许策略而没有平台默认拒绝/DNS 策略，不等于完成 namespace 网络隔离。

## 手动同步与验收

完成业务 GitOps 手册中的 Project/Application 创建后，管理员请求一次手动同步：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd patch application production-demo --type merge -p '{"operation":{"sync":{"prune":false}}}'
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n argocd get application production-demo -o jsonpath='{.status.operationState.phase}'
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n apps-prod rollout status deployment/production-demo --timeout=180s
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n apps-prod get pods -l app.kubernetes.io/name=production-demo -o wide
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n apps-prod exec deployment/production-demo -c web -- wget -qO- http://production-demo:8080/
```

要求 operation 为 Succeeded、两副本 Ready 且分布在不同节点，Service 请求返回
`production-demo-ready`。若 DNS/Service 请求失败，检查集群 DNS 实现、平台 DNS 出站规则、
此应用的 ingress/egress 规则和 EndpointSlice；不要关闭默认拒绝来掩盖原因。
使用实际业务身份确认只能查看/同步 `platform-apps-production/*`，不能同步平台 Application；
本地的资源种类拒绝检查不等同这种现场用户权限证据。临时集群已验证上面的业务部署与
Service 请求；真实生产环境、业务身份及网络策略执行仍需分别验收。

修改页面会生成新的 ConfigMap 名称并滚动 Deployment。默认没有自动 prune；审核旧 ConfigMap
不再被任何保留的 ReplicaSet 引用后，再由管理员安排清理，不要在发布中自动删除回退材料。
