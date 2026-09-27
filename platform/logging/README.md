# Kubernetes ELK 与日志采集

Operator 使用 `platform/releases.yaml` 锁定的 ECK 3.5.0，管理 `logging` namespace。
Elastic Stack 固定 9.5.4。这里是实际配置，不表示已部署或已完成运行验收。
独立单机方式见 `../../compose/logging/README.md`，两条路径不要共用同一份活跃数据目录。

| 部分 | production | local |
| --- | --- | --- |
| Elasticsearch | 3 节点，各请求4Gi/限制8Gi，100Gi PVC，硬反亲和 | 单节点2Gi上限、10Gi local-retain、关闭 mmap |
| Kibana | 2副本，共同 secureSettings | 单副本 |
| Logstash | 2副本，20Gi PVC、8GB持久队列 | 单副本，3Gi PVC、1GB队列 |
| Filebeat | Linux DaemonSet，读取节点容器日志，mTLS发送 | 同样需要节点日志与有限元数据权限 |

生产至少三个符合调度条件的节点。`production-rwo` 需要真实 CSI；没有自动供应后端。
ES 的 `DeleteOnScaledownOnly` 保留集群删除时的 PVC，但缩小节点集仍可能删除对应 PVC；
缩容需先评估数据迁移、PV Retain 和备份。Logstash 队列不等于跨节点复制或业务数据备份。

## 准备和渲染

```sh
bash .agent/run.sh kubectl kustomize platform/logging/kubernetes/production
bash .agent/run.sh kubectl kustomize platform/logging/kubernetes/local
```

这只做 Kustomize 渲染，不验证运行中的 ECK webhook。生产节点需要按所选 Elastic 版本
要求配置 `vm.max_map_count` 等内核参数；通过节点运维流程配置，不能在业务 Pod 中默认加入
特权 init container 修改宿主。先验证 CSI、可用磁盘、DNS和资源预算。

在 `logging` namespace 通过秘密系统创建 `logging-kibana-secure-settings`，含三个稳定、
至少32字符的独立强随机值，全部副本一致：

- `xpack.security.encryptionKey`
- `xpack.encryptedSavedObjects.encryptionKey`
- `xpack.reporting.encryptionKey`

这些 key 必须随备份安全保存。重新生成会影响已有会话、加密对象或报表。不要提交 Secret 明文。

## 分阶段安装

先部署 cert-manager 与 ECK Operator，并等待控制器就绪和所需 CRD Established。
命令中的 kubeconfig/context 需替换为实际目标；示例假设 namespace 已由基础层创建：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production wait --for=condition=Established --timeout=120s crd/elasticsearches.elasticsearch.k8s.elastic.co crd/kibanas.kibana.k8s.elastic.co crd/logstashes.logstash.k8s.elastic.co crd/beats.beat.k8s.elastic.co crd/certificates.cert-manager.io
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -k platform/logging/kubernetes/production -l app.kubernetes.io/component=foundation
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n logging get elasticsearch,kibana,certificate,pvc
```

foundation 创建 ES/Kibana、证书与采集权限，先不启用 Logstash/Filebeat。等待 ES green、
Kibana 可用、三个 Certificate Ready；通过受控 TLS 访问 Kibana Dev Tools 或 Elasticsearch
管理接口，在开始采集前按次序提交：

| API | JSON 文件 |
| --- | --- |
| `PUT _ilm/policy/platform-logs-14d` | `kubernetes/operations/ilm-policy.json` |
| `PUT _index_template/platform-logs` | `kubernetes/operations/index-template.production.json` |

本地使用 `index-template.local.json`。先检查文件中的 policy 名称与索引 pattern；索引配置
改变不会自动重写既有 backing indices。API 管理身份只用于初始化，不能作为 Logstash writer。

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -k platform/logging/kubernetes/production -l app.kubernetes.io/component=collection
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production -n logging get logstash,beat,pods,pvc
```

初次初始化后可同步 `platform-logging` Application 接管整套资源。此后保持 Git 作为持续管理源，
不要并行使用另一个 Helm release 或控制器改写它们。生产 Git revision 应固定到已审核提交。

## TLS、权限与入口

ES/Kibana HTTP TLS 由 ECK 默认管理；Logstash 通过 `elasticsearchRefs` 获得 ECK 管理的用户
及 CA，不使用 elastic 超级用户。Filebeat → Logstash 使用单独的 cert-manager CA 与 mTLS，
服务私钥采用 Logstash 支持的 PKCS8。这里的 CA Secret 在集群内生成，不进入源码。

根 CA 的替换需要重叠信任迁移，不能直接删除 Secret。叶证书续期后，验证 Filebeat/Logstash
确实重新载入证书；必要时通过更新其 ECK podTemplate 注解滚动重建，观察 TLS 连接与队列。
不要假定挂载文件改变就意味着长连接已切换证书。

Kibana 当前保持内部 HTTPS Service，没有配置公网路由。若接到共享 Gateway，需要使用
BackendTLSPolicy/受信后端 CA 或受控 TLS passthrough；不能以关闭 ECK TLS 代替该配置。
Filebeat 读取 `/var/log/containers` 和 `/var/log/pods`，且有节点元数据只读权限；这是管理员
管理的日志采集组件，不适合在 restricted 业务 namespace 中原样部署。

## 验收与恢复

从示例工作负载输出带唯一标记的日志，在 `logs-kubernetes-platform` data stream 查询。
确认 Kubernetes 元数据、时间戳、重复/丢失范围、索引 replica、ILM、PVC和积压指标正确。
模拟 Logstash/ES 暂时不可用并恢复，测量队列回放；不要把 Pod Ready 当成采集链路验收。

Elasticsearch 使用受支持的 snapshot repository 和 SLM，凭据由 secureSettings/工作负载
身份供应；先在独立集群恢复快照后再接受备份。不要复制在线 ES 数据目录当作一致性备份。
集群内 CA、Kibana加密 key、角色/ILM/template以及外部 Git 副本也要纳入恢复。

参考：[ECK 安装](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/install)、
[Logstash 配置](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/configuration-logstash)、
[ECK 存储](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/volume-claim-templates)。
