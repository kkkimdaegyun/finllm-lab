#!/usr/bin/env bash
# release manifest에 적힌 구성으로 추론 서버를 재기동한다.
#
#   bash ops/release/restart.sh <release-id>
#
# 이것은 **A파트 컨테이너가 들어오기 전까지의 임시 실행 기구**다. rollback이
# 문서가 아니라 실행 가능한 절차여야 하므로 필요했다. Codex의 deploy compose가
# 들어오면 release manifest의 runtime.restart_command를
#
#   docker compose -f <deploy compose> up -d --wait
#
# 로 바꾸면 scripts/rollback_release.py는 그대로 쓴다. 그것이 restart_command를
# manifest에 둔 이유다.
#
# CUDA/드라이버는 건드리지 않는다. 이미 설치된 .venv의 vLLM을 실행할 뿐이다.

set -uo pipefail

RELEASE_ID="${1:-}"
if [[ -z "$RELEASE_ID" ]]; then
  echo "usage: bash ops/release/restart.sh <release-id>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

MANIFEST="ops/release/history/${RELEASE_ID}.json"
[[ -f "$MANIFEST" ]] || { echo "release manifest 없음: $MANIFEST" >&2; exit 1; }

VENV="${VENV:-/home/dgkim/dgkim/new_project/.venv}"
GPU_INDEX="${GPU_INDEX:-1}"
PORT="${PORT:-8000}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-300}"
LOG="work/v02/serve-${RELEASE_ID}.log"

SERVE_CMD=$(python3 -c "
import json,sys
m=json.load(open('$MANIFEST',encoding='utf-8'))
cmd=m['runtime'].get('serve_command')
if not cmd: sys.exit('runtime.serve_command가 manifest에 없다')
print(cmd)
") || exit 1

echo "[restart] release   : $RELEASE_ID"
echo "[restart] gpu index : $GPU_INDEX"
echo "[restart] command   : $SERVE_CMD"

# --- stop ---------------------------------------------------------------
# 패턴을 "[v]llm serve"로 쓴다. pkill -f 는 **자기 자신을 포함한** 모든
# 프로세스의 전체 명령줄을 훑기 때문에, 스크립트를 인라인으로 실행하는 셸의
# argv에 "vllm serve"라는 문자열이 들어 있으면 그 셸까지 죽인다.
# INC-001 실험에서 실제로 이것 때문에 복구 단계가 중단됐다.
pkill -f "[v]llm serve" 2>/dev/null || true
sleep 3

# pkill만으로는 부족하다. vLLM의 engine worker는
#   python -c from multiprocessing.spawn import spawn_main; ... --multiprocessing-fork
# 로 뜨기 때문에 명령줄에 "vllm serve"가 없다. 부모를 SIGKILL하면 이 worker가
# PPID=1로 고아가 되어 **VRAM을 그대로 쥐고 남는다.** INC-001에서 23,004 MiB가
# 이렇게 남아 있는 것을 확인했다.
#
# 그래서 프로세스 이름이 아니라 GPU 점유 사실로 찾아 정리한다. 대상은
#   - GPU_INDEX 한 장에 한정하고 (다른 GPU의 프로세스는 건드리지 않는다)
#   - 현재 사용자 소유인 것만
# 죽인다. 무엇을 죽였는지 항상 출력한다.
reclaim_gpu() {
  local pids pid owner
  pids=$(nvidia-smi --id="$GPU_INDEX" --query-compute-apps=pid --format=csv,noheader 2>/dev/null)
  for pid in $pids; do
    owner=$(ps -o user= -p "$pid" 2>/dev/null | tr -d ' ')
    if [[ "$owner" == "$(id -un)" ]]; then
      echo "[restart] GPU ${GPU_INDEX} 잔존 프로세스 정리: pid=${pid} ($(ps -o cmd= -p "$pid" 2>/dev/null | cut -c1-70))"
      kill -9 "$pid" 2>/dev/null || true
    else
      echo "[restart] WARNING: pid=${pid} 는 다른 사용자(${owner:-?}) 소유라 건드리지 않는다" >&2
    fi
  done
}

waited=0
while (( waited < 120 )); do
  used=$(nvidia-smi --id="$GPU_INDEX" --query-gpu=memory.used --format=csv,noheader,nounits)
  (( used < 1024 )) && break
  (( waited == 9 )) && reclaim_gpu
  sleep 3; waited=$((waited+3))
done

used=$(nvidia-smi --id="$GPU_INDEX" --query-gpu=memory.used --format=csv,noheader,nounits)
if (( used >= 1024 )); then
  echo "[restart] GPU ${GPU_INDEX} 가 아직 ${used} MiB를 쥐고 있다. 기동해도 peak VRAM 측정이 오염된다." >&2
  exit 1
fi
echo "[restart] GPU ${GPU_INDEX} 회수 완료 (${waited}s, ${used} MiB)"

# --- start --------------------------------------------------------------
mkdir -p work/v02
CUDA_VISIBLE_DEVICES="$GPU_INDEX" nohup bash -c "${VENV}/bin/${SERVE_CMD}" > "$LOG" 2>&1 &
echo "[restart] 기동 중... 로그: $LOG"

waited=0
while (( waited < READY_TIMEOUT_S )); do
  if curl -sf -m 2 "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "[restart] ready after ${waited}s"
    exit 0
  fi
  if grep -qE "Traceback|out of memory|No available memory" "$LOG" 2>/dev/null; then
    echo "[restart] 기동 실패 — 로그 확인: $LOG" >&2
    grep -iE "out of memory|No available memory|Error" "$LOG" | head -5 >&2
    exit 1
  fi
  sleep 5; waited=$((waited+5))
done

echo "[restart] ${READY_TIMEOUT_S}s 안에 ready 되지 않았다" >&2
exit 1
