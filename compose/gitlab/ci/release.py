"""Create a review artifact; no GitLab API, Git mutation, or cluster access."""
import json
import os
from pathlib import Path
import re
import sys

AUTHORITY = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::[0-9]{1,5})?"
REPOSITORY = AUTHORITY + r"/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"


def checked(pattern, value, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {label}")
    return value


def proposal(metadata, env):
    if env.get("CI_DEBUG_TRACE", "false") != "false" or env.get("CI_DEBUG_SERVICES", "false") != "false":
        raise ValueError("CI debug logging is forbidden")
    if (env.get("CI_PIPELINE_SOURCE") != "push" or
            env.get("CI_COMMIT_REF_PROTECTED") != "true" or
            not env.get("CI_DEFAULT_BRANCH") or
            env.get("CI_COMMIT_BRANCH") != env["CI_DEFAULT_BRANCH"]):
        raise ValueError("Protected default-branch push required")
    registry = checked(AUTHORITY, env.get("REGISTRY_HOST"), "registry")
    repository = checked(REPOSITORY, env.get("IMAGE_REPOSITORY"), "repository")
    if repository.split("/", 1)[0] != registry:
        raise ValueError("Repository registry mismatch")
    if ":" in registry and not 1 <= int(registry.rsplit(":", 1)[1]) <= 65535:
        raise ValueError("Registry port outside 1..65535")
    source = checked(r"[0-9a-f]{40}", env.get("CI_COMMIT_SHA"), "source SHA")
    pipeline = checked(r"[1-9][0-9]*", env.get("CI_PIPELINE_ID"), "pipeline ID")
    platform = checked(r"linux/(?:amd64|arm64)", env.get("BUILD_PLATFORM"), "platform")
    if not isinstance(metadata, dict):
        raise ValueError("Build metadata must be an object")
    digest = checked(r"sha256:[0-9a-f]{64}", metadata.get("containerimage.digest"), "image digest")
    descriptor = metadata.get("containerimage.descriptor")
    if descriptor is not None and (not isinstance(descriptor, dict) or descriptor.get("digest") != digest):
        raise ValueError("Image descriptor digest mismatch")
    return {"schemaVersion": 1, "status": "requires-review", "application": "production-demo",
            "deploymentPath": "apps/production-demo", "sourceCommit": source,
            "pipelineId": pipeline, "platform": platform, "image": f"{repository}@{digest}",
            "buildTag": f"{repository}:{source}", "applicationRevision": None}


def main():
    if len(sys.argv) != 3:
        raise ValueError("Usage: release.py BUILD_METADATA OUTPUT_DIRECTORY")
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    if source.stat().st_size > 1024 * 1024:
        raise ValueError("Build metadata exceeds 1 MiB")
    release = proposal(json.loads(source.read_text()), os.environ)
    output.mkdir(parents=True, exist_ok=False)
    (output / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    (output / "REVIEW.md").write_text(f"""# 发布候选：production-demo

镜像：`{release['image']}`
源码提交：`{release['sourceCommit']}`；流水线：`{release['pipelineId']}`；架构：`{release['platform']}`。

1. 核验该 digest 对应本次构建，完成镜像漏洞检查和按 digest 拉取、启动、HTTP 内容验证；本文件没有完成这些验证。
2. 在业务部署仓库的 apps/production-demo/deployment.yaml，仅将 web 容器 image 更新为上面的 digest 引用。
3. 首次从本仓库 ConfigMap 示例切换时，删除 web 的 content volumeMount 和 content volume，删除 kustomization.yaml 的对应 configMapGenerator。否则 /www 会遮住镜像中的内容。
4. 私有 Registry 的只读拉取 Secret 由管理员预先放入 apps-prod；在 Pod 中引用 imagePullSecrets。不要把 CI 推送密码或短期 CI_JOB_TOKEN 当作长期拉取凭据。
5. 验证渲染后的命名空间、资源、安全上下文、架构调度与差异，提交 MR 并审核合并。该单架构示例须使用匹配节点；保留双副本反亲和及滚动更新时至少需要 3 个可调度工作节点。
6. 管理员读取部署 MR 最终合并后的完整 commit SHA，审核后更新 production-demo Application 的 targetRevision，再手动同步并检查双副本 Ready、HTTP 内容及实际 imageID。源码 SHA 和合并前分支 SHA 均不能替代它。
7. 保留上一发布的部署 commit SHA 和镜像 digest；回滚也通过审核 Application pin 和手动同步进行。合并业务 MR 本身不会移动固定的 Application revision。

此产物不会提交 Git、创建 MR、修改 Application 或访问 Kubernetes；applicationRevision 保持 null，直到管理员选择已审核部署提交。
""")
    print("Created release proposal; production deployment still requires review and verification.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Release proposal rejected: {error}") from error
