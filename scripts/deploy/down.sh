#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_root"

if [ -f deploy/.env ]; then
  docker compose --env-file deploy/.env --file deploy/compose.yaml down "$@"
  docker compose --env-file deploy/.env --file monitoring/compose.monitoring.yaml down "$@"
else
  docker compose --file deploy/compose.yaml down "$@"
  docker compose --file monitoring/compose.monitoring.yaml down "$@"
fi
