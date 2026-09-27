# 配置验证记录

验证日期：2026-09-27。以下是当前工作区配置的离线检查结果；尚无发布 commit，
本记录不代表任何环境已经部署或通过生产验收。修改配置后需重新执行对应检查。

| 检查 | 结果与范围 |
| --- | --- |
| 源文件检查 | 12 个 Python 文件语法、4 个 shell 文件语法、16 个本地文档链接通过 |
| YAML 静态检查 | 65 个文件、91 个文档、19 个本地资源引用通过 |
| Compose 模型解析 | GitLab 2、Logging 5、Monitoring 4 个服务通过；包含可选 profile，共 11 个服务 |
| 生产 Helm lint/render | 6 个 chart，共 326 个对象通过 |
| 本地 Helm lint/render | 5 个 chart，共 283 个对象通过；不包含 GitLab |
| Kustomize | 7 个入口、49 个对象通过，包含 local 与 production 两套日志配置 |
| 自定义资源 schema | 23 个对象通过所渲染 chart 提供的 CRD schema；跳过 26 个内置对象 |
| GitOps 生成 | 示例生成 1 个 AppProject 与 7 个 Application；默认无自动同步 |
| 输入拦截 | 生产模式拒绝示例仓库地址；生产 preflight 对未替换的环境占位符返回失败 |

执行入口和命令见 [容器工具链](tooling.md)。Helm 归档校验值及对象数量保存在
忽略提交的 `rendered/production/summary.json` 和 `rendered/local/summary.json`；
发布时应将其与实际 Git commit 一起归档到受控的发布记录中。

## 检查边界

- Compose 检查只解析配置并检查资源约束等规则，未启动这些服务。
- Helm 与 Kustomize 渲染未调用 Kubernetes API，不验证调度、CSI、网络或实际控制器行为。
- CRD 检查不包含 Kubernetes 内置 schema、CEL、admission webhook 和运行期约束。
- 原始 Argo CD bootstrap 引用的远程 manifest 未在该静态检查中展开；已验证的是 Helm 路径。
- preflight 的占位符拦截是预期保护行为。示例当前不能不经环境配置直接用于生产。

## 目标环境验收

按照 [生产部署手册](production.md) 填入域名、固定 Git commit、凭据、CSI 与外部后端，
逐层部署并记录以下证据：

1. 节点、DNS、负载均衡、证书链、NetworkPolicy 与 PVC 供给正常。
2. Argo CD SSO/权限、监控采集和告警 firing/resolved 投递成功。
3. 容器日志经 Filebeat/Logstash 到达 Elasticsearch，保留策略及磁盘告警生效。
4. GitLab clone/push、CI、制品及启用时的 Registry 可用，外部依赖连接受保护。
5. 节点故障、凭据和证书轮换、升级及独立环境恢复演练通过，记录实测 RPO/RTO。

本地 kind 全流程、实际服务启动和上述验收尚未执行。自建生产节点的安装与升级自动化
也不在当前配置范围内；生产手册以已有多节点 Kubernetes 集群为前提。
