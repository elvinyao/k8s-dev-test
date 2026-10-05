# GitLab 配置验证与投产验收

## 已验证范围

2026-10-05 在 OrbStack 提供的 Docker 原生 `linux/arm64` 环境中，使用
`gitlab/gitlab-ee:19.4.1-ee.0@sha256:803e5887cd561ed49ce84c92756838d39e9bcef5c061d4399f2e1057a335cae2`
执行镜像内的 Ruby / `SettingsDSL` 配置检查。registry index 同时包含 `linux/amd64`
和 `linux/arm64`；amd64 本轮没有执行。

检查直接读取 `compose.yaml` 的 `GITLAB_OMNIBUS_CONFIG`，用公开示例环境变量和测试密码文件
求值。真实配置加载器接受 12 个赋值选项；检查覆盖 HTTPS 外部 URL、SSH 广告端口、
内部 HTTP 监听、代理来源、root 密码文件读取、备份目录及保留期，另确认镜像内包含 healthcheck。
选项还必须存在于该镜像的配置模板中，因为 Ruby DSL 本身不会拒绝所有嵌套拼写错误。

GitLab 19.4 示例保留 `gitlab_rails['nginx'][...]` 写法。版本对应的
[官方配置模板](https://gitlab.com/gitlab-org/omnibus-gitlab/-/blob/19.4.1%2Bee.0/files/gitlab-config-template/gitlab.rb.template)
和[官方 NGINX 配置说明](https://docs.gitlab.com/omnibus/settings/nginx/) 支持此结构。
验证读取的是镜像内模板，外部文档链接不是自动验证的输入。

以下命令重跑检查；每次生成自己的报告，实际成功或失败以该次报告为准：

```sh
bash .agent/run.sh --docker --toolbox python compose/gitlab/scripts/validate-config.py
```

## 验证没有覆盖的部署条件

| 条件 | 上线前需取得的证据 |
| --- | --- |
| 主机资源及数据盘 | 按负载规划 CPU/RAM/IOPS/容量；完整初始化、健康检查及压力测试通过 |
| 域名、TLS、代理 | 真实 DNS、完整可信证书链；公网 HTTPS clone/push；来源 IP、重定向正确 |
| 数据持久化权限 | 专用 Linux 主机上的 config/data/logs/backups 路径与权限通过真实启动验证 |
| SSH | 防火墙、NAT 与公开广告端口一致；外部客户端 SSH clone/push 通过 |
| 邮件与身份 | SMTP 密码重置/通知到达；2FA、关闭开放注册、管理员权限及需要的 SSO 完成验收 |
| Runner | 真实 token、可轮换的 Kubernetes 凭据、RBAC、网络/CA；完整 clone→job→制品→清理通过 |
| 数据保护 | 异地加密备份含应用数据与独立配置密钥；全新目标恢复并记录 RPO/RTO |
| 升级 | 官方必经升级版本、后台迁移及恢复回退在隔离副本验证 |

当前单机 Compose 内置 PostgreSQL、Redis、Gitaly，没有主机级高可用性。
SMTP、SSO、外部对象存储、Registry、Pages 需按所需功能增加配置；这些能力没有因本次
配置解析通过而启用。需多节点 GitLab 时使用[生产 Helm 路径](../../platform/gitlab/README.md)，
其外部 PostgreSQL/Redis/Gitaly/S3/TLS/凭据同样必须先供应和验收。

此检查不生成 Chef 最终配置、不执行 reconfigure/迁移、不访问真实后端，也不证明 GitLab
已经可以启动或恢复。完整运行与恢复步骤见[部署运维手册](../../docs/gitlab-production.md)。
