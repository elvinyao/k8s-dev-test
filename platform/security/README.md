# 权限与秘密管理规划

状态：规划。实施 SSO/OIDC、Argo CD RBAC、受限 AppProject、Kubernetes RBAC 与
ServiceAccount 权限；平台配置修改者视为管理员，业务仓库不得获得同等权限。

秘密管理方案后续选择 SOPS/外部密钥系统或 External Secrets，并明确密钥轮换与恢复。
不要把 base64 编码视为加密。Kubeconfig、token、私钥、真实 Secret 不得提交。

Pod Security、NetworkPolicy、镜像来源、资源配额和审计需逐项落地并验证。
不可把目录结构或未经运行验证的清单当成安全边界。
