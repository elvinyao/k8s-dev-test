# GitLab Compose 部署入口

`compose.yaml` 是可配置的单机 GitLab 服务；`runner` profile 独立启用。
未创建容器、账号、Runner 或数据目录。完整步骤见
[GitLab 部署与运维](../../docs/gitlab-production.md)。

只解析配置，不连接 Docker daemon 或部署服务：

```sh
bash .agent/run.sh docker compose --project-directory compose/gitlab --env-file compose/gitlab/.env.example -f compose/gitlab/compose.yaml config
```

运行前使用编辑器从 `.env.example` 创建本目录 `.env`，填写真实域名和宿主绝对路径。
`*.invalid` 与 `192.0.2.0/24` 均为示例地址，不能直接运行。运行命令改用 `.env`，
并仅在需要连接 daemon 时加入 `--docker --host-dir /srv/gitlab`；该授权只给工具容器，
不会给 GitLab 或 Runner 自动挂载 Docker socket。

## 使用镜像内的真实配置解析器

下面的命令只使用 `.env.example`，在独立临时 Compose 项目中运行 GitLab 镜像自带的
Ruby 和 `Gitlab.from_file` 配置加载器。它检查 Omnibus Ruby 语法、环境变量和文件读取，
并将 12 个配置选项与该镜像内的 `gitlab.rb.template` 比对，防止嵌套选项拼写错误被忽略。
另外运行三个失败用例，确认无效 Ruby、错误选项名称和非整数 SSH 端口会被拒绝。

```sh
bash .agent/run.sh --docker --toolbox python compose/gitlab/scripts/validate-config.py
```

首次运行可能下载较大的固定 digest GitLab 镜像。验证容器限制为 1 CPU / 512 MiB，
使用非 root 用户、只读根文件系统、禁用网络、无发布端口；不会启动 GitLab、PostgreSQL、
Redis、Runner，不会执行 `reconfigure` 或数据库迁移，也不挂载部署数据目录和真实秘密。
`config-check.compose.yaml` 是独立验证作业，不能作为生产 Compose 的 overlay 合并使用。

结果写到 `.local/gitlab-config-check-<随机ID>/report.json` 和 `config-check.log`。
报告记录源文件 SHA256、实际镜像身份/架构、嵌入模板哈希和清理结果。脚本只清理自身创建的
随机项目；失败时按日志处理，不要对生产项目执行 `down --volumes`。
详见[验证证据与验收边界](validation.md)。
