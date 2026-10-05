# GitLab CI：构建镜像与发布候选

这是可复制到业务源码仓库的双阶段示例：受保护默认分支 push → 远程 BuildKit 构建及推送 → digest 发布候选。CI job 只运行 buildctl 客户端和 Python，不启动 Docker/BuildKit daemon、不挂载 Docker socket、不使用生产 kubeconfig。现有 Kubernetes executor 的能力限制保持不变。

**当前没有运行真实 GitLab 流水线、Registry 推送或生产发布验收。** 本仓库 GitOps 演练证明的是另一条预置清单同步链路，不能替代本示例验收。脚本测试只覆盖输入拒绝及客户端参数。

## 前置条件

- 由管理员提供可从 CI Pod 和 Kubernetes 节点访问的 **HTTPS Registry**。当前 GitLab Compose 没有启用 Registry；`CI_REGISTRY_*` 不会凭空提供该服务。推送账号仅授权一个镜像仓库；节点使用独立只读长期拉取凭据，并安排轮换。
- 在专用可信构建环境提供 BuildKit **v0.33.1**，gRPC TCP 必须校验双方证书；服务证书 SAN 包含客户端使用的主机名。客户端、builder 分别可访问 Registry/认证服务且信任其 CA。私有 Registry CA 在 daemon 的 registry 配置和客户端变量中分别配置；禁用 TLS 校验不受支持。
- 本目录不部署 builder。不要把 daemonless rootless BuildKit 直接放入当前受限 CI Pod：它需要额外 user namespace/mount 权限。独立 rootless Docker 部署按官方说明配置 seccomp/AppArmor/systempaths；不要给现有 Runner 添加 privileged 或 Docker socket。一个 builder 只服务同一信任域；mTLS 并不隔离共享缓存或提供项目级授权。
- 默认分支须受保护，并限制提交/合并权限；Runner 在 GitLab UI 设为项目专属、Protected、禁止 untagged jobs，固定 tag 为 `platform-kubernetes`。不要把这些凭据暴露给 fork/MR 流水线，也不要给普通开发者覆盖保留 CI 变量的权限。
- 此示例只构建 `linux/amd64` 或 `linux/arm64` 一种架构，builder 必须支持所选架构。目标节点匹配；混合架构集群须显式调度或另行验证多架构构建。

## 复制和配置

把本目录的 `build.sh`、`release.py`、`image/` 放到业务源码仓库的 `ci/`，把 `pipeline.yaml.example` 复制为该仓库根目录 `.gitlab-ci.yml`。改变示例网页时编辑 `ci/image/index.html`；构建上下文限定在 `ci/image/`。

在 GitLab 项目 CI/CD Variables 中配置以下值，全部设 **Protected**，关闭变量展开；密码设 Masked/Hidden。证书私钥使用受保护 File 类型，禁止调试输出和上传整个工作目录。

| 变量 | 类型和示例 |
| --- | --- |
| `BUILDKIT_ADDR` | Variable，`tcp://buildkit.example.invalid:1234`，替换为真实 DNS/端口 |
| `BUILDKIT_CA` / `BUILDKIT_CERT` / `BUILDKIT_KEY` | File，可信 CA、客户端证书、私钥；job 中的变量值是文件路径 |
| `REGISTRY_HOST` | Variable，`registry.example.invalid:443`，不含协议或路径 |
| `IMAGE_REPOSITORY` | Variable，`registry.example.invalid:443/business/production-demo`，不含 tag/digest |
| `REGISTRY_USER` / `REGISTRY_PASSWORD` | Variable，单仓库推送账号/密码 |
| `REGISTRY_CA` | 可选 File，私有 Registry CA；daemon 同样需单独配置信任 |
| `BUILD_PLATFORM` | 默认 `linux/amd64`；仅允许 `linux/arm64` 作为另一选择 |

采用有限的 DNS 和小写仓库路径语法；不支持 IPv6 authority、URL 用户信息或任意 buildctl 参数。Builder 私钥留在构建主机，不能作为 CI 客户端密钥。客户端镜像、Python 镜像和 BusyBox 基础镜像已固定多平台 digest（2026-10-05 从 Docker Hub 查询）；builder 服务端版本也必须单独固定并维护。

## 发布流程和验收

合并源码后查看 build-image 日志和 release-proposal 的 `release/release.json`、`release/REVIEW.md`。产物包含源码 SHA、流水线 ID、平台和不可变镜像 digest；它不是签名证明、漏洞扫描报告或已部署证明。

按 REVIEW.md 提交业务部署 MR。**现有 production-demo 的 ConfigMap 挂载会覆盖 `/www`**，首次切换为镜像内页面时需移除该挂载/卷及对应 generator。管理员预置私有 Registry 拉取 Secret；业务 AppProject 不允许 CI 创建 Secret。审核合并后，管理员将 Application 固定到最终部署提交完整 SHA 并手动同步。CI 不持有 GitLab 写入 token 或 Argo CD/Kubernetes 凭据。

目标环境验收至少记录：无客户端证书/错误 CA 连接被拒、成功构建推送并按 digest 拉取、容器非 root 只读运行及 HTTP 内容正确、短期构建凭据撤销后节点仍能拉取、双副本滚动发布/回滚、Application 实际 revision 与已审核部署 SHA 相同。源代码提交、业务部署提交和 Application 配置提交是三个不同审计对象。

本地仅运行脚本回归（仍经过仓库 Docker runner）：

```sh
bash .agent/run.sh python -m unittest discover -s compose/gitlab/ci -p 'test_*.py'
bash .agent/run.sh sh -n compose/gitlab/ci/build.sh
```

参考：[BuildKit v0.33.1 发布](https://github.com/moby/buildkit/releases/tag/v0.33.1)、[mTLS 与镜像 metadata](https://github.com/moby/buildkit/blob/v0.33.1/README.md)、[rootless 权限要求](https://github.com/moby/buildkit/blob/v0.33.1/docs/rootless.md)、[BuildKit 安全边界](https://github.com/moby/buildkit/blob/v0.33.1/PROJECT.md)、[GitLab BuildKit](https://docs.gitlab.com/ci/docker/using_buildkit/)、[受保护 CI 变量](https://docs.gitlab.com/ci/variables/)。
