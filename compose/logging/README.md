# ELK 单机部署配置

这里提供 Elasticsearch、Kibana、Logstash 的可部署配置与初始化工具；提交配置不表示服务已安装或运行。默认启用认证、内部 TLS、Kibana HTTPS、Logstash HTTPS/Beats mTLS、持久队列、日志保留和快照策略。该拓扑只有一个 Elasticsearch 节点、一个宿主机，不能提供节点或机房故障下的高可用。

三个组件统一固定为 **9.5.4**。2026-09-26 核查了官方 [Elasticsearch](https://www.docker.elastic.co/r/elasticsearch/elasticsearch)、[Kibana](https://www.docker.elastic.co/r/kibana/kibana)、[Logstash](https://www.docker.elastic.co/r/logstash/logstash) 镜像清单，均有 amd64/arm64 镜像。版本维护范围需随 [Elastic 维护政策](https://www.elastic.co/support/eol) 持续复核；正式发布可在内部镜像仓库进一步锁定 digest。

## 部署前提

- Docker Engine / Docker Desktop 和 Compose v2；所有项目命令从仓库根目录经 `bash .agent/run.sh` 执行。`--docker` 显式接入宿主 Docker daemon，服务仍由宿主管理。
- 默认容器内存上限共 8 GiB，另需 Docker、操作系统和文件缓存空间。建议给这组服务准备至少 12–16 GiB 内存，再用实际峰值日志量、查询量和保留天数测算容量；这不是吞吐量保证。
- 管理员预先配置 Linux/Docker VM 的内核与存储：检查 `vm.max_map_count`，建议值为 `1048576`；禁用交换或验证内存锁生效，预留文件句柄和磁盘容量。脚本不会更改宿主 `sysctl`。具体要求见 [Docker 生产配置](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-docker-prod)。本配置强制执行 Elasticsearch bootstrap checks。
- 数据卷和快照使用可靠的持久磁盘；避免把 Docker Desktop 虚拟机当作生产存储。先定义 RPO/RTO、告警接收人、备份目的地和容量扩容流程。
- 默认所有宿主端口仅绑定 `127.0.0.1`。远程使用时为 Kibana、采集端分别分配私网 DNS、地址和防火墙规则，保持 Elasticsearch 9200 仅管理网可达；9300 和 Logstash 管理 API 9600 不发布。

## 初始化证书和 secret 文件

在仓库根执行下面的命令，把 `--host-root` 改为 **Docker daemon 所在宿主的仓库绝对路径**。默认本机浏览器地址是 `https://localhost:5601`：

```sh
bash .agent/run.sh python compose/logging/scripts/prepare.py \
  --host-root /absolute/path/to/k8s-dev-test
```

若已确定远程域名，首次初始化时指定真实名称，例如：

```sh
bash .agent/run.sh python compose/logging/scripts/prepare.py \
  --host-root /absolute/path/to/k8s-dev-test \
  --kibana-host kibana.example.com \
  --ingest-host logs.example.com
```

此命令在 runner 容器中使用 Python 和 OpenSSL，生成被 Git 忽略的 `compose/logging/runtime/` 及 `.env`。它不会启动服务。CA 私钥单独保存于 `runtime/ca-private/`，不会挂载到任何长期服务。各服务仅获得自身的 PKCS#8 私钥、证书与 CA 公钥。SAN 包括服务 DNS 名、localhost、127.0.0.1，以及指定的外部域名；所有客户端保持完整证书链和主机名校验。

服务证书有效期 365 天，CA 为 10 年。已有完整初始化重复执行时保留所有密钥和密码；参数改变、缺少文件、部分初始化均会停止，不会悄悄生成新 CA。证书轮换需提前告警、签发和维护窗口；不要通过删除 `runtime/` 轮换凭证。企业环境可由 PKI/secret manager 提供同名文件，保留内部服务名 SAN；CA 私钥应保存在离线或受控签发环境。

生成的密码为独立的随机 64 位十六进制字符串，默认不打印。secret 文件为 root:root、0640，长期服务为 UID 1000 / GID 0；CA 私钥为 0600。Compose 的本地 file secrets 本质上仍是磁盘文件，需要磁盘加密、访问控制和独立加密备份。不要把 `.env`、`runtime/`、CA 私钥或密码提交到 Git。

复核 `.env` 中的所有路径，不能把 `/workspace` 用作宿主 bind 源。远程部署另需设置 `KIBANA_BIND_ADDRESS`、`LOGSTASH_BIND_ADDRESS` 为实际私网地址，调整 `KIBANA_PUBLIC_URL`。IPv4/DNS 是此脚本生成 public URL 的预期输入；IPv6 公网入口需自行设置带方括号的 URL。`ELASTICSEARCH_BIND_ADDRESS` 可以继续保持回环地址。浏览器与采集器应导入 `runtime/tls/public/ca.crt`，不要跳过 TLS 校验。

若生产 secret/config 存放在仓库外的 `/srv/platform`，修改 `.env` 的绝对路径，并在对应命令中加 `--host-dir /srv/platform`，让 Compose 客户端能够读取该目录；它在 runner 内为只读，实际服务挂载权限仍由 Compose 定义。外部文件和目录应由部署管理员提前准备，短语法自动创建目录未启用。

## 检查与首次启动

无需 secret 文件、无需 Docker socket 的静态检查：

```sh
bash .agent/run.sh docker compose \
  --project-directory compose/logging \
  --env-file compose/logging/.env.example \
  -f compose/logging/compose.yaml config
```

完成初始化并审核 `.env` 后，依次执行以下命令。首次先启动 Elasticsearch，然后创建服务账号及策略，最后启动采集和界面；正常 `up` 不会运行 `init` 或 `ops` profile。

```sh
bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml up -d --wait elasticsearch

bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile init run --rm bootstrap

bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml up -d --wait kibana logstash
```

Bootstrap 显式使用一次性的管理员凭据，创建 `logstash_writer` 及 `platform_logstash_writer` 角色、设置 `kibana_system` 密码、创建只读角色、索引模板、14 天 ILM 保留策略、快照仓库与每天 **02:30 UTC** 的 SLM 策略。快照保留 14 天，至少 3 个、最多 30 个。重复运行 bootstrap 会按源配置重新应用这些策略和同一组密码，因此生产修改策略后应先同步源配置。

Kibana 长期连接使用 `kibana_system`，密码及三个固定加密 key 从 secret 文件导入 Kibana keystore。Logstash 使用专用 writer，仅获得 `monitor` 和 `platform-logs-*` 的创建/追加权限；它没有读日志、删除索引、管理模板/ILM 或安全账号的权限。模板和生命周期由 bootstrap 管理。Logstash 两个密码导入带口令的 keystore；口令从 secret 文件读取到容器内进程环境，不放在 Compose 环境配置中。[Logstash 安全连接](https://www.elastic.co/docs/reference/logstash/secure-connection)和 [keystore](https://www.elastic.co/docs/reference/logstash/keystore)说明了相应机制。

`elastic` 只用于初始化和显式运维。首次在 Kibana 完成人员账号/SSO 配置后，日常查询账号分配 `platform_log_reader` 加对应 Space 的 Discover/Dashboard 权限，不要直接给所有人 `superuser` 或 `kibana_admin`。`kibana_system` 是后台服务身份，不能作为人的登录账号。持久加密 key 需要随备份保管，否则重建后可能无法解密已有对象；参考 [Kibana 安全配置](https://www.elastic.co/docs/reference/kibana/configuration-reference/security-settings)。

已有 ES 数据卷时，`ELASTIC_PASSWORD_FILE` 不会重置已存在的管理员密码；应恢复匹配的原 secret 或走官方密码重置流程。修改密码需先在 ES API 更新服务身份，再更新外部 secret，最后 `up -d --force-recreate kibana logstash` 重新加载；不要只改 `.env`。轮换 Logstash keystore 口令也要重建容器，让临时 keystore 按新口令生成，原始 secret 文件保持外置。

## 采集和验收

`config/filebeat.yml.example` 展示单个真实日志源的 filestream 配置。把外部采集域名改为证书 SAN 内的名称，并将该采集器自己的 `client.crt/client.key/ca.crt` 挂入配置路径。初始化生成的 `runtime/tls/collector/` 可用于第一台采集器；多台生产采集器应分别签发、分发、轮换证书，不要共享同一个客户端私钥。Beats 5044 强制客户端证书，证书只由受控 CA 签发；应结合来源网络白名单，因 TLS 接入本身没有按证书主体分租户路由。

Kubernetes 应在各节点运行采集 DaemonSet，读取 `/var/log/containers`、持久化采集进度、配置 Kubernetes metadata/RBAC 和敏感字段脱敏，再经 mTLS 发送到 Logstash；本 Compose 不会自动采集 k8s 或宿主日志。HTTP 8443 接口用于应用/其他代理发送 JSON，必须使用 `collector` 用户及 `ingest_password`，客户端信任 CA。它限制单请求 1 MiB，生产还需按来源配置速率限制和缓冲重试，避免无限重试导致重复事件。

部署后运行：

```sh
bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml ps

bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile ops run --rm ops check

bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile ops run --rm ops smoke
```

`smoke` 会通过 HTTPS + Basic 写入一个带唯一 ID 的事件，并确认它出现在 ES，因而覆盖认证、TLS、pipeline 和 writer 权限。随后在 Kibana 为 `platform-logs-*` 建立 Data View，时间字段选 `@timestamp`，确认 Discover 可查询。还应分别验证无密码 HTTP 返回拒绝、无客户端证书 Beats 握手失败，重启服务后历史日志和 Kibana 对象仍在。

Compose healthcheck 的能力有限：ES 探测 TLS 与 401 安全响应，Kibana 检查 `/api/status`，Logstash 检查本地管理端口。它们不代替写入验收、日志延迟、丢失率、磁盘水位、GC/heap、队列积压、DLQ、证书到期和 SLM 失败告警。Logstash API 仅容器 loopback 可达；若接入监控，应另配受认证的采集路径，不要直接公开 9600。PQ/DLQ 是有上限的缓冲，不构成跨主机副本；满队列会反压，DLQ 满会丢弃后续失败事件，需要监控和回放流程。

## 备份和恢复演练

默认快照目录是宿主 `runtime/snapshots`，仅用于验证 API 流程，宿主故障会与数据一起丢失。生产将 `LOGGING_SNAPSHOT_DIR` 指向独立故障域的受控共享文件系统，或改成 S3 等对象存储 repository，并配置相应 keystore 凭据。文件系统 repository 应只有一个集群写入；恢复集群以只读方式注册。不要直接复制运行中的 ES data volume 当作备份，参考 [快照恢复](https://www.elastic.co/docs/deploy-manage/tools/snapshot-and-restore/restore-snapshot)。

手动快照和列出结果：

```sh
bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile ops run --rm ops snapshot-create

bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile ops run --rm ops snapshot-list
```

必须看到 `SUCCESS`；超时并不说明服务端任务被取消，先列出快照状态再决定重试。日志、系统索引和集群状态随全量快照保存，快照也含安全相关状态，应限制访问并加密。另行备份 `.env`、配置、CA/客户端证书和全部 secret/加密 key；这些文件不在 Elasticsearch 快照里。

恢复演练使用一个现有快照名和全新的索引前缀：

```sh
bash .agent/run.sh --docker docker compose \
  --project-directory compose/logging --env-file compose/logging/.env \
  -f compose/logging/compose.yaml --profile ops run --rm ops restore-logs \
  --snapshot platform-20260926-023000 --prefix drill-20260926
```

该操作将日志恢复为 `drill-20260926-platform-logs-*`，不覆盖在线索引、不恢复安全/Kibana feature state，并移除原 ILM policy，防止演练数据立刻因年龄被删除。比较文档数、时间范围、抽样内容和耗时后，再由运维显式清理演练索引。完整灾难恢复应在隔离集群验证 ES 版本兼容性、系统 feature states、角色/密码和 Kibana 加密 key；安全索引恢复会改变身份状态，不能把上述“仅恢复日志”命令当作全平台恢复。

## 升级到多节点生产拓扑

不要直接对当前 `single-node` 服务执行 `--scale`。跨至少三个独立故障域配置 master-eligible 节点，按负载分离 data/ingest 角色；删除 `discovery.type: single-node`，配置唯一 node.name、稳定 transport publish DNS、seed hosts，以及各节点 SAN 与 mTLS。`cluster.initial_master_nodes` 只用于新集群首次引导，加入现存集群不能重新引导。复制模板前把副本数从 0 改为至少 1，并更新既有索引设置、分片分配感知和容量规划；本目录没有伪装成 HA 的同机多容器 override。

Kibana 多副本需要负载均衡与一致的安全/对象/报表加密 key。Logstash 多实例要配独立持久队列、客户端负载均衡和中断重试；需要跨主机抗丢失时评估持久消息队列。生产发布还需独立监控告警、SSO/RBAC、网络分区演练、备份恢复演练、日志脱敏和版本升级演练。升级前先快照并保存配置；ES 数据格式升级后通常不能直接换回旧镜像，回退应使用受支持的恢复路径。

停止保留数据可使用相同 runner/Compose 参数执行 `down`。不要附加 `-v`，该参数会删除数据卷。保留快照与 secret 的生命周期应独立于容器生命周期。
