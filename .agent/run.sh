#!/usr/bin/env bash
set -euo pipefail

# Host-side work is limited to locating this checkout and starting Docker.
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Flags keep every invocation beginning with `bash .agent/run.sh`.
while [ "$#" -gt 0 ]; do
  case "$1" in
    --docker) AGENT_DOCKER=1; shift ;;
    --toolbox) AGENT_TOOLBOX=1; shift ;;
    --kubeconfig|--network|--host-dir|--socket)
      [ "$#" -ge 2 ] || { printf '%s\n' "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --kubeconfig) AGENT_KUBECONFIG="$2" ;;
        --network) AGENT_NETWORK="$2" ;;
        --host-dir) AGENT_HOST_DIR="$2" ;;
        --socket) AGENT_DOCKER_SOCKET="$2" ;;
      esac
      shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'Docker is required. Install/start Docker Desktop, then retry.' >&2
  exit 127
fi

if [ "$#" -eq 0 ]; then
  printf '%s\n' 'Usage: bash .agent/run.sh <command> [args...]' >&2
  exit 2
fi

IMAGE="python:3.13-bookworm"
case "$1" in
  docker) IMAGE="docker:29.8.1-cli" ;;
  helm|kubectl|kind) IMAGE="local-platform/toolbox:2026-09-26" ;;
esac
if [ "${AGENT_TOOLBOX:-0}" = 1 ]; then
  IMAGE="local-platform/toolbox:2026-09-26"
fi

DOCKER_ARGS=(--rm -i --init)
if [ "${AGENT_DOCKER:-0}" = 1 ]; then
  SOCKET="${AGENT_DOCKER_SOCKET:-/var/run/docker.sock}"
  if [ ! -S "$SOCKET" ]; then
    printf '%s\n' "Docker socket not found: $SOCKET (set AGENT_DOCKER_SOCKET)." >&2
    exit 2
  fi
  DOCKER_ARGS+=(-v "${SOCKET}:/var/run/docker.sock" -e DOCKER_HOST=unix:///var/run/docker.sock)
  # Compose bind sources are interpreted by the daemon. Make repository paths
  # available under their host spelling as well as /workspace.
  DOCKER_ARGS+=(-v "${PROJECT_ROOT}:${PROJECT_ROOT}:ro")
fi
if [ -n "${AGENT_KUBECONFIG:-}" ]; then
  if [ ! -f "$AGENT_KUBECONFIG" ]; then
    printf '%s\n' 'AGENT_KUBECONFIG must name an existing, flattened kubeconfig.' >&2
    exit 2
  fi
  DOCKER_ARGS+=(-v "${AGENT_KUBECONFIG}:/run/platform-kubeconfig:ro" -e KUBECONFIG=/run/platform-kubeconfig)
fi
if [ -n "${AGENT_NETWORK:-}" ]; then
  DOCKER_ARGS+=("--network=$AGENT_NETWORK")
fi
if [ -n "${AGENT_HOST_DIR:-}" ]; then
  case "$AGENT_HOST_DIR" in
    /*) DOCKER_ARGS+=(-v "${AGENT_HOST_DIR}:${AGENT_HOST_DIR}:ro") ;;
    *) printf '%s\n' 'AGENT_HOST_DIR must be an absolute path.' >&2; exit 2 ;;
  esac
fi

exec docker run --entrypoint "" \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -e "PLATFORM_HOST_ROOT=${PROJECT_ROOT}" \
  "${DOCKER_ARGS[@]}" \
  "${IMAGE}" \
  "$@"
