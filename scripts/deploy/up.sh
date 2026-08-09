#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_root"

docker network inspect finllm-net >/dev/null 2>&1 \
  || docker network create finllm-net >/dev/null

if [ -f deploy/.env ]; then
  docker compose --env-file deploy/.env --file monitoring/compose.monitoring.yaml \
    up -d
  exec docker compose --env-file deploy/.env --file deploy/compose.yaml \
    up --build "$@"
fi

docker compose --file monitoring/compose.monitoring.yaml up -d
exec docker compose --file deploy/compose.yaml up --build "$@"
