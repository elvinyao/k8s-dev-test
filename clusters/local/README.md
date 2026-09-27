# local 环境

`kind.yaml` 定义 1 个 control-plane 和 2 个 worker；三个节点仍共享同一台机器。
当前只提供配置，尚未创建集群。默认 API 地址绑定回环接口。

基线版本：kind `v0.33.0`、Kubernetes `v1.35.8`。节点镜像使用 kind 官方 release
列出的多架构 digest，覆盖 arm64 / amd64；实际运行仍须匹配宿主架构。
选择 1.35 系列以落在 Argo CD 3.5 的官方测试范围中，首次部署前仍须验证其他组件兼容性。

后续实现 cluster-up 时必须处理：

- 工具容器访问宿主 Docker daemon 的 socket 及权限，避免默认给所有校验命令此权限。
- kind 网络内 API 的 kubeconfig；工具容器中的 `127.0.0.1` 不指向宿主或 control-plane。
- `extraMounts.hostPath` 使用 Docker daemon 可见的宿主路径，不能误写成工具容器的 `/workspace`。
- 本地数据目录的持久化、磁盘配额和备份；现在没有配置宿主持久卷挂载。
- 创建集群前检查 Docker 的实际可用资源与同名集群，防止误覆盖已有环境。

来源：[kind v0.33.0](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0)、
[kind 配置](https://kind.sigs.k8s.io/docs/user/configuration/)、
[Argo CD 测试版本](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/#tested-versions)。
