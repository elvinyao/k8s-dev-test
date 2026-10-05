# ELK 隔离运行验收

`scripts/smoke-logging.py` 面向本目录的完整 Compose 配置，使用默认资源限制和真实镜像，
验证 HTTP/Beats 采集、认证权限、Kibana、服务重建与日志快照恢复。它是一次性验收入口，
不会建立供业务长期使用的部署；长期部署步骤见 [README](README.md)。

## 当前证据与前提

完整运行尚未完成。2026-10-05 的前置检查发现，当前 OrbStack Docker 为 ARM64、8 CPU、
总内存约 7.8 GiB，且还有其他业务容器。检查按预期拒绝启动 ELK；没有拉取新镜像、修改
内核设置或启动 ELK 服务。该结果不能作为采集或恢复已成功的证据。

执行前准备：

- 按[工具链](../../docs/tooling.md)构建工具箱，runner 与 Docker daemon 在同一台主机。
- Docker 实际报告至少 12 GiB 内存；建议分配 16 GiB，并为已有负载保留容量。可用内存还须
  覆盖三个服务原始内存上限之和加 2 GiB。检查不会停止其他应用或调整 Docker 分配。
- Docker 文件系统至少 20 GiB 可用空间，供镜像、测试卷及快照使用；这不是生产容量规划。
- Linux VM/主机 `vm.max_map_count` 至少 262144；Elastic 推荐 1048576。前者是当前
  [bootstrap 最低检查值](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/bootstrap-checks)，
  后者是[虚拟内存配置建议](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/vm-max-map-count)。
  本机 262144 满足最低检查，脚本会记录推荐值提示，不会修改 sysctl。

```sh
bash .agent/run.sh --docker --toolbox python scripts/smoke-logging.py --preflight-only
bash .agent/run.sh --docker --toolbox python scripts/smoke-logging.py --check-model
bash .agent/run.sh --docker --toolbox python scripts/smoke-logging.py
```

第一条仅检查前提。报告为 `preflight-passed` 时也不代表服务运行通过；条件不满足时返回非零。
第二条生成测试凭据和两套 Compose 模型并解析后清理 fixture，不拉镜像或启动服务；容量
不足仍可做模型检查，报告为 `model-checked`，不会将资源问题标为已解决。
第三条才会创建服务并做实际恢复，首次下载镜像及启动服务可能需要数分钟。

## 完整流程的验收范围

测试先复制公开配置到 `.local/logging-smoke-<随机值>/fixture/`，排除已有 `.env`、运行数据
和秘密，再生成自己的一套 CA、证书与随机密码。Compose 项目、网络和卷使用随机名称。
不会挂载 Docker socket 到 ELK，也不会挂载现有业务数据或 CA 私钥。

测试配置只取消宿主端口发布、关闭自动重启、将网络设为 internal，并增加验收客户端；
ES/Kibana/Logstash 的配置、内存上限、堆大小、TLS、密码加载和持久卷行为保持原样。
Filebeat 使用相同 9.5.4 系列的[官方容器](https://www.elastic.co/docs/reference/beats/filebeat/running-on-docker)，
读取自己生成的测试文件，沿示例的 filestream → Beats mTLS → Logstash → Elasticsearch 路径发送。
它不读取宿主或 Kubernetes 的真实日志。

| 阶段 | 通过条件 |
| --- | --- |
| 首次启动 | ES healthy；原 bootstrap 成功创建身份、模板、ILM/SLM 与 repository；Kibana/Logstash healthy |
| 接入边界 | 无凭据/错误凭据 HTTP 请求拒绝；Beats 无客户端证书拒绝，有效证书可握手 |
| Writer 权限 | 允许本日志索引创建事件，拒绝读取、更新、删除以及写入其他索引 |
| 数据链路 | HTTP 和真实 Filebeat 发送的两个唯一 marker 均可从 ES 查询；Kibana Data View 可创建读取 |
| 服务重建 | 保留原卷和秘密强制重建三服务，已写事件及 Data View 仍存在 |
| 快照 | 原服务记录日志总数并创建 SUCCESS 快照，之后停止源集群写入 |
| 空卷恢复 | 第二个独立项目使用全新 ES 卷，快照目录只读挂载，repository 只读注册；恢复到新索引前缀 |
| 恢复内容 | 日志总数和两个 marker 对应快照，原 ILM 已解除；重复恢复到同一前缀被拒绝 |

恢复阶段只启动第二套 ES，不同时运行两套完整 ELK。它不会执行原 bootstrap，因为 bootstrap
会注册可写 repository 和 SLM。两套 ES 只能有一个快照写入者。

## 报告、清理与限制

每次报告位于 `.local/logging-smoke-<随机值>/report.json`，包含源码 SHA256、镜像身份、
前提检查、逐步日志、验收结果和清理结果。测试期间源码改变会使运行失败，需完成编辑后重跑。
通过该脚本不自动更新本文的运行证据；上线前应归档实际通过的报告及对应 Git commit。

正常结束或失败后，脚本只删除自己登记的随机项目容器、网络和卷。服务失败时先收集限量日志；
清理成功后删除含临时凭据和快照的 fixture，保留权限受限且已替换已知密码值的日志与报告。
清理失败时返回失败并保留 fixture，按报告中的项目名排查；不要对其他 Compose 项目执行清理。
镜像缓存保留。运行日志不应公开分享，正式凭据始终不参与测试。

此恢复只覆盖 `platform-logs-*`，不恢复安全身份、Kibana feature state、真实用户、加密对象、
Filebeat checkpoint 或 Logstash PQ/DLQ；因此不能称为整个平台灾难恢复。跨主机备份介质、
生产负载、独立告警、真实浏览器/SSO、证书轮换、网络分区和节点故障还需目标环境演练。
