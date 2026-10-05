# Prometheus、Alertmanager 与 Grafana

本目录提供可配置后部署的单机 Docker Compose 监控栈：Prometheus 采集指标与计算规则，Alertmanager 接收告警，Grafana 提供已经预置的数据源和概览面板。三个服务的数据分别使用 named volume，容器配置了 CPU/内存上限、健康检查、只读根文件系统和日志轮转。

默认仅监听宿主 `127.0.0.1`，仅采集这三个服务自身。**默认 Alertmanager 只在本地界面展示告警，不发送外部通知。** 正式值班使用前必须配置真实接收器，并验收告警触发、恢复和监控系统自身失联的通知。本目录没有默认挂载宿主 `/`、Docker socket、node-exporter 或 cAdvisor，也没有自动监控 Kubernetes 节点；节点指标需要按下面的接入说明增加。

此配置是单机运行基线。Prometheus 本地 TSDB、Grafana SQLite 和单实例 Alertmanager 都依赖同一 Docker 主机；容器 healthy、自动重启及数据卷持久化不等于生产高可用。已在临时 Compose 项目验证启动、自监控与空卷恢复；正式环境的负载、外部通知、TLS 和故障恢复仍需验收。

## 可重复的启动与恢复验证

从仓库根目录执行：

```sh
bash .agent/run.sh --docker --toolbox python scripts/smoke-monitoring.py
```

脚本复制本目录到被忽略的 `.local/monitoring-smoke-<随机ID>/`，生成独立凭据，使用原始
配置、固定版本镜像和资源上限。测试副本关闭宿主端口发布及自动重启，将网络设为 internal，额外启动一个仅在
内部网络运行的 Python HTTP 检查容器。不会读取原有 `.env`，不提供访问外部告警通道的网络路径。

验证流程为 promtool/amtool 原生配置检查、三个服务健康、自监控采集和 Watchdog 到达
Alertmanager、Grafana 登录权限/数据源/预置面板。随后写入自定义文件夹和 silence，停止
服务并执行本文的 tar 备份，将归档恢复到另一随机项目的空卷，核对恢复前时间点的指标、
自定义文件夹和 silence。

成功或失败后均尝试清理脚本创建的两组容器、网络和数据卷；这是对独立测试资源的定向
清理，不应把 `down --volumes` 用于正式项目。报告、日志、测试配置/秘密及备份归档留在
权限受限的 `.local` 目录供复核，不提交 Git。若报告显示 cleanup 失败，按报告中的随机
项目名核对残留资源。镜像缓存保留以供复测。

该验证不测试外部通知投递、真实生产负载、TLS 代理或跨主机灾备；Grafana 测试数据包括
自定义文件夹，不包含外部数据源秘密的解密验证。API 检查使用
[Grafana Folder API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/folder/)。

## 固定版本与文件

版本于 2026-09-26 按官方资料核查，未使用 `latest` 标签：

| 组件 | 固定镜像 | 依据 |
| --- | --- | --- |
| Prometheus | `prom/prometheus:v3.13.3` | [3.13.3 发布记录](https://github.com/prometheus/prometheus/releases/tag/v3.13.3) |
| Alertmanager | `prom/alertmanager:v0.34.1` | [0.34.1 发布记录](https://github.com/prometheus/alertmanager/releases/tag/v0.34.1) |
| Grafana OSS | `grafana/grafana:13.2.2` | [官方 Docker 下载](https://grafana.com/grafana/download?edition=oss&platform=docker) |

发布标签仍可能被重新推送。生产发布时应拉取镜像、检查目标 CPU 架构和漏洞扫描结果，再将 `.env` 中镜像值固定为批准的 `image:tag@sha256:...`；本仓库不填未经核实的摘要。修改版本后重新执行配置检查、通知测试和恢复演练。

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | 三个常驻服务与显式启用的 `volume-tools` 维护服务 |
| `.env.example` | 可提交的环境参数示例，无密码 |
| `prepare.py` | 通过仓库 runner 生成本地 `.env` 和强随机 Grafana secret，不覆盖已有文件 |
| `config/prometheus/prometheus.yml` | 自监控采集、规则加载及 Alertmanager 地址 |
| `config/prometheus/targets/external.yml` | 扩展 exporter 的 file discovery 列表，默认为空 |
| `config/prometheus/rules/platform.yml` | 采集失败、配置失败、通知失败等基础告警 |
| `config/alertmanager/alertmanager.yml` | 本地展示模式，默认不外发 |
| `config/alertmanager/alertmanager.webhook.example.yml` | 使用文件保存 webhook URL 的真实通知配置示例 |
| `config/grafana/provisioning/` | 自动配置 Prometheus 数据源和面板目录 |
| `config/grafana/dashboards/platform.json` | 服务可用性、采集耗时、进程内存和活跃时间序列面板 |

## 路径与执行前提

所有命令从仓库根目录执行，并经过 `bash .agent/run.sh ...`。使用受支持的 Docker Engine 与 Compose v2，并确认 Compose 支持 `up --wait`。配置解析不需要 Docker socket；拉取镜像、创建服务、容器内验证和备份操作需要 runner 的 `--docker`。这个开关将宿主 Docker socket 挂进工具容器，工具因此能管理该 daemon，只有执行这些运维命令时才使用。

`MONITORING_CONFIG_DIR` 必须是 **Docker daemon 宿主机上，本仓库 `compose/monitoring/config` 的真实绝对路径**。示例为 `/srv/platform/compose/monitoring/config`，表示仓库实际位于 `/srv/platform`。工具容器里的 `/workspace/compose/monitoring/config` 不能作为宿主 bind mount 源。

下面运行示例按 `/srv/platform` 部署目录编写。若仓库位于当前 Mac 上，应把所有 `--host-dir /srv/platform` 改为 `--host-dir /Users/elvinyao/workspace/repos/k8s-dev-test`，准备命令中的路径也改为 `/Users/elvinyao/workspace/repos/k8s-dev-test/compose/monitoring/config`。`--host-dir` 为工具容器提供同路径的只读访问，以便 Compose 读取 file secrets；daemon 仍从宿主原路径挂载配置。

本说明假定 runner 与服务使用同一台本地 Docker daemon。远程 daemon 需要先把配置和秘密安全部署到远程宿主，并重新确定客户端读取 file secrets 的路径方案，不能只修改 `DOCKER_HOST` 后照抄命令。

## 准备参数与秘密

先用不接触 daemon 的命令解析公开示例：

```sh
bash .agent/run.sh docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env.example -f compose/monitoring/compose.yaml config
```

此命令只检查 Compose 插值与结构，不证明 `/srv/platform` 路径、镜像、内部配置或服务启动有效。

确认仓库实际位置后，生成部署专用文件：

```sh
bash .agent/run.sh python compose/monitoring/prepare.py --host-config-dir /srv/platform/compose/monitoring/config
```

脚本只在当前仓库写入以下被 Git 忽略的内容，不启动服务，也不打印秘密：

- `.env`：复制公开示例并填写宿主配置路径。
- `config/secrets/grafana-admin-password`：首次初始化管理员的随机密码。
- `config/secrets/grafana-secret-key`：Grafana 数据库加密配置所需的稳定随机 key。
- `backups/`：用于后续停机备份的宿主目录。

secret 目录权限为 `0700`，文件为 `0444`：宿主通过私有父目录限制访问，挂载后的单个文件可被 Grafana 非 root 用户读取。普通 Compose 的 file secret 不提供通用的 uid/gid 重映射或静态加密，不能把它视为完整秘密管理服务。生产环境可由秘密管理工具准备同名文件，但必须同时保证宿主访问限制和容器 UID 的可读性。[Compose secrets 行为](https://docs.docker.com/reference/compose-file/services/#secrets)

脚本不会覆盖已有 `.env`、密码或 key。重新运行后若路径改变，需要手动编辑 `.env`。不要把密码填进 `.env`、命令行或 Git。通过受控的密码管理方式读取并保管管理员密码。

Grafana 使用官方支持的 `GF_SECURITY_ADMIN_PASSWORD__FILE` 与 `GF_SECURITY_SECRET_KEY__FILE`。管理员密码文件仅控制**新数据库的首次初始化**，更换文件不会自动更新已有用户密码；已有账号通过受控的 Grafana 管理操作轮换。加密 key 必须与数据库一起安全备份，不能每次部署重新生成。[Grafana Docker secrets](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/#configure-grafana-with-docker-secrets)

## 验证与启动

首先检查 `.env` 的路径、外部 URL、资源上限、数据保留时间及镜像版本。Prometheus 和 Alertmanager 的配置读取权限需允许镜像内非 root 用户访问；公开配置可使用 `0644` 文件和 `0755` 目录。

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml config --quiet
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml pull prometheus alertmanager grafana
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml run --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/prometheus/prometheus.yml
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml run --rm --no-deps --entrypoint /bin/amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
```

这些 `run --rm` 是短时配置检查，仍会使用 Docker daemon，并可能创建网络和数据卷；它们不启动常驻 Prometheus 或 Alertmanager。检查通过后再启动：

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml up -d --wait prometheus alertmanager grafana
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml ps
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml logs --tail 100 prometheus alertmanager grafana
```

本机访问 [Grafana](http://localhost:3000/)、[Prometheus targets](http://localhost:9090/targets) 和 [Alertmanager](http://localhost:9093/)。Grafana 使用 `.env` 的管理员用户名和 secret 文件中的密码，面板位于 `Platform` 文件夹。

实际验收至少包括：

1. 三个容器 healthy，Prometheus 三个默认 target 均为 UP。
2. Grafana 数据源查询成功，面板出现数据，`Watchdog` 告警在 Alertmanager 可见。
3. 配置通知后，暂停一个测试 target 或临时添加测试 exporter，验证 `MonitoringTargetDown` 触发和恢复均送达；完成后删除测试配置。
4. 重启服务后，历史指标、Grafana 用户与设置、Alertmanager silences 仍存在。
5. 完成一次下述恢复演练并记录允许的数据损失、实测恢复时间和未覆盖的故障。

## 接入节点、应用与 Kubernetes 指标

在 `config/prometheus/targets/external.yml` 中加入 Prometheus 容器实际能访问的 exporter 地址，例如已经独立部署并限制来源的 Linux node-exporter。该文件通过 file discovery 自动重读，不需要重启 Prometheus。新增带 TLS 或认证的 target 时，应在 `prometheus.yml` 添加独立 scrape job 并配置 `tls_config` / 文件凭据，不要为了接入方便关闭证书校验。

Docker Desktop 中 Linux 容器观察到的是 Linux VM 或容器环境，不能把其 `/proc` 数据当作 macOS 宿主指标。本配置故意不将宿主根目录和特权权限赋给 exporter；部署 node-exporter/cAdvisor 前应单独确认目标是 Linux 宿主、kind 节点还是容器，以及所需权限。[node-exporter 官方说明](https://github.com/prometheus/node_exporter)

Kubernetes 的 node-exporter、kube-state-metrics、kubelet/cAdvisor 指标需要集群内采集、认证和连通性配置。对于 Kubernetes 平台，优先使用相应的集群内 Prometheus 配置；本 Compose 栈可作为独立监控环境，但不会自动继承 kubeconfig 或服务发现权限。

## 真实通知配置

`alertmanager.webhook.example.yml` 是可用于通用 Alertmanager webhook 接收器的完整配置示例。上线时：

1. 由秘密管理工具在宿主 `config/alertmanager/secrets/webhook-url` 写入完整的 HTTPS 接收地址。文件不进入 Git；确保容器内 UID `65534` 可读，并限制宿主上的目录访问。
2. 将审核后的 webhook 示例内容替换 `alertmanager.yml`。接收端必须支持 Alertmanager webhook 的 JSON 格式；Slack、Teams 等服务可能需要对应原生配置或转换网关，不能直接把任意聊天机器人的 URL 填进去。
3. 执行 `amtool check-config`，然后重启 Alertmanager。
4. 验证 firing 和 resolved 两类通知。`Watchdog` 可用于确认通路，但单靠它在 UI 中显示，不证明外部通知正常；独立于此主机的 dead-man 检查才能发现整机失联。

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml restart alertmanager
```

本次配置编写不会发送通知。实际接收器、接收人和渠道由部署者配置与验收。[Alertmanager webhook 配置](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)

## 生产入口与访问控制

保留所有服务的回环绑定，并由独立受维护的 TLS 代理暴露必要入口。Grafana 使用专用域名，例如 `https://grafana.example.com/`，在 `.env` 中同步设置：

```dotenv
GRAFANA_ROOT_URL=https://grafana.example.com/
GRAFANA_DOMAIN=grafana.example.com
GRAFANA_COOKIE_SECURE=true
```

代理需支持 Grafana WebSocket，验证允许的域名，正确传递 `Host` 和 `X-Forwarded-Proto`，维护有效证书，并对公网管理入口配置身份验证、访问限制和审计。Grafana 的 `enforce_domain` 保持关闭，保证内部 `grafana:3000` 指标采集不会被重定向；公网 Host 校验由代理承担。生产不直接公开无认证的 Prometheus 或 Alertmanager API；需要远程访问时另配认证代理或受控运维通道。

如果代理运行于宿主网络，其上游可以使用宿主 `127.0.0.1:3000`。如果代理也是容器，代理内的 `127.0.0.1` 指向代理自身；应通过审核后的 Compose override 只把 Grafana 接入共享代理网络，并使用 `grafana:3000` 上游。不要通过改成 `0.0.0.0` 暴露所有管理端口来解决连通性。

HTTP 本地开发时 `GRAFANA_COOKIE_SECURE=false`；启用生产 TLS 后必须改为 `true`。Grafana 数据源使用 Compose 私有网络访问 Prometheus。默认 bridge 网络允许对外访问，以支持外部 scrape 和通知；网络成员都应被视为能访问内部指标的受信服务。

## 容量、日志与配置变更

默认运行上限合计为 Prometheus 2 GiB、Grafana 1 GiB、Alertmanager 256 MiB，CPU 上限分别为 2、1、0.5。它们是示例限制而非可保证的容量；实际需求取决于时间序列数量、采集频率、查询并发和告警规模。根据 OOM、CPU throttling、采集耗时和 TSDB 指标调整，不把持续重启当作可用运行。

Prometheus 默认保留 15 天或约 10 GB 存储块，先达到者生效。该数值不是磁盘配额，WAL、head chunks 与 compaction 仍需额外空间；底层磁盘必须另有容量告警和至少相应的安全余量。named volume 不自动限制大小。使用可靠的本地 POSIX 文件系统，避免将本地 TSDB 放在 NFS 上。[Prometheus 存储说明](https://prometheus.io/docs/prometheus/latest/storage/)

所有服务仅写控制台日志，Docker 使用 `json-file`，每个日志文件最多 10 MB、保留 5 个。应用数据保留与 Docker 日志轮转是独立策略。默认没有宿主磁盘 exporter，因此这份基线不能检测所有宿主磁盘耗尽情况，正式运行需补齐宿主监控。

Prometheus 的 HTTP reload/admin API 默认未开启。修改采集配置或规则后先执行 `promtool check config`，通过后重启；修改 Grafana provisioning 后重启 Grafana，面板 JSON 则由 provider 定期重读。变更 `.env` 需要 `up -d` 重建服务，单纯 `restart` 不会重新载入 Compose 环境变量。

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml restart prometheus grafana
```

## 停机备份

备份包含三份 named volume，以及另外安全保管的配置、`.env` 和 secrets。Grafana SQLite 应停止写入后备份；Prometheus 的 WAL 与 blocks 也应保持一致。本目录采用停机备份，故有监控中断窗口，应提前安排。[Grafana 备份说明](https://grafana.com/docs/grafana/latest/administration/back-up-grafana/)

先停止三个写入者，再由显式维护 profile 打包。以下文件名是示例，实际每次使用唯一的备份编号；命令拒绝覆盖已有同名归档。

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml stop prometheus alertmanager grafana
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml --profile tools run --rm --no-deps volume-tools -ec 'umask 077; test ! -e /backups/monitoring-backup-001.tar.gz; tar -czpf /backups/monitoring-backup-001.tar.gz -C /volumes prometheus alertmanager grafana; tar -tzf /backups/monitoring-backup-001.tar.gz >/dev/null; sha256sum /backups/monitoring-backup-001.tar.gz'
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml up -d --wait prometheus alertmanager grafana
```

归档写入宿主 `compose/monitoring/backups/`，不会打包源码目录或 secret 文件。`volume-tools` 没有网络、只在指定 profile 下运行，并仅挂载数据卷与备份目录。维护时不能同时执行其他启动服务的操作。若归档失败，保留错误现场，检查磁盘空间后决定重试或恢复服务；不要把失败的归档标记为可恢复备份。

将归档及校验值加密复制到独立故障域，并通过秘密管理系统单独备份 Grafana 加密 key、管理员恢复资料和通知凭据。只有宿主本地的 `backups/` 无法应对整机或磁盘损坏。保留对应 Git commit、镜像摘要、`.env` 和备份时间。

## 恢复演练与回滚

恢复优先使用新的 Compose 项目名创建空卷，保留原始卷和原始备份。不要在有数据的目标卷上直接解压覆盖。以下以 `platform-monitoring-restore` 为隔离项目；还应在独立宿主或独立 `.env` 中使用其他端口和域名，防止与原服务冲突。

1. 将校验通过的归档放入同一备份目录，恢复对应版本的配置和 secret，尤其是原来的 Grafana 加密 key。
2. 确认恢复项目没有正在运行的服务，检查归档内容与预期一致；只恢复自己可信且验证过的归档。
3. 使用以下命令创建并恢复**空**卷；三个目录中任何一个非空都会停止操作。

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env -f compose/monitoring/compose.yaml --project-name platform-monitoring-restore --profile tools run --rm --no-deps volume-tools -ec 'test -z "$(ls -A /volumes/prometheus)"; test -z "$(ls -A /volumes/alertmanager)"; test -z "$(ls -A /volumes/grafana)"; tar -xzpf /backups/monitoring-backup-001.tar.gz -C /volumes'
```

4. 准备恢复环境专用、被忽略的 `.env.restore`：改用未占用的端口、域名与 external URL，并确认不会向正式值班通道重复发送告警。然后使用同一个恢复项目名启动：

```sh
bash .agent/run.sh --docker --host-dir /srv/platform docker compose --project-directory compose/monitoring --env-file compose/monitoring/.env.restore -f compose/monitoring/compose.yaml --project-name platform-monitoring-restore up -d --wait prometheus alertmanager grafana
```

5. 核对历史指标、Grafana 登录和面板、数据库凭据解密、Alertmanager silence 与通知设置，记录恢复时间；验证通过后再安排正式切换。

Compose 项目名决定 named volume 的命名。无意改变项目名会创建一组新卷，看起来像“数据消失”；先核对项目和卷，不要立即初始化或删除。正常停止/移除容器保留卷，**不要使用 `down -v` 删除持久数据**。

升级前先完成备份与发布记录；回滚不保证只需换回旧镜像，尤其是 Grafana 数据库迁移和 Prometheus 存储格式变更。需要按组件升级说明，在隔离环境中恢复升级前备份后验证旧版本。

## 单机生产边界

在接收器、TLS、身份权限、容量、备份和恢复验收完成后，这份配置可作为明确接受单机故障风险的部署基础。它没有跨主机复制、自动灾备、Prometheus 长期存储、Grafana 外部数据库、Alertmanager HA、外部独立探测或完整 Kubernetes 服务发现。

需要更高可用性时，应分别规划跨故障域 Prometheus/远程写入存储、Grafana 外部 PostgreSQL 与多实例、Alertmanager 集群，以及位于本监控故障域之外的健康检查。不能仅把 Compose 的实例数增加为 2 就获得这些能力。
