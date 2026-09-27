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
