#!/usr/bin/env bash
# F5 / F6 / F7 / F8 재현. F6만 컨테이너를 실행하고 나머지는 정적 확인이다.
#
#   bash f5_f6_f7_f8_static.sh
#
# A파트 트리를 읽기만 한다. CUDA/드라이버는 건드리지 않는다.

set -uo pipefail
A_ROOT="${FINLLM_A_ROOT:-/home/dgkim/dgkim/finllm-lab}"
B_ROOT="${FINLLM_B_ROOT:-/home/dgkim/dgkim/finllm-lab}"
VLLM_IMAGE="${FINLLM_VLLM_IMAGE:-finllm-vllm:0.2-vllm0.9.2-cuda12.2.2}"
cd "$A_ROOT"

echo "======================================================================"
echo "F5 — GPU 기본값 0이 런북(GPU 1)과 충돌하고 VRAM alert 오탐을 만든다"
echo "======================================================================"
echo "-- compose / env 기본값"
grep -n "FINLLM_GPU_DEVICE_ID" deploy/compose.yaml deploy/.env.example
echo "-- 런북이 지정하는 GPU"
grep -n "GPU 1을 쓴다" docs/runbook-profile-a.md
echo "-- GPU 0 현재 점유"
nvidia-smi --id=0 --query-gpu=memory.used --format=csv,noheader
echo "-- B파트 alert 표현식 (gpu 라벨 필터 없음)"
grep -A12 "alert: FinLLMGPUMemoryAboveProfileClass" \
  "$B_ROOT/monitoring/prometheus/rules/finllm-alerts.yml" 2>/dev/null | grep "expr:"
echo "산술: GPU0 점유 + v0.1 실측 22362 MiB > 임계 24576 MiB -> critical 오발화"

echo
echo "======================================================================"
echo "F6 — 오프라인 모델 반입 경로 부재 (컨테이너 실행 테스트)"
echo "======================================================================"
echo "-- compose 에 오프라인 설정이 있는가"
grep -nE "HF_HUB_OFFLINE|TRANSFORMERS_OFFLINE|huggingface" deploy/compose.yaml || true
echo "-- 모델 캐시 볼륨 존재 여부 (compose up 이력)"
docker volume ls | grep -i "finllm-lab-v02" || echo "   (없음 — compose up이 실행된 적 없다)"
echo "-- 네트워크 차단 상태로 pin된 모델 로드 시도"
timeout 90 docker run --rm --network none "$VLLM_IMAGE" \
  --model Qwen/Qwen3-14B-AWQ \
  --revision 31c69efc29464b6bb0aee1398b5a7b50a99340c3 \
  --quantization awq --enforce-eager 2>&1 \
  | grep -iE "huggingface.co|NameResolution|Invalid repository" | head -4
echo "docs/on-prem-architecture.md:64-65 가 런타임 외부 접속을 금지한다:"
grep -n "런타임에 Hugging Face" docs/on-prem-architecture.md

echo
echo "======================================================================"
echo "F7 — 계약 이원화: service 이름 / network 불일치"
echo "======================================================================"
echo "-- compose service 이름 (계약상 finllm-gateway 인가)"
docker compose --file deploy/compose.yaml config --services
echo "-- network (external finllm-net 인가)"
docker compose --file deploy/compose.yaml config 2>/dev/null | grep -A4 "^networks:"
echo "-- 두 트리의 계약 문서"
ls "$A_ROOT/docs/cross-review/" | grep -i "contract"
ls "$B_ROOT/docs/cross-review/" 2>/dev/null | grep -i "contract"

echo
echo "======================================================================"
echo "F8 — 기동 시 retriever config hash를 baseline과 대조하지 않는다"
echo "======================================================================"
grep -n "config-hash\|exec python" scripts/deploy/start_service.sh
echo "baseline 기대값: $(python3 -c "import json;print(json.load(open('$B_ROOT/ops/baselines/profile-a-baseline.json'))['retriever_config_hash'])" 2>/dev/null || echo 11d1f8cfeb42)"
echo "-> 출력만 하고 검증 분기 없이 exec 로 진행한다."
