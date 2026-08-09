"""결정적 OpenAI-호환 stub 추론 서버.

파일로 모드를 바꾸므로 재시작 없이 동작을 전환할 수 있다.

    ok           /v1/models 200, /v1/chat/completions 200 (즉시)
    engine_dead  /v1/models 200, /v1/chat/completions 500
                 -> "API server는 살아 있는데 생성만 실패" 상태를 모사한다
    slow         /v1/models 200, /v1/chat/completions 200 (12초 지연)

사용: python _stub_vllm.py <mode_file> <port>
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE_FILE = sys.argv[1]
PORT = int(sys.argv[2])
MODEL = "Qwen/Qwen3-14B-AWQ"
SLOW_SECONDS = 12


def mode() -> str:
    try:
        with open(MODE_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return "ok"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/v1/models"):
            # 엔진이 죽어도 이 응답은 정적 설정에서 나온다. 이것이 F1의 핵심이다.
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        current = mode()
        if current == "engine_dead":
            self._send(500, {"error": {"message": "engine core died"}})
            return
        if current == "slow":
            time.sleep(SLOW_SECONDS)
        self._send(200, {"choices": [{"message": {"content": "테스트 답변 [POL-2026-001#제3조]"}}]})

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
