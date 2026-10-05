#!/bin/sh
# This script runs inside the CI job container, never on the host.
set -eu
umask 077
fail() { printf '%s\n' "$1" >&2; exit 2; }
match() { printf '%s' "$1" | grep -Eq "$2"; }
[ "${CI_DEBUG_TRACE:-false}" = false ] && [ "${CI_DEBUG_SERVICES:-false}" = false ] || fail 'CI debug logging is forbidden.'
[ "${CI_PIPELINE_SOURCE:-}" = push ] &&
  [ "${CI_COMMIT_REF_PROTECTED:-}" = true ] &&
  [ -n "${CI_DEFAULT_BRANCH:-}" ] &&
  [ "${CI_COMMIT_BRANCH:-}" = "$CI_DEFAULT_BRANCH" ] || fail 'Protected default-branch push required.'
match "${CI_COMMIT_SHA:-}" '^[0-9a-f]{40}$' || fail 'Full source commit SHA required.'
case "${BUILD_PLATFORM:-}" in linux/amd64|linux/arm64) ;; *) fail 'Select linux/amd64 or linux/arm64.' ;; esac
# Deliberately limited to lower-case DNS authorities and simple repository paths.
host='[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*'
authority="$host(:[0-9]{1,5})?"
match "${REGISTRY_HOST:-}" "^${authority}$" || fail 'Invalid REGISTRY_HOST.'
match "${IMAGE_REPOSITORY:-}" "^${authority}/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$" || fail 'Invalid IMAGE_REPOSITORY.'
[ "${IMAGE_REPOSITORY%%/*}" = "$REGISTRY_HOST" ] || fail 'Repository must belong to REGISTRY_HOST.'
match "${BUILDKIT_ADDR:-}" "^tcp://${host}:[0-9]{1,5}$" || fail 'A TLS BuildKit TCP host:port address is required.'
# grep is line-based: reject embedded line breaks separately before any network use.
case "$REGISTRY_HOST$IMAGE_REPOSITORY$BUILDKIT_ADDR$CI_COMMIT_SHA" in
  *'
'*) fail 'Multiline addresses or revisions are forbidden.' ;;
esac
for address in "$REGISTRY_HOST" "${BUILDKIT_ADDR#tcp://}"; do
  case "$address" in *:*)
    port=${address##*:}
    [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail 'Port outside 1..65535.' ;;
  esac
done
for cert in "${BUILDKIT_CA:-}" "${BUILDKIT_CERT:-}" "${BUILDKIT_KEY:-}"; do
  [ -f "$cert" ] && [ -s "$cert" ] || fail 'BuildKit CA/certificate/key must be nonempty file variables.'
done
[ -n "${REGISTRY_USER:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ] || fail 'Scoped registry push credentials required.'
case "$REGISTRY_USER" in *:*) fail 'Registry username cannot contain a colon.' ;; esac
DOCKER_CONFIG=$(mktemp -d)
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT HUP INT TERM
auth=$(printf '%s:%s' "$REGISTRY_USER" "$REGISTRY_PASSWORD" | base64 | tr -d '\n')
printf '{"auths":{"%s":{"auth":"%s"}}}\n' "$REGISTRY_HOST" "$auth" > "$DOCKER_CONFIG/config.json"
unset auth REGISTRY_PASSWORD
set -- --addr "$BUILDKIT_ADDR" --tlscacert "$BUILDKIT_CA" --tlscert "$BUILDKIT_CERT" --tlskey "$BUILDKIT_KEY"
# Both the builder and this client's registry-auth connection must trust its CA.
if [ -n "${REGISTRY_CA:-}" ]; then
  [ -f "$REGISTRY_CA" ] && [ -s "$REGISTRY_CA" ] || fail 'REGISTRY_CA must be a nonempty file variable.'
  registry_tls="host=$REGISTRY_HOST,ca=$REGISTRY_CA"
  case "$REGISTRY_CA" in *,*|*'
'*) fail 'Invalid registry CA path.' ;; esac
  set -- "$@" build --registry-auth-tlscontext "$registry_tls"
else
  set -- "$@" build
fi
# A small, explicit context prevents CI credentials and the .git tree being sent.
buildctl "$@" --frontend dockerfile.v0 \
  --local context=ci/image --local dockerfile=ci/image \
  --opt "platform=$BUILD_PLATFORM" --opt "build-arg:SOURCE_REVISION=$CI_COMMIT_SHA" \
  --output "type=image,name=$IMAGE_REPOSITORY:$CI_COMMIT_SHA,push=true" \
  --metadata-file build-metadata.json
