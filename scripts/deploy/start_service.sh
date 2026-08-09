#!/bin/sh
set -eu

service_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$service_root"

corpus_dir=${FINLLM_CORPUS_DIR:-/app/corpus/v0.1}
index_path=${FINLLM_INDEX_PATH:-/var/lib/finllm/index-v0.1.json}
index_dir=$(dirname -- "$index_path")

mkdir -p "$index_dir"
python scripts/rag_index.py build --corpus "$corpus_dir" --output "$index_path"
python scripts/rag_index.py config-hash --index "$index_path"

exec python -m service.app
