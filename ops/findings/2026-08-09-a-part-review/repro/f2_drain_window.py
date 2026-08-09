"""F2 (MAJOR) — drain 중 /ready=503을 관측할 수 없다.
동시에 F9 (MAJOR) 단일 요청 버전 — in-flight 응답이 잘린다.

2초 간격 폴링으로는 503 창을 놓친다. 0.5초 timeout 연속 폴링으로 측정한다.

관측(3회 반복 동일):
    t=0.002s  /ready=503        <- 창이 0.5초 미만
    t=0.502s  TimeoutError      <- 거부가 아니라 hang. healthcheck timeout을 소진한다
    t=10.02s  ConnectionRefused <- 프로세스 종료
  그리고 in-flight 요청은 IncompleteRead 로 잘린다.
"""
import os
import signal
import threading
import time

import _common as C

C.kill_leftovers()
C.start_stub("ok")
service = C.start_service()
print(f"기동 확인  /ready={C.probe('/ready')[0]}")

C.set_stub_mode("slow")   # 12초 생성
time.sleep(0.3)

inflight: dict = {}


def run_inflight() -> None:
    started = time.perf_counter()
    inflight["result"] = C.post_rag(90)
    inflight["seconds"] = round(time.perf_counter() - started, 2)


thread = threading.Thread(target=run_inflight)
thread.start()
time.sleep(2.0)

print("\nin-flight 1건(12초짜리) 진행 중 SIGTERM. shutdown_timeout=30s")
os.kill(service.pid, signal.SIGTERM)
t0 = time.perf_counter()

transitions, last, exited_at = [], None, None
while time.perf_counter() - t0 < 20.0:
    status, duration = C.probe("/ready", 0.5)
    if status != last:
        transitions.append((round(time.perf_counter() - t0, 3), status, round(duration, 3)))
        last = status
    if exited_at is None and service.poll() is not None:
        exited_at = round(time.perf_counter() - t0, 2)
    if exited_at is not None and time.perf_counter() - t0 > exited_at + 1.5:
        break

thread.join(timeout=60)

print("\n=== SIGTERM 이후 /ready 상태 전이 (새 연결, timeout 0.5s) ===")
print(f"{'t(s)':>8}  {'응답':<24} {'probe 소요(s)':>12}")
for moment, status, duration in transitions:
    print(f"{moment:>8.3f}  {str(status):<24} {duration:>12.3f}")

saw_503 = any(s == 503 for _, s, _ in transitions)
print(f"\n프로세스 종료      : SIGTERM +{exited_at}s")
print(f"503 관측 여부      : {saw_503} (관측되어도 창이 0.5초 미만이면 healthcheck는 놓친다)")
print(f"in-flight 요청 결과: {inflight.get('result')} ({inflight.get('seconds')}s)")
print(f"서버가 200으로 로그: {C.service_log().count(chr(34) + 'POST /v1/rag/chat/completions HTTP/1.1' + chr(34) + ' 200')}건")
C.stop_all()
