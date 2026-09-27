# GitLab 单机部署与运维

本目录提供独立于 Kubernetes 的 GitLab Compose 实例，适合本地平台演练或允许停机维护的
小型单机部署。Git 仓库和恢复资料位于集群外，可避免集群损坏时同时失去 GitOps 源。
它包含 PostgreSQL、Redis、Gitaly 等服务，宿主故障会导致整套 GitLab 不可用；没有实现 HA。
正式上线前需要自行完成负载验证、备份恢复演练、监控告警和维护窗口安排。

## 版本与机器

2026-09-26 核验的默认值为 GitLab EE `19.4.1-ee.0`（固定多架构 index digest）和
Runner `alpine-v19.4.1`。均来自 GitLab 官方仓库：[GitLab 镜像](https://hub.docker.com/r/gitlab/gitlab-ee/tags)、
[Runner 镜像](https://hub.docker.com/r/gitlab/gitlab-runner/tags?name=alpine-v&page=1)。
运行前复核对应补丁公告；修改版本时同时更新 digest，不能只改 tag。EE 镜像可以运行 Free 层，
付费能力是否可用取决于许可证，本方案不替代许可证。

建议使用专用 Linux 宿主；本机 macOS Docker Desktop 仅作演练。
GitLab 官方 20 RPS / 1,000 用户单机参考为 **8 vCPU、16 GB RAM**，且不提供 HA；
本模板以此设置 GitLab 容器上限，宿主还需给操作系统和代理留余量。
CI 工作负载另算，不能把该数值当作 GitLab、Kubernetes、ELK 等全部服务的总预算。
[官方单机参考架构](https://docs.gitlab.com/administration/reference_architectures/1k_users/)。

持久数据使用可靠 SSD 和受监控文件系统。初始容量由仓库、数据库、LFS、制品增长决定，
小型试运行可从 200 GiB 数据盘起测，保留至少 30% 余量；这是本项目的容量起点，不是官方保证。
`config` 存储密钥与配置；`data` 存储数据库和仓库；`logs` 独立限额；`backups` 使用独立盘。
备份时还需要临时空间，`STRATEGY=copy` 尤其如此。磁盘告警应覆盖容量、inode、延迟和失败备份。
外部对象存储、Registry、Pages、SMTP、SSO 尚未配置；按真实需求单独加入并验证。

## 首次配置与启动

1. 使用编辑器将 `compose/gitlab/.env.example` 复制为同目录 `.env`；后续运行只使用 `.env`。
   填写真实 `GITLAB_HOSTNAME` 和 `https://真实域名` 的 `GITLAB_EXTERNAL_URL`。
   DNS 必须从用户、Runner 和 Kubernetes job Pod 均可解析、访问；不要使用 `localhost`。
2. 由宿主管理员或基础设施供应流程预先创建 `.env` 中的绝对路径，建议全在 `/srv/gitlab` 下。
   不要把 `/workspace/...` 当宿主路径。工具容器的 `/workspace` 与 daemon 的宿主文件系统不同。
   本模板设置 `create_host_path: false`，路径未准备好时会失败，而不是悄悄创建空目录。
3. 按 `compose/gitlab/secrets/README.md` 供应初始 root 密码文件；配置目录和秘密目录
   仅管理员可访问。不要对 GitLab 数据目录盲目统一 `chown`；容器初始化会配置内部服务权限。
4. 在外部代理安装有效 TLS 证书，按下一节配置转发。生产防火墙只开放 HTTPS 和选定的 SSH
   端口；HTTP 后端端口只能被代理访问。单独配置 SMTP 后验证通知和密码重置邮件。

配置校验不连接 daemon；它只能证明 Compose 能解析，不能证明镜像、DNS、权限或磁盘可用：

```sh
bash .agent/run.sh docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env.example -f compose/gitlab/compose.yaml config
```

以下及全文运行命令都在仓库根执行。`--docker` 把 Docker socket 交给工具容器，
`--host-dir /srv/gitlab` 只读映射同名路径供 Compose 读取 secret 文件；服务自身的 bind mount
由 daemon 管理，仍按 Compose 声明读写。更换存储根目录时同步替换此参数。

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml config --quiet
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml pull gitlab
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml up -d gitlab
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml ps
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml logs --tail=100 gitlab
```

等待健康状态通过后访问真实 HTTPS 域名，使用 root 与秘密文件中的初始密码登录，立即更换密码、
启用 2FA、关闭无审核的开放注册，创建个人管理员账号。验证 HTTPS 与 SSH clone/push、创建项目
及密码重置邮件。日志可能含初始化信息，排障时只向授权管理员提供必要片段。

## TLS、代理和 SSH

`compose/gitlab/nginx.conf.example` 用于**已有宿主 NGINX** 的 `http {}` 上下文，替换域名、
证书路径和后端端口。它将 TLS 终结在代理，GitLab 内部 HTTP 监听 80，宿主仅发布 `127.0.0.1:8929`。
若代理在另一台机器上，改为后端专用 IP 并由防火墙仅允许代理访问；若代理也是容器，不能使用
代理容器自己的 `127.0.0.1`，需要配置实际可达的宿主后端地址或受控共享网络。

`GITLAB_TRUSTED_PROXIES` 只填 GitLab 实际看到的代理来源 IP/CIDR，注意 Docker NAT，
不要填 `0.0.0.0/0`。外部代理覆盖来自用户的转发头，并传递 Host、HTTPS scheme 和客户端地址。
GitLab `external_url` 始终是用户访问的 HTTPS URL，即使后端是 HTTP。
19.4 模板使用 `gitlab_rails['nginx'][...]` 配置应用 NGINX。
[GitLab NGINX 与外部 TLS 官方说明](https://docs.gitlab.com/omnibus/settings/nginx/)。

SSH 走独立 TCP 连接，不经过 HTTP 反向代理。默认只发布到宿主回环地址；供远端使用时将
`GITLAB_SSH_BIND` 改为指定可达 IP，并在防火墙放行 `GITLAB_SSH_PORT`（示例 2222）。
该值也用于生成 clone URL；如前置 NAT 使用不同公网端口，须同步修改发布与广告端口设计。
如果继续使用回环绑定，远端 SSH clone 不会成功。

## 独立 Runner：Kubernetes executor

`runner` profile 默认不启动；既不挂 Docker socket，也不使用 privileged。
Runner manager 可以与 GitLab 在同一 Docker 宿主运行，但 CI jobs 调用 Kubernetes API
创建在独立 `ci-jobs` 命名空间。正式平台可将 manager 迁入集群，由投射 ServiceAccount token
自动轮换。高风险或不可信项目应使用独立 runner、节点/集群和网络边界。

1. 管理员准备受限 RBAC。示例 `runner/rbac.yaml.example` 只有 `ci-jobs` 内的 Role，
   manager 可管理 job Pod 和临时 Secret，没有集群级授权；job 自身的 SA 没有 RoleBinding。
   不要在 `ci-jobs` 放生产凭据。通过管理员 Kubernetes 上下文审核并应用该示例：

```sh
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -f compose/gitlab/runner/rbac.yaml.example
```

2. 在 GitLab UI 创建项目或组 Runner，配置允许的项目、标签、保护分支及是否接受无标签作业。
   获取 **Runner authentication token (`glrt-...`)**，不使用旧 registration token。
3. 准备 `GITLAB_RUNNER_CONFIG_DIR` 后运行注册，按交互提示输入真实 URL、token、名称及
   `kubernetes` executor。token 不写命令行、Git 文件或终端日志：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml --profile runner run --rm runner register
```

4. 在私有宿主 `runner/config.toml` 中保留注册生成的 token 与身份信息，将
   `config.toml.example` 的限制合并到已有 `[[runners]]`，不要重复追加第二个 runner。
   文件仅管理员可读写；本模板无特权容器，依赖 Docker-in-Docker 的流水线需要另行设计。
5. 将 `kubeconfig.yaml.example` 配置到私有 `runner/kubernetes/config`，供应真实 API URL、
   CA 到 `ca.crt`、`gitlab-runner-manager` 的有效 SA token 到 `token`。不可复制 cluster-admin
   kubeconfig。管理员通过 TokenRequest 签发短期 token，并在过期前由秘密供应系统轮换文件；
   此 Compose 不承担签发与续期，未配置续期的 runner 会在 token 到期后停止接单执行。
   不关闭 API TLS 验证。若 GitLab 使用私有 CA，还需给 manager 和 job/helper 配置信任链。
6. 验证 RBAC、DNS、CA 和网络后启动：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml --profile runner up -d runner
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml --profile runner exec -T runner gitlab-runner verify
```

验证一个真实测试 job 的 clone、日志、制品上传与清理。`verify` 仅检查 Runner 与 GitLab 连接，
不证明 Kubernetes 权限或整个 job 可用。按业务加 ResourceQuota、LimitRange 和网络出站白名单，
保留示例禁止 namespace/SA/token 覆盖的限制；新增 executor 功能时重新审查所需 RBAC。
[官方注册流程](https://docs.gitlab.com/runner/register/)、
[Kubernetes executor 配置与权限](https://docs.gitlab.com/runner/executors/kubernetes/)。

## 备份

先暂停接收新 job，并冻结 Git 写入/维护入口以取得一致恢复点；等运行中的任务结束。
备份包括两份独立材料：应用数据与 `/etc/gitlab` 配置/密钥。下面分别生成它们：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-backup create STRATEGY=copy
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-ctl backup-etc
```

应用归档位于 `GITLAB_BACKUPS_DIR`；配置归档位于 `GITLAB_CONFIG_DIR/config_backup`，
含 `gitlab-secrets.json`。Compose 注入的 `GITLAB_OMNIBUS_CONFIG` 不会写回 `gitlab.rb`，
所以还要独立保存已部署的 Compose、私有 `.env`、镜像版本/digest、代理配置和证书。
初始密码、Runner 凭据也按各自秘密策略备份。异地副本加密存储，配置密钥与应用备份分开授权。
本地保留 7 天只是清理窗口，不是异地备份策略；安排每日备份、失败告警和定期恢复演练。
如启用对象存储，还要另外备份该存储，不能只保留 GitLab tar。
[官方应用备份](https://docs.gitlab.com/administration/backup_restore/backup_gitlab/)、
[配置与密钥备份](https://docs.gitlab.com/omnibus/settings/backups/)。

## 恢复演练与灾难恢复

在隔离宿主或独立目录恢复，确保实例不接收生产流量；流程会覆盖目标数据库。
`.env` 固定为备份时**完全相同的版本与 CE/EE 类型**，准备新的空数据目录及匹配配置。
将已解密的配置归档供应到新 `config/config_backup/CONFIG_BACKUP.tar`，应用归档供应到
新 `backups/BACKUP_ID_gitlab_backup.tar`；下面两个大写名称必须替换成实际文件名/备份 ID。
恢复配置归档前先检查其成员，确认来自可信备份且路径均属于预期 GitLab 配置：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml run --rm --no-deps --entrypoint tar gitlab -tf /etc/gitlab/config_backup/CONFIG_BACKUP.tar
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml run --rm --no-deps --entrypoint tar gitlab -xf /etc/gitlab/config_backup/CONFIG_BACKUP.tar -C /
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml up -d gitlab
```

等初始化完成并确认恢复了原 `gitlab-secrets.json`，停止数据库客户端，保留 PostgreSQL/Redis：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-ctl stop puma
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-ctl stop sidekiq
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-ctl status
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab chown git:git /var/opt/gitlab/backups/BACKUP_ID_gitlab_backup.tar
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-backup restore BACKUP=BACKUP_ID
```

核对还原提示后确认覆盖。恢复完成再重启和检查：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml restart gitlab
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-rake gitlab:check SANITIZE=true
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-rake gitlab:doctor:secrets
```

恢复后再次等待健康，抽查用户/2FA、项目 Git push/pull、LFS、制品与 CI 变量解密。
外部对象存储等独立备份需同期恢复；检验完成才能切换入口。
记录实际 RPO/RTO，不能用「备份命令成功」代替恢复验证。
[官方恢复前提与 Docker 流程](https://docs.gitlab.com/administration/backup_restore/restore_gitlab/)。

## 升级与回退

1. 查询[官方升级路径](https://docs.gitlab.com/update/upgrade_paths/)，从当前精确版本规划每个必经版本，
   在隔离恢复副本中先演练。大版本/次版本不能直接跳到任意新镜像。
2. 冻结写入并完成上述备份；保存旧镜像 digest、配置和 `.env`。检查所有后台迁移完成：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml exec -T gitlab gitlab-rake gitlab:background_migrations:list
```

3. 在私有 `.env` 改为下一站已批准的 tag/digest，执行配置校验，再拉取并更新 GitLab：

```sh
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml config --quiet
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml pull gitlab
bash .agent/run.sh --docker --host-dir /srv/gitlab docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env -f compose/gitlab/compose.yaml up -d gitlab
```

4. 等健康及后台迁移完成，做 clone/push/CI 冒烟检查，保存该站备份后再进行下一站。
   Runner 单独更新到兼容固定版本，重复 `--profile runner pull runner` 与 `up -d runner`。
5. 回退需要恢复升级前的匹配数据与配置。不能仅把镜像降版并复用已迁移数据库。
   如已发生新写入，先确定可接受的数据损失与恢复点，再按恢复流程切换。

[Docker 升级流程](https://docs.gitlab.com/update/docker/)、
[后台迁移检查](https://docs.gitlab.com/update/background_migrations/)。
