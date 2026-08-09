"""재현 스크립트 공통 기구.

A파트 트리를 읽기만 한다. 어떤 파일도 수정하지 않는다.

환경변수로 경로를 바꿀 수 있다.
    FINLLM_A_ROOT     service/ deploy/ 가 있는 저장소 (기본: 통합 트리)
                      검토 당시 A파트는 별도 트리에 있었고 통합 후 삭제됐다.
    FINLLM_PYTHON     httpx/jsonschema가 설치된 python
    FINLLM_REPRO_WORK 임시 산출물 디렉터리 (기본 /tmp/finllm-repro)
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import time
from pathlib import Path

A_ROOT = Path(os.environ.get("FINLLM_A_ROOT", "/home/dgkim/dgkim/finllm-lab"))
PYTHON = os.environ.get("FINLLM_PYTHON", "/home/dgkim/dgkim/new_project/.venv/bin/python")
WORK = Path(os.environ.get("FINLLM_REPRO_WORK", "/tmp/finllm-repro"))
HERE = Path(__file__).resolve().parent

STUB_PORT = int(os.environ.get("FINLLM_REPRO_STUB_PORT", "8009"))
SVC_PORT = int(os.environ.get("FINLLM_REPRO_SVC_PORT", "8085"))

RAG_REQUEST = json.dumps({"question": "고액현금거래 보고 기한은?", "role": "branch-staff"})

_procs: list[subprocess.Popen] = []


def _mode_file() -> Path:
    return WORK / "stub_mode"


def set_stub_mode(mode: str) -> None:
    """ok | engine_dead | slow — 재시작 없이 stub 동작을 바꾼다."""
    _mode_file().write_text(mode + "\n", encoding="utf-8")


def kill_leftovers() -> None:
    # 패턴에 대괄호를 쓰는 이유: pkill -f 는 자기 자신의 명령줄도 훑기 때문에
    # 스크립트를 인라인 실행하는 셸까지 죽일 수 있다.
    for pattern in ("[_]stub_vllm.py", "[s]ervice.app"):
        subprocess.run(["pkill", "-f", pattern], check=False)
    time.sleep(1)


def ensure_index() -> Path:
    """A파트가 기동 시 만드는 것과 동일한 index를 만든다."""
    WORK.mkdir(parents=True, exist_ok=True)
    index = WORK / "index.json"
    if not index.exists():
        subprocess.run(
            [PYTHON, "scripts/rag_index.py", "build",
             "--corpus", "corpus/v0.1", "--output", str(index)],
            cwd=A_ROOT, check=True, stdout=subprocess.DEVNULL,
        )
    return index


def start_stub(mode: str = "ok") -> subprocess.Popen:
    WORK.mkdir(parents=True, exist_ok=True)
    set_stub_mode(mode)
    proc = subprocess.Popen(
        [PYTHON, str(HERE / "_stub_vllm.py"), str(_mode_file()), str(STUB_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _procs.append(proc)
    time.sleep(2)
    return proc


def start_service(**extra_env: str) -> subprocess.Popen:
    index = ensure_index()
    env = dict(
        os.environ,
        FINLLM_INDEX_PATH=str(index),
        FINLLM_INFERENCE_BASE_URL=f"http://127.0.0.1:{STUB_PORT}/v1",
        FINLLM_PORT=str(SVC_PORT),
        FINLLM_SHUTDOWN_TIMEOUT_SECONDS="30",
    )
    env.update(extra_env)
    log = open(WORK / "service.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [PYTHON, "-m", "service.app"], cwd=A_ROOT, env=env,
        stdout=log, stderr=subprocess.STDOUT,
    )
    _procs.append(proc)
    for _ in range(60):
        time.sleep(0.5)
        if probe("/ready", 1.0)[0] == 200:
            return proc
    raise RuntimeError(f"서비스가 준비되지 않았다. 로그: {WORK / 'service.log'}")


def probe(path: str, timeout: float = 2.0) -> tuple[object, float]:
    """(HTTP status 또는 예외 이름, 소요초)"""
    started = time.perf_counter()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", SVC_PORT, timeout=timeout)
        conn.request("GET", path)
        status = conn.getresponse().status
        conn.close()
        return status, time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001 - 예외 종류 자체가 관측 대상이다
        return type(exc).__name__, time.perf_counter() - started


def post_rag(timeout: float = 90.0) -> tuple[str, object, object]:
    """('OK', status, body_len) 또는 ('FAIL', 예외이름, 메시지)"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", SVC_PORT, timeout=timeout)
        conn.request("POST", "/v1/rag/chat/completions", body=RAG_REQUEST,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        body = response.read()
        return "OK", response.status, len(body)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", type(exc).__name__, str(exc)[:70]


def service_log() -> str:
    path = WORK / "service.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def stop_all() -> None:
    for proc in _procs:
        if proc.poll() is None:
            proc.terminate()
    time.sleep(1)
    for proc in _procs:
        if proc.poll() is None:
            proc.kill()
    _procs.clear()
