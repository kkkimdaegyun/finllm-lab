"""F9 (MAJOR, 신규) — graceful shutdown이 in-flight 응답을 잘라먹는데 서버는 200으로 기록한다.

원인 경로
    service/runtime.py:300   finally: self._gate.leave()      <- 여기서 gate 해제
    service/http_server.py:108 self._write_json(200, response) <- 응답 쓰기는 그 다음
    service/app.py:64        wait_for_drain() 통과 -> server_close() -> 프로세스 종료
    service/http_server.py:23 daemon_threads = True            <- handler thread를 join하지 않는다

설계 동시성 10에서 관측: 완주 9/10, 1건 IncompleteRead. 서버 로그는 200을 10건 기록.
"""
import os
import signal
import threading
import time

import _common as C

N = int(os.environ.get("FINLLM_REPRO_CONCURRENCY", "10"))

C.kill_leftovers()
C.start_stub("ok")
service = C.start_service()
print(f"기동 확인  /ready={C.probe('/ready')[0]}")

C.set_stub_mode("slow")   # 12초 생성
time.sleep(0.3)

results: list[tuple[int, tuple, float]] = []
lock = threading.Lock()


def one(index: int) -> None:
    started = time.perf_counter()
    outcome = C.post_rag(90)
    with lock:
        results.append((index, outcome, round(time.perf_counter() - started, 2)))


threads = [threading.Thread(target=one, args=(i,)) for i in range(N)]
for thread in threads:
    thread.start()
time.sleep(2.0)

print(f"\n설계 동시성 {N}건 in-flight 상태에서 SIGTERM (각 요청 12초, drain timeout 30초)")
os.kill(service.pid, signal.SIGTERM)
t0 = time.perf_counter()
for thread in threads:
    thread.join(timeout=90)

exited_at = None
for _ in range(300):
    if service.poll() is not None:
        exited_at = round(time.perf_counter() - t0, 2)
        break
    time.sleep(0.1)

ok = [r for r in results if r[1][0] == "OK"]
bad = [r for r in results if r[1][0] != "OK"]
marker = chr(34) + "POST /v1/rag/chat/completions HTTP/1.1" + chr(34) + " 200"
logged_200 = C.service_log().count(marker)

print(f"\n프로세스 종료 : SIGTERM +{exited_at}s")
print(f"클라이언트 완주: {len(ok)}/{N}")
print(f"클라이언트 실패: {len(bad)}/{N}")
for index, outcome, seconds in sorted(bad):
    print(f"   req{index}: {outcome[1]} — {outcome[2]}  ({seconds}s)")
print(f"\n서버가 200으로 로그한 응답 수: {logged_200}")
if logged_200 > len(ok):
    print(f"판정: FAIL — 서버는 {logged_200}건 성공으로 기록했으나 실제 전달은 {len(ok)}건이다.")
    print("      metrics도 request_finished(success=True)로 집계하므로 오류율 SLO가 이를 보지 못한다.")
else:
    print("판정: PASS")
C.stop_all()
