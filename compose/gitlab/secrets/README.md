# 秘密文件约定

不在仓库中放置真实密码。本目录只保存说明；生产文件通过管理员的秘密管理流程
供应到 `.env` 中的宿主绝对路径。

- `GITLAB_ROOT_PASSWORD_FILE`：UTF-8 单行随机初始 root 密码，至少 20 个字符；
  文件属主 root、权限 `0600`，父目录 `0700`。Compose 只读挂载至
  `/run/secrets/gitlab_root_password`，GitLab 配置使用官方支持的 `File.read` 初始化密码。
  不把密码放入 `.env` 或 `GITLAB_OMNIBUS_CONFIG` 字符串。
- 这不是加密秘密存储：普通 Compose 的 file secret 是宿主文件挂载，宿主管理员仍可读取。
- 首次初始化后在 GitLab 更换 root 密码并启用 2FA；这个文件不会重置已存在的 root 账号。
  当前 Compose 每次启动仍需读取它，可保留受控文件，或在完成初始化后一起移除配置行和 secret 挂载。
- `/etc/gitlab/gitlab-secrets.json` 包含数据加密密钥，与初始 root 密码用途不同。
  必须单独加密备份；不能靠 root 密码重建它。
- Runner 的 `config.toml` 含 `glrt-` 身份验证 token；其 kubeconfig/token 文件、SMTP
  密码和 TLS 私钥都通过秘密管理系统供应、限制读取与定期轮换，不提交到 Git。

实现依据：[GitLab Docker secret 示例](https://docs.gitlab.com/install/docker/installation/#install-gitlab-by-using-docker-swarm-mode)。
