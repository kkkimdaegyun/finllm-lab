#!/usr/bin/env python3
"""Promote, roll back, and verify a FinLLM release.

A rollback plan that exists only as prose has never been executed, and a
procedure that has never been executed does not work. This is the executable
version.

    python3 scripts/rollback_release.py list
    python3 scripts/rollback_release.py current
    python3 scripts/rollback_release.py promote --manifest ops/release/history/<id>.json
    python3 scripts/rollback_release.py rollback --to <release-id> --reason "..."
    python3 scripts/rollback_release.py verify

Two invariants this enforces, because they are the ones that get skipped under
pressure:

1. A release whose regression gate did not pass cannot be promoted.
2. Every rollback is appended to an immutable log with a reason. "왜 되돌렸는가"가
   없는 rollback은 다음 사람에게 아무것도 알려주지 않는다.

The restart mechanism is deliberately pluggable. Today the active release's
`runtime.restart_command` drives vLLM directly. When the A파트 컨테이너가 들어오면
그 값이 compose 명령으로 바뀌고, 이 스크립트는 그대로 쓴다.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "ops" / "release"
HISTORY_DIR = RELEASE_DIR / "history"
CURRENT_PATH = RELEASE_DIR / "current-release.json"
LOG_PATH = RELEASE_DIR / "rollback-log.jsonl"
SCHEMA_PATH = ROOT / "schemas" / "release-manifest.schema.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def schema_errors(manifest: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ModuleNotFoundError:
        raise SystemExit(
            "jsonschema가 필요하다. python3 -m pip install -e . 를 실행하라."
        )
    validator = jsonschema.Draft202012Validator(read_json(SCHEMA_PATH))
    return [
        f"{'.'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    ]


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []
    releases = []
    for path in sorted(HISTORY_DIR.glob("*.json")):
        manifest = read_json(path)
        manifest["_path"] = str(path.relative_to(ROOT))
        releases.append(manifest)
    return releases


def current_release() -> dict[str, Any] | None:
    return read_json(CURRENT_PATH) if CURRENT_PATH.exists() else None


def append_log(event: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- commands


def command_list(args: argparse.Namespace) -> int:
    releases = load_history()
    if not releases:
        print("ops/release/history/ 에 release manifest가 없다")
        return 1
    active = current_release()
    active_id = active["release_id"] if active else None
    print(f"{'':2} {'RELEASE ID':28} {'STATUS':12} {'GATE':10} {'QUALITY':>8}  MODEL REVISION")
    print("-" * 104)
    for manifest in releases:
        gate = manifest["regression_gate"]
        marker = "->" if manifest["release_id"] == active_id else "  "
        quality = gate.get("quality_score")
        print(
            f"{marker} {manifest['release_id']:28} {manifest['status']:12} "
            f"{gate['status']:10} {(f'{quality:.3f}' if quality is not None else '-'):>8}  "
            f"{manifest['model']['revision'][:12]}"
        )
    print("\n-> 표시가 현재 active release다")
    return 0


def command_current(args: argparse.Namespace) -> int:
    manifest = current_release()
    if manifest is None:
        print("active release 없음")
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _validate_promotable(manifest: dict[str, Any], allow_failed_gate: bool) -> list[str]:
    problems = schema_errors(manifest)
    gate = manifest.get("regression_gate", {})
    if gate.get("status") != "pass" and not allow_failed_gate:
        problems.append(
            f"regression gate가 통과하지 않았다 (status={gate.get('status')!r}). "
            "gate를 통과시키거나, 장애 대응 중이라면 --allow-failed-gate와 이유를 남겨라."
        )
    report = gate.get("report")
    if report and not (ROOT / report).exists():
        problems.append(f"gate 리포트 파일이 없다: {report}")
    return problems


def command_promote(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest)
    problems = _validate_promotable(manifest, args.allow_failed_gate)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    previous = current_release()
    manifest["status"] = "active"
    manifest["supersedes"] = previous["release_id"] if previous else None

    if previous and previous["release_id"] != manifest["release_id"]:
        previous_path = HISTORY_DIR / f"{previous['release_id']}.json"
        if previous_path.exists():
            stored = read_json(previous_path)
            stored["status"] = "superseded"
            write_json(previous_path, stored)

    write_json(HISTORY_DIR / f"{manifest['release_id']}.json", manifest)
    write_json(CURRENT_PATH, manifest)
    append_log(
        {
            "at_utc": now(),
            "action": "promote",
            "release_id": manifest["release_id"],
            "from_release_id": previous["release_id"] if previous else None,
            "gate_status": manifest["regression_gate"]["status"],
            "gate_report": manifest["regression_gate"].get("report"),
            "reason": args.reason,
            "allow_failed_gate": bool(args.allow_failed_gate),
        }
    )
    print(f"promoted: {manifest['release_id']}")
    return 0


def command_rollback(args: argparse.Namespace) -> int:
    target_path = HISTORY_DIR / f"{args.to}.json"
    if not target_path.exists():
        print(f"ERROR: release manifest 없음: {target_path}", file=sys.stderr)
        print("사용 가능한 release는 `rollback_release.py list`로 확인한다", file=sys.stderr)
        return 1

    target = read_json(target_path)
    previous = current_release()

    if previous and previous["release_id"] == target["release_id"]:
        print(f"ERROR: {args.to} 는 이미 active다", file=sys.stderr)
        return 1

    # 되돌릴 대상은 known-good이어야 한다. 통과한 적 없는 release로 되돌리는 것은
    # rollback이 아니라 또 다른 배포다.
    problems = _validate_promotable(target, args.allow_failed_gate)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    started = time.time()
    restart_command = target.get("runtime", {}).get("restart_command")

    print(f"rollback: {previous['release_id'] if previous else '(none)'} -> {target['release_id']}")
    print(f"reason  : {args.reason}")
    print(f"model   : {target['model']['id']} @ {target['model']['revision'][:12]}")
    print(f"prompt  : {target['rag']['prompt_revision']}   retriever: {target['rag']['retriever_config_hash']}")

    executed = False
    exec_returncode = None
    if restart_command:
        if args.exec:
            print(f"\n실행: {restart_command}")
            completed = subprocess.run(restart_command, shell=True, cwd=ROOT, check=False)
            exec_returncode = completed.returncode
            executed = True
            if exec_returncode != 0:
                print(f"WARNING: restart 명령이 {exec_returncode}로 끝났다", file=sys.stderr)
        else:
            print("\n다음 명령으로 서비스를 이 release로 되돌린다 (--exec 를 주면 여기서 실행한다):")
            print(f"  {restart_command}")
    else:
        print("\nWARNING: runtime.restart_command가 없다. 수동으로 재기동해야 한다.", file=sys.stderr)

    if previous:
        previous_path = HISTORY_DIR / f"{previous['release_id']}.json"
        if previous_path.exists():
            stored = read_json(previous_path)
            stored["status"] = "rolled-back"
            write_json(previous_path, stored)

    target["status"] = "active"
    write_json(HISTORY_DIR / f"{target['release_id']}.json", target)
    write_json(CURRENT_PATH, target)

    event = {
        "at_utc": now(),
        "action": "rollback",
        "release_id": target["release_id"],
        "from_release_id": previous["release_id"] if previous else None,
        "reason": args.reason,
        "restart_command": restart_command,
        "executed": executed,
        "exec_returncode": exec_returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "incident": args.incident,
    }
    append_log(event)
    print(f"\ncurrent-release.json -> {target['release_id']}")
    print(f"rollback-log.jsonl 에 기록됨")
    print("\n복구 확인은 프로세스 생존이 아니라 metric으로 한다:")
    print("  python3 scripts/rollback_release.py verify")
    return 0


def _http_get(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except Exception as error:  # noqa: BLE001
        return 0, f"{type(error).__name__}: {error}"


def command_verify(args: argparse.Namespace) -> int:
    """선언된 release와 실제로 돌고 있는 것이 같은가.

    rollback 후 이 둘이 어긋나면 어떤 버전이 서비스 중인지 아무도 모른다.
    """
    manifest = current_release()
    if manifest is None:
        print("ERROR: active release 없음", file=sys.stderr)
        return 1

    print(f"declared release: {manifest['release_id']}")
    print(f"declared model  : {manifest['model']['id']} @ {manifest['model']['revision'][:12]}")

    failures: list[str] = []

    status, body = _http_get(f"{args.base_url.rstrip('/')}/models", args.timeout)
    if status != 200:
        failures.append(f"{args.base_url}/models 에 접근할 수 없다 ({status} {body[:120]})")
    else:
        try:
            served = [entry["id"] for entry in json.loads(body).get("data", [])]
        except json.JSONDecodeError:
            served = []
        print(f"served models   : {served}")
        if manifest["model"]["id"] not in served:
            failures.append(
                f"선언된 모델 {manifest['model']['id']} 이 서비스 중이 아니다. 실제: {served}"
            )

    # A파트가 배포되어 있으면 build_info의 라벨과 manifest가 일치해야 한다.
    if args.metrics_url:
        status, body = _http_get(args.metrics_url, args.timeout)
        if status != 200:
            print(f"note: {args.metrics_url} 없음 — A파트 미배포로 간주 (검증 생략)")
        else:
            for field, value in (
                ("model_revision", manifest["model"]["revision"]),
                ("prompt_revision", manifest["rag"]["prompt_revision"]),
                ("retriever_config_hash", manifest["rag"]["retriever_config_hash"]),
            ):
                if f'{field}="{value}"' not in body:
                    failures.append(f"finllm_build_info의 {field}가 manifest와 다르다 (기대: {value})")

    if failures:
        print("\nVERIFY: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nVERIFY: OK — 선언된 release와 실제 서비스가 일치한다")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="release 목록과 현재 active 표시")
    sub.add_parser("current", help="현재 active release manifest 출력")

    promote = sub.add_parser("promote", help="release를 active로 승격")
    promote.add_argument("--manifest", type=Path, required=True)
    promote.add_argument("--reason", default="")
    promote.add_argument(
        "--allow-failed-gate",
        action="store_true",
        help="gate 미통과 release를 승격한다. 장애 대응 외에는 쓰지 마라 — 로그에 남는다",
    )

    rollback = sub.add_parser("rollback", help="이전 known-good release로 되돌린다")
    rollback.add_argument("--to", required=True, help="대상 release_id")
    rollback.add_argument("--reason", required=True, help="왜 되돌리는가 (필수)")
    rollback.add_argument("--incident", default=None, help="관련 incident id (예: INC-001)")
    rollback.add_argument("--exec", action="store_true", help="restart_command를 실제로 실행한다")
    rollback.add_argument("--allow-failed-gate", action="store_true")

    verify = sub.add_parser("verify", help="선언된 release와 실제 서비스가 일치하는지 확인")
    verify.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    verify.add_argument("--metrics-url", default="http://127.0.0.1:8080/metrics")
    verify.add_argument("--timeout", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return {
        "list": command_list,
        "current": command_current,
        "promote": command_promote,
        "rollback": command_rollback,
        "verify": command_verify,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
