#!/usr/bin/env python3
"""Capture a reproducible, non-secret runtime environment manifest."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def command_output(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def redact_process_table(text: str) -> str:
    """Drop the nvidia-smi process table.

    On a shared host it lists other users' PIDs and interpreter paths, which
    contradicts this manifest's own promise to exclude host identity.
    """
    marker = text.find("| Processes:")
    if marker == -1:
        return text
    return text[:marker].rstrip() + "\n[process table redacted]"


def package_versions(names: list[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def build_manifest() -> dict[str, Any]:
    gpu_query = command_output(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,driver_version,power.limit",
            "--format=csv,noheader,nounits",
        ]
    )
    nvidia_smi = command_output(["nvidia-smi"])
    if isinstance(nvidia_smi.get("stdout"), str):
        nvidia_smi["stdout"] = redact_process_table(nvidia_smi["stdout"])
    return {
        "schema_version": "1.0.0",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "host_identity_included": False,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version,
        },
        "packages": package_versions(
            ["vllm", "torch", "transformers", "httpx"]
        ),
        "gpu_query_columns": [
            "index",
            "name",
            "memory.total_mib",
            "driver_version",
            "power.limit_w",
        ],
        "gpu_query": gpu_query,
        "nvidia_smi": nvidia_smi,
        "notes": (
            "Hostname, username, environment variables, tokens, model-cache "
            "paths, and the nvidia-smi process table are intentionally excluded."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    if not manifest["gpu_query"]["ok"]:
        print(
            "WARNING: GPU query failed; run this command on the NVIDIA GPU host.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

