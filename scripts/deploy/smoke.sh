#!/bin/sh
set -eu

base_url=${FINLLM_SMOKE_BASE_URL:-http://127.0.0.1:8080}

curl --fail --silent --show-error "$base_url/health"
curl --fail --silent --show-error "$base_url/ready"
curl --fail --silent --show-error "$base_url/metrics" \
  | grep --fixed-strings finllm_requests_total >/dev/null

echo "service endpoint smoke checks passed"
