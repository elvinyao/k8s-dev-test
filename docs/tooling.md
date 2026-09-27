# 容器工具链

宿主只检查/编辑源码，全部项目命令以 `bash .agent/run.sh` 开始。默认镜像是
Python 3.13 Bookworm；`docker` 命令选用固定 Docker CLI 镜像；`helm`、`kubectl`、`kind`
选用本仓库工具箱。默认不挂载 Docker socket、宿主 kubeconfig 或用户凭据。

## 构建与验证

```sh
bash .agent/run.sh --docker docker build -t local-platform/toolbox:2026-09-26 -f .agent/Dockerfile .
bash .agent/run.sh --toolbox python scripts/validate.py
bash .agent/run.sh --toolbox python scripts/validate-compose.py
bash .agent/run.sh --toolbox python scripts/render.py --environment local
bash .agent/run.sh --toolbox python scripts/render.py --environment production
bash .agent/run.sh --toolbox python scripts/validate-kustomize.py
bash .agent/run.sh --toolbox python scripts/validate-crds.py
```

工具箱固定 Helm 3.22.0、kubectl 1.35.8、kind 0.33.0；下载二进制时校验上游 SHA256。
首次构建/下载 chart 需要网络。`.dockerignore` 仅允许工具构建所需文件，不打包秘密与数据。
Helm 下载位于 `.cache/charts/`，渲染输出位于 `rendered/`，均不提交。
渲染摘要记录 chart archive SHA256；生产发布还需将批准的镜像摘要纳入环境配置。
CRD 校验读取这些固定 chart 中的 OpenAPI schema；它不执行 CEL、admission webhook，
不校验 Kubernetes 内置资源，也不证明控制器实际能启动服务。

| runner 参数 | 用途 |
| --- | --- |
| `--toolbox` | 在通用 Python/shell 命令中使用完整工具箱 |
| `--docker` | 显式授予内层工具访问当前 Docker daemon 的能力 |
| `--socket /absolute/docker.sock` | 覆盖默认 `/var/run/docker.sock` |
| `--kubeconfig /absolute/file` | 只读挂载单个 kubeconfig，自动设置容器 KUBECONFIG |
| `--network kind` | 工具容器加入指定现有 Docker 网络 |
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
