# Argo CD 自举入口

固定使用 `v3.5.3` 官方 non-HA 安装清单；适合本地验证。此目录尚未应用到集群。
namespace 固定为 `argocd`，与上游 ClusterRoleBinding 中的引用保持一致。

后续 runner 具备 kubectl、集群连接与上下文保护后，再以 server-side apply 安装并等待
CRD、controller、repo-server 和 server Ready。现在的校验仅检查本地文件，
不会下载或渲染远程安装清单，也不代表安装已通过验证。

自举成功后再配置仓库认证，并按顺序安装 `argocd-sys-settings` 中的 AppProject 和
Application。凭据从运行时 Secret 或后续的秘密管理方案注入，不写进 Git。
bootstrap Git 源必须在集群内 GitLab 不可用时仍可读取。

Argo CD 自管理、SSO/RBAC、TLS、备份和升级回滚将在后续实现；初始管理员账户仅用于自举。

来源：[Argo CD v3.5.3](https://github.com/argoproj/argo-cd/releases/tag/v3.5.3)、
[官方安装说明](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)。
