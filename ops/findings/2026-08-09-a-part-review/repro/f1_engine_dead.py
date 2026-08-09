"""F1 (BLOCKER) — 추론 엔진이 죽어도 /ready가 200을 유지한다.

기대: 생성이 지속 실패하면 /ready가 503으로 내려간다.
관측: 30초 내내 POST=502, /ready=200.
"""
import time

import _common as C

C.kill_leftovers()
C.start_stub("ok")
C.start_service()
print(f"기동 확인  /ready={C.probe('/ready')[0]}  POST={C.post_rag(20)[1]}")

print("\n엔진 사망 주입 (/v1/models 는 계속 200, /chat/completions 만 500)")
C.set_stub_mode("engine_dead")

failures = 0
for i in range(1, 7):
    _, post_status, _ = C.post_rag(20)
    ready_status, _ = C.probe("/ready")
    ready_body = ""
    if ready_status == 200:
        failures += 1
    print(f"  +{i * 5:>3}s  POST={post_status}  /ready={ready_status}")
    time.sleep(5)

print(f"\n결과: 30초 동안 /ready=200 관측 {failures}/6회. 생성은 전부 502.")
print("판정: FAIL — readiness가 생성 경로를 관측하지 않는다." if failures else "판정: PASS")
C.stop_all()
