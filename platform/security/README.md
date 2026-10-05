# 权限、秘密与证书示例

已有配置：Argo CD OIDC/RBAC、受限业务 AppProject、`apps-prod.yaml` 的配额/网络策略，
以及 cert-manager values、Cloudflare ClusterIssuer 和公共证书示例。使用顺序见
[生产部署手册](../../docs/production.md)。这些配置未在真实集群完成安全验收。

平台配置修改者视为管理员，业务仓库不得获得同等权限。Argo CD server 的入口策略需在
安装前应用；检查所有选中同一 Pod 的 NetworkPolicy，额外放行策略不会被另一条拒绝策略抵消。

秘密管理方案后续选择 SOPS/外部密钥系统或 External Secrets，并明确密钥轮换与恢复。
不要把 base64 编码视为加密。Kubeconfig、token、私钥、真实 Secret 不得提交。

Pod Security、NetworkPolicy、镜像来源、资源配额和审计需在目标环境逐项验证。
不可把目录结构或未经运行验证的清单当成安全边界。
