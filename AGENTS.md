# Repository execution policy

Host shell commands are for read-only inspection only (files, metadata, searches,
and read-only Git operations). Edit source with file editing tools.

Run every project command, including Git mutations, dependency installation,
validation, tests, formatting, generators, and cluster tools, through:

```sh
bash .agent/run.sh <command> [args...]
```

For compound commands, put the shell inside Docker:

```sh
bash .agent/run.sh sh -lc 'command1 && command2'
```

Read and reuse `.agent/run.sh`. It must use `docker run --rm`, mount this repository
at `/workspace`, use `/workspace` as the working directory, and forward all command
arguments. Do not run project code on the host, invoke `docker run` outside the
runner, or use host shell redirection for project work. If Docker fails, report
the problem; never fall back to host execution.

The runner uses Python 3.13 Bookworm for Git and validation, a pinned Docker CLI
image for Compose, and a repository-built toolbox for kind, kubectl and Helm.
See docs/tooling.md for building the toolbox. Docker socket and kubeconfig access
are explicit options; do not mount host credentials by default.

Keep credentials, kubeconfig, generated manifests, and runtime data out of Git.
Pin deployed component versions; do not introduce floating `latest` or `stable`
deployment references. Preserve the distinction between local simulation and
production availability. Document prerequisites and verification for new services.

This repository contains deployment examples, not an installed production
platform. Distinguish static/rendering checks from runtime and recovery evidence.
Do not deploy all optional services while editing configuration examples.
