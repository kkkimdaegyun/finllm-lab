#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_root"

python -m json.tool deploy/config/service.json >/dev/null
docker compose --file deploy/compose.yaml config --quiet
docker compose --file monitoring/compose.monitoring.yaml config --quiet
python -m unittest tests.test_deploy_contract
