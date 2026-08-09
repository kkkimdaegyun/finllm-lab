#!/usr/bin/env bash
# Run the whole Profile A sweep in one command.
#
# Serving, load testing, quality evaluation and result recording were done by
# hand for the first measurement, which makes the numbers hard to reproduce.
# This drives the same sequence from configs/ so a rerun is one command.
#
#   bash scripts/run_profile_a.sh 2026-08-09
#   bash scripts/run_profile_a.sh 2026-08-09 --dry-run
#
# A startup failure or an OOM is a result, not a reason to abort the sweep:
# the configuration is recorded as failed and the next one still runs.

set -uo pipefail

DATE_TAG="${1:-}"
DRY_RUN="${2:-}"
if [[ -z "$DATE_TAG" ]]; then
  echo "usage: bash scripts/run_profile_a.sh <DATE_TAG> [--dry-run]" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GPU_INDEX="${GPU_INDEX:-1}"
PORT="${PORT:-8000}"
VENV="${VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
BASE_URL="http://127.0.0.1:${PORT}/v1"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-900}"
REPS="${REPS:-3}"
CONCURRENCY="${CONCURRENCY:-10}"
REQUESTS="${REQUESTS:-30}"

# ENFORCE_EAGER=1 drops CUDA graph capture, which costs 2.2-3.5 GiB outside the
# executor budget. Slugs get an -eager suffix so both sweeps can coexist in
# results/ instead of one overwriting the other.
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"
SLUG_SUFFIX=""
EAGER_ARGS=()
if [[ "$ENFORCE_EAGER" == "1" ]]; then
  SLUG_SUFFIX="-eager"
  EAGER_ARGS=(--enforce-eager)
fi

REVISION_8B="b968826d9c46dd6066d109eabc6255188de91218"
REVISION_14B_AWQ="31c69efc29464b6bb0aee1398b5a7b50a99340c3"

# slug | model | revision | quantization | budget-mode
CONFIGS=(
  "qwen3-8b-bf16-classceiling|Qwen/Qwen3-8B|${REVISION_8B}|none|class-ceiling"
  "qwen3-14b-awq-classceiling|Qwen/Qwen3-14B-AWQ|${REVISION_14B_AWQ}|awq|class-ceiling"
  "qwen3-14b-awq-deploymentmatched|Qwen/Qwen3-14B-AWQ|${REVISION_14B_AWQ}|awq|deployment-matched"
)

RETRIEVAL="work/retrieval-${DATE_TAG}.json"
FAILED_CONFIGS=()

log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
run() {
  if [[ "$DRY_RUN" == "--dry-run" ]]; then
    printf '  DRY: %s\n' "$*"
  else
    "$@"
  fi
}

require_idle_gpu() {
  local used
  used=$(nvidia-smi --id="$GPU_INDEX" --query-gpu=memory.used --format=csv,noheader,nounits)
  if (( used > 1024 )); then
    echo "GPU ${GPU_INDEX} already holds ${used} MiB. Peak VRAM would be polluted." >&2
    echo "Pick a free GPU with GPU_INDEX=<n> or stop the other process." >&2
    exit 1
  fi
  log "GPU ${GPU_INDEX} is idle (${used} MiB used)"
}

wait_for_ready() {
  local logfile="$1" waited=0
  while (( waited < READY_TIMEOUT_S )); do
    if curl -s -m 2 "${BASE_URL}/models" >/dev/null 2>&1; then
      log "server ready after ${waited}s"
      return 0
    fi
    if grep -qE "Traceback|out of memory|No available memory|ValueError|RuntimeError" "$logfile" 2>/dev/null; then
      return 1
    fi
    sleep 5
    waited=$(( waited + 5 ))
  done
  echo "server did not become ready within ${READY_TIMEOUT_S}s" >&2
  return 1
}

stop_server() {
  pkill -f "vllm serve" 2>/dev/null || true
  local waited=0
  while (( waited < 120 )); do
    local used
    used=$(nvidia-smi --id="$GPU_INDEX" --query-gpu=memory.used --format=csv,noheader,nounits)
    (( used < 1024 )) && return 0
    sleep 5
    waited=$(( waited + 5 ))
  done
  echo "WARNING: GPU ${GPU_INDEX} still busy after stopping the server" >&2
}

measure_one() {
  local slug="$1" model="$2" revision="$3" quant="$4" budget="$5"
  local cmdfile="work/cmd-${slug}.txt"
  local serverlog="work/serve-${slug}.log"
  local evalfile="work/eval-${slug}-${DATE_TAG}.json"

  log "=== ${slug} ==="

  local quant_args=()
  [[ "$quant" != "none" ]] && quant_args=(--quantization "$quant")
  python3 scripts/finllm_profile.py serve-command \
    --profile profile-a --model "$model" --revision "$revision" \
    --budget-mode "$budget" --max-model-len 8192 --max-num-seqs 10 \
    --port "$PORT" "${quant_args[@]}" "${EAGER_ARGS[@]}" 2>/dev/null > "$cmdfile"
  log "serve command: $(cat "$cmdfile")"

  if [[ "$DRY_RUN" == "--dry-run" ]]; then
    printf '  DRY: start server, warm up, %d reps at concurrency %d, evaluate, record\n' \
      "$REPS" "$CONCURRENCY"
    return 0
  fi

  # The generated command starts with `vllm`; run the one inside the venv.
  local serve_cmd
  serve_cmd="${VENV}/bin/$(cat "$cmdfile")"
  CUDA_VISIBLE_DEVICES="$GPU_INDEX" bash -c "$serve_cmd" > "$serverlog" 2>&1 &

  if ! wait_for_ready "$serverlog"; then
    log "${slug}: SERVER FAILED TO START — recording as a failed configuration"
    grep -iE "out of memory|No available memory|Error" "$serverlog" | head -5 >&2
    FAILED_CONFIGS+=("$slug")
    stop_server
    return 0
  fi

  grep -E "KV cache size|Maximum concurrency|Model loading took|Available KV cache memory|Graph capturing" \
    "$serverlog" | sed 's/^/    /'

  log "${slug}: warm-up (separate from every measured request)"
  "$PY" scripts/load_test.py --model "$model" --dataset datasets/smoke.jsonl \
    --base-url "$BASE_URL" --concurrency 1 --requests 3 \
    --output "work/warmup-${slug}.json" >/dev/null

  log "${slug}: concurrency 1 baseline"
  "$PY" scripts/load_test.py --model "$model" --dataset datasets/smoke.jsonl \
    --base-url "$BASE_URL" --concurrency 1 --requests 10 \
    --output "work/load-${slug}-c1.json" >/dev/null

  for (( r = 1; r <= REPS; r++ )); do
    log "${slug}: concurrency ${CONCURRENCY}, repetition ${r}/${REPS}"
    python3 scripts/gpu_watch.py --gpu-index "$GPU_INDEX" --interval 0.3 \
      --output "work/vram-${slug}-c10-r${r}.json" >/dev/null 2>&1 &
    local watcher=$!
    sleep 1
    "$PY" scripts/load_test.py --model "$model" --dataset datasets/smoke.jsonl \
      --base-url "$BASE_URL" --concurrency "$CONCURRENCY" --requests "$REQUESTS" \
      --output "work/load-${slug}-c10-r${r}.json" >/dev/null
    kill -TERM "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
  done

  # The first configuration freezes retrieval; the rest reuse it so the quality
  # comparison is generation-only (experiment-protocol Stage C).
  local retrieval_args=(--save-retrieval "$RETRIEVAL")
  [[ -f "$RETRIEVAL" ]] && retrieval_args=(--frozen-retrieval "$RETRIEVAL")

  log "${slug}: quality evaluation"
  "$PY" scripts/rag_eval.py --index "work/index-${DATE_TAG}.json" \
    --base-url "$BASE_URL" --model "$model" \
    "${retrieval_args[@]}" --output "$evalfile" >/dev/null || \
    log "${slug}: rag_eval reported a failure — see ${evalfile}"

  stop_server

  log "${slug}: writing result records"
  for (( r = 1; r <= REPS; r++ )); do
    local template
    template=$(mktemp)
    python3 scripts/finllm_profile.py new-result \
      --profile profile-a --model "$model" --revision "$revision" \
      --quantization "$quant" --evidence memory-budget-emulation \
      --budget-mode "$budget" --max-model-len 8192 --max-num-seqs 10 \
      --request-count "$REQUESTS" --repetition "$r" --output "$template" >/dev/null
    "$PY" scripts/fill_result.py --template "$template" \
      --load-test "work/load-${slug}-c10-r${r}.json" \
      --vram "work/vram-${slug}-c10-r${r}.json" \
      --eval "$evalfile" --server-log "$serverlog" \
      --environment work/environment.json --command-file "$cmdfile" \
      --repetition "$r" \
      --decision-reason "FILL_ME: 측정값을 보고 직접 작성한다" \
      --notes "A6000 1장(GPU index ${GPU_INDEX})에서 ${budget} 예산으로 측정. 지연시간과 처리량은 A6000 관측값이며 대상 카드 성능이 아니다." \
      --output "results/${DATE_TAG}-profile-a-${slug}-r${r}.json" >/dev/null
    rm -f "$template"
  done
}

log "Profile A sweep — tag ${DATE_TAG}"
[[ "$DRY_RUN" == "--dry-run" ]] || require_idle_gpu

log "capturing environment"
run "$PY" scripts/capture_environment.py --output work/environment.json

log "building index"
run python3 scripts/rag_index.py build --corpus corpus/v0.1 \
  --output "work/index-${DATE_TAG}.json"
if [[ "$DRY_RUN" != "--dry-run" ]]; then
  log "retriever config hash: $(python3 scripts/rag_index.py config-hash --index "work/index-${DATE_TAG}.json")"
fi

for entry in "${CONFIGS[@]}"; do
  IFS='|' read -r slug model revision quant budget <<< "$entry"
  measure_one "${slug}${SLUG_SUFFIX}" "$model" "$revision" "$quant" "$budget"
done

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  log "dry run complete"
  exit 0
fi

log "validating every record written in this sweep"
status=0
for file in results/"${DATE_TAG}"-profile-a-*.json; do
  [[ -e "$file" ]] || continue
  if python3 scripts/finllm_profile.py validate-result "$file" >/dev/null 2>&1; then
    echo "  OK   $(basename "$file")"
  else
    echo "  FAIL $(basename "$file")"
    python3 scripts/finllm_profile.py validate-result "$file" 2>&1 | sed 's/^/       /'
    status=1
  fi
done

if (( ${#FAILED_CONFIGS[@]} > 0 )); then
  log "configurations that failed to start: ${FAILED_CONFIGS[*]}"
  echo "These are results too. Record why they failed instead of deleting them." >&2
fi

log "decision.reason is FILL_ME in every new record — fill it from the measurements"
exit "$status"
