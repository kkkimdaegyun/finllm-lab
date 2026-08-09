#!/bin/sh
set -eu

release_id=${1:-}
if [ -z "$release_id" ]; then
  echo "usage: scripts/deploy/restart_release.sh <release-id>" >&2
  exit 2
fi

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_root"

manifest="ops/release/history/${release_id}.json"
if [ ! -f "$manifest" ]; then
  echo "release manifest not found: $manifest" >&2
  exit 1
fi

expected_digest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["source"]["image_digest"])' "$manifest")
expected_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["source"]["git_sha"])' "$manifest")
actual_digest=$(docker image inspect finllm-api:0.2-python3.10.12 --format '{{.Id}}')

if [ "$actual_digest" != "$expected_digest" ]; then
  echo "immutable API image mismatch: expected $expected_digest, got $actual_digest" >&2
  exit 1
fi
grep -Fx "FINLLM_GIT_SHA=$expected_sha" deploy/.env >/dev/null || {
  echo "deploy/.env FINLLM_GIT_SHA does not match release manifest" >&2
  exit 1
}
grep -Fx "FINLLM_IMAGE_DIGEST=$expected_digest" deploy/.env >/dev/null || {
  echo "deploy/.env FINLLM_IMAGE_DIGEST does not match release manifest" >&2
  exit 1
}

docker network inspect finllm-net >/dev/null 2>&1 || docker network create finllm-net >/dev/null
exec docker compose --env-file deploy/.env --file deploy/compose.yaml \
  up --detach --no-build --wait --wait-timeout 180
