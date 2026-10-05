# 容器工具链

宿主只检查/编辑源码，全部项目命令以 `bash .agent/run.sh` 开始。默认镜像是
Python 3.13 Bookworm；`docker` 命令选用固定 Docker CLI 镜像；`helm`、`kubectl`、`kind`
选用本仓库工具箱。默认不挂载 Docker socket、宿主 kubeconfig 或用户凭据。

## 构建与验证

```sh
bash .agent/run.sh --docker docker build -t local-platform/toolbox:2026-09-26 -f .agent/Dockerfile .
bash .agent/run.sh --toolbox python scripts/verify.py
```

`verify.py` 顺序执行源码、YAML、回归测试、Compose、两套 Helm、Kustomize、CRD、业务项目权限和组件
衔接检查。它不需要 Docker socket 或 kubeconfig，不部署服务。首次下载 chart 需要网络，
缓存完整后可离线使用。任一步失败即停止；新运行先作废旧的成功状态。报告记录 Git commit、
工作区是否有修改、源码内容 SHA256 和逐项日志，写入 `rendered/verification/report.json`。
检查过程中修改源码会使该次报告失败，需等待修改完成后重跑。

定位问题时可单独执行：

```sh
bash .agent/run.sh --toolbox python scripts/check-source.py
bash .agent/run.sh --toolbox python -m unittest discover -s scripts/tests -v
bash .agent/run.sh --toolbox python scripts/validate.py
bash .agent/run.sh --toolbox python scripts/validate-compose.py
bash .agent/run.sh --toolbox python scripts/render.py --environment local
bash .agent/run.sh --toolbox python scripts/render.py --environment production
bash .agent/run.sh --toolbox python scripts/validate-kustomize.py
bash .agent/run.sh --toolbox python scripts/validate-crds.py
bash .agent/run.sh --toolbox python scripts/validate-integration.py
bash .agent/run.sh --toolbox python apps/production-demo/check.py --example
```

工具箱固定 Helm 3.22.0、kubectl 1.35.8、kind 0.33.0；下载二进制时校验上游 SHA256。
首次构建/下载 chart 需要网络。`.dockerignore` 仅允许工具构建所需文件，不打包秘密与数据。
Helm 下载位于 `.cache/charts/`，渲染输出位于 `rendered/`，均不提交。
`platform/releases.yaml` 固定版本、归档 SHA256 和管理方式。渲染前强制核对摘要，缓存
归档变化会失败；不会自动修改批准值。升级 chart 时，审核新归档来源/内容后再更新版本与
摘要。现有摘要锁定的是已检查归档的身份，不提供发布者签名验证。
渲染摘要同时记录输出 manifest SHA256；下游检查拒绝缺失或改变的结果。单组件渲染会使
整套摘要失效，之后先完整渲染再运行 CRD/组件衔接检查。

该摘要检查作用于本地工具链。Argo CD 仍按 repository/version 获取 chart，不会执行
`render.py`；不能把本地校验表述为 Argo CD 下载验证。生产发布仍需审核实际镜像摘要和
仓库供应流程。本地使用 Helm 3.22.0；Argo CD 自带渲染器的实际结果需在部署前比较。
CRD 校验读取这些固定 chart 中的 OpenAPI schema；它不执行 CEL、admission webhook，
不校验 Kubernetes 内置资源，也不证明控制器实际能启动服务。
组件衔接检查对照 AppProject 与实际渲染资源、检查管理权归属和 Argo CD server 的叠加
NetworkPolicy，并渲染 GitLab Registry 开/关两种配置；不证明网络策略已由 CNI 执行。

## 运行层验证

完整离线命令不自动启动服务。需要真实 Docker 执行的检查单独运行：

```sh
bash .agent/run.sh --docker --toolbox python scripts/smoke-monitoring.py
bash .agent/run.sh --docker --toolbox python compose/logging/scripts/validate-logstash.py
bash .agent/run.sh --docker --toolbox python compose/gitlab/scripts/validate-config.py
bash .agent/run.sh --docker --toolbox python scripts/smoke-local-cluster.py
bash .agent/run.sh --docker --toolbox python scripts/smoke-gitops.py
bash .agent/run.sh --docker --toolbox python scripts/smoke-logging.py --preflight-only
```

监控检查会拉取镜像、创建随机项目、自监控并完成空卷恢复，随后清理自己的容器和卷；
Logstash 检查仅启动一个独立配置检查容器，经过实际 keystore 初始化后验证 pipeline。
GitLab 检查使用固定镜像内的 Ruby 配置加载器，不启动 GitLab 服务；上述三个 Compose
检查不发布宿主端口。kind 检查建立临时三节点集群，两次安装 Argo CD 并验收 PVC，API
仅绑定宿主回环地址，完成后删除自己的测试集群。资源有限时逐个执行。
报告保存在各自 `.local/` 目录。范围与资源说明见各组件 README 和[本地集群手册](local-cluster.md)，
不能将这些检查与目标环境的高可用、外部通知或完整 ELK/GitLab 数据流验收混为一谈。

GitOps 检查单独创建临时集群及只读测试 Git 仓库，由 Argo CD 实际同步业务示例，验证手工
漂移恢复和 AppProject 对 Secret/ClusterRole 的拒绝。范围与判据见[实际同步验收](gitops-validation.md)。

ELK 全链路入口为 `scripts/smoke-logging.py`，不带参数时才会启动服务并做空卷日志恢复；
`--preflight-only` 只检查资源，`--check-model` 只解析测试配置。当前本机内存不足，已验证
前置检查会拒绝启动；完整运行尚未验收，准备条件和范围
见 [ELK 运行验收](../compose/logging/validation.md)。

需要保留本地环境时使用 `scripts/local_cluster.py up`；`verify` 检查存储示例并保留集群。
两条命令同样通过 `bash .agent/run.sh --docker --toolbox python ...` 执行。

| runner 参数 | 用途 |
| --- | --- |
| `--toolbox` | 在通用 Python/shell 命令中使用完整工具箱 |
| `--docker` | 显式授予内层工具访问当前 Docker daemon 的能力 |
| `--socket /absolute/docker.sock` | 覆盖默认 `/var/run/docker.sock` |
| `--kubeconfig /absolute/file` | 只读挂载单个 kubeconfig，自动设置容器 KUBECONFIG |
| `--network kind` | 工具容器加入指定现有 Docker 网络 |
| `--publish 127.0.0.1:8443:8443` | 显式发布一个仅宿主回环可访问的端口，用于本地 port-forward |
| `--host-dir /srv/platform` | 在工具容器中按同名路径只读挂载外部配置目录 |

kubeconfig 应内嵌 CA/证书/密钥；引用宿主文件路径的配置在容器内通常无效。
云集群 exec 认证需要另行提供对应插件和短期身份，本工具箱不默认包含各云 CLI。
每条集群命令使用明确的 `--context` 或 Helm `--kube-context`；不要依赖当前默认 context。
私钥/密码不要写进命令行参数或提交到 Git。

Compose 的 bind source 由 daemon 解释，必须填写 daemon 宿主可见的绝对路径；
`/workspace` 是工具容器路径。`--host-dir` 只帮助客户端读取配置，不会把文件部署到远程主机。
在 Linux 生产主机本地运行本 runner；远程操作需要另行准备受控访问和文件分发。

`--docker` 用于构建、Compose 运维和 kind。它具有管理该 Docker 主机的权限，
普通语法检查、Helm 渲染和 Kubernetes 命令不需要它。Docker 不可用时停止执行，不能回退宿主。
