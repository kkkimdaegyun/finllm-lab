"""F4 (MAJOR) — listen backlog 5로 연결이 무성 드롭되고 metric에 집계되지 않는다.

두 가지를 한다.
  (1) 통제 실험: 동일 코드에서 request_queue_size만 5 -> 512로 바꿔 드롭이 사라지는지 확인
  (2) HTTP 백엔드 조건에서 동시성별 드롭률과 finllm_requests_total 증가분 대조

드롭률은 백엔드 속도에 의존한다. in-process fake보다 HTTP stub이 훨씬 나쁘고,
실제 vLLM은 stub보다 느리므로 더 나쁠 가능성이 높다(미측정).
"""
import collections
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import _common as C

sys.path.insert(0, str(C.A_ROOT))


def burst(url: str, n: int, timeout: float = 30.0) -> collections.Counter:
    codes: collections.Counter = collections.Counter()
    lock = threading.Lock()

    def post() -> None:
        request = urllib.request.Request(
            url, data=C.RAG_REQUEST.encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
        with lock:
            codes[code] += 1

    threads = [threading.Thread(target=post) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return codes


# ---------------------------------------------------------------- (1) 통제 실험
print("=" * 72)
print("(1) 통제 실험 — backlog 변수 하나만 바꾼다")
print("=" * 72)
from service.http_server import FinLLMHTTPServer      # noqa: E402
from service.runtime import ServiceRuntime            # noqa: E402
from tests.service_test_support import FakeInference, make_config  # noqa: E402


class BiggerBacklogServer(FinLLMHTTPServer):
    request_queue_size = 512


print(f"  FinLLMHTTPServer.request_queue_size = {FinLLMHTTPServer.request_queue_size} "
      f"(재정의 여부: {'request_queue_size' in FinLLMHTTPServer.__dict__})")

drops_by_backlog = {}
for label, server_class in (("원본 backlog=5", FinLLMHTTPServer),
                            ("동일코드 backlog=512", BiggerBacklogServer)):
    directory = tempfile.TemporaryDirectory()
    runtime = ServiceRuntime(make_config(Path(directory.name)), inference=FakeInference())
    runtime.initialize()
    server = server_class(("127.0.0.1", 0), runtime)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)
    codes = burst(f"http://127.0.0.1:{port}/v1/rag/chat/completions", 120)
    dropped = sum(v for k, v in codes.items() if isinstance(k, str))
    drops_by_backlog[label] = dropped
    print(f"  {label:22} 200={codes.get(200, 0):>4}  드롭={dropped:>4}  {dict(codes)}")
    server.shutdown(); server.server_close()
    runtime.begin_shutdown(); runtime.wait_for_drain(); runtime.close()
    directory.cleanup()

a, b = drops_by_backlog["원본 backlog=5"], drops_by_backlog["동일코드 backlog=512"]
print(f"\n  드롭 {a} -> {b}. " + ("가설 확인: 원인은 backlog다." if a > 0 and b == 0
                                  else "가설 반증: 원인은 backlog가 아니다."))

# ---------------------------------------------------------------- (2) HTTP 백엔드
print()
print("=" * 72)
print("(2) HTTP 백엔드 조건 — 동시성별 드롭률과 metric 집계 대조")
print("=" * 72)
C.kill_leftovers()
C.start_stub("ok")
C.start_service()
base = f"http://127.0.0.1:{C.SVC_PORT}"


def metric(name: str) -> float | None:
    text = urllib.request.urlopen(f"{base}/metrics", timeout=10).read().decode("utf-8")
    match = re.search(rf"^{re.escape(name)} (\S+)$", text, re.M)
    return float(match.group(1)) if match else None


print(f"{'동시':>5} {'200':>5} {'드롭':>5} {'드롭률':>8}  requests_total 증가분")
for n in (10, 30, 60, 120):
    before = metric("finllm_requests_total")
    codes = burst(f"{base}/v1/rag/chat/completions", n)
    after = metric("finllm_requests_total")
    dropped = sum(v for k, v in codes.items() if isinstance(k, str))
    print(f"{n:>5} {codes.get(200, 0):>5} {dropped:>5} {dropped / n * 100:>7.1f}%  +{after - before:.0f}")
    time.sleep(1)

print("\n드롭된 연결은 route에 도달하지 못해 어떤 metric에도 남지 않는다.")
C.stop_all()
