#!/usr/bin/env python3
"""Sample GPU memory during a run so peak VRAM is measured, not estimated.

`nvidia-smi` at the end of a benchmark shows the memory still held, not the
maximum reached, and vLLM's own reservation makes the two differ. Run this
alongside the load test and stop it when the test finishes.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType
from typing import Any


MIB_PER_GIB = 1024


def query_used_mib(gpu_index: int) -> int | None:
    """Memory currently in use on one GPU, in MiB."""
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_index}",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return parse_used_mib(completed.stdout)


def parse_used_mib(output: str) -> int | None:
    line = output.strip().splitlines()[0].strip() if output.strip() else ""
    if not line:
        return None
    try:
        return int(float(line))
    except ValueError:
        return None


def build_report(
    gpu_index: int, samples: list[int], started: str, failures: int
) -> dict[str, Any]:
    peak = max(samples) if samples else 0
    return {
        "gpu_index": gpu_index,
        "started_utc": started,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "failed_query_count": failures,
        "peak_vram_mib": peak,
        "peak_vram_gib": round(peak / MIB_PER_GIB, 3),
        "mean_vram_gib": (
            round(sum(samples) / len(samples) / MIB_PER_GIB, 3) if samples else 0
        ),
        "note": (
            "Whole-GPU usage, so anything else running on this GPU is included. "
            "Run on an otherwise idle GPU for a clean number."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop after this many seconds; omit to run until interrupted",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    samples: list[int] = []
    failures = 0
    started = datetime.now(timezone.utc).isoformat()
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    deadline = time.monotonic() + args.duration if args.duration else None
    while not stopping:
        used = query_used_mib(args.gpu_index)
        if used is None:
            failures += 1
        else:
            samples.append(used)
        if deadline is not None and time.monotonic() >= deadline:
            break
        time.sleep(args.interval)

    report = build_report(args.gpu_index, samples, started, failures)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not samples:
        print("WARNING: no successful GPU query", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
