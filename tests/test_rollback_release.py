"""Rollback must be fail-closed and commit state only after verification."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import rollback_release


def manifest(release_id: str, restart_command: str) -> dict:
    return {
        "release_id": release_id,
        "model": {"id": "Qwen/Qwen3-14B-AWQ", "revision": "1" * 40},
        "rag": {"prompt_revision": "prompt-v0.1", "retriever_config_hash": "11d1f8cfeb42"},
        "runtime": {"restart_command": restart_command},
    }


def args(*, execute: bool) -> argparse.Namespace:
    return argparse.Namespace(
        to="target",
        reason="deterministic test",
        incident="TEST",
        exec=execute,
        allow_failed_gate=False,
        base_url="http://unused/v1",
        ready_url="http://unused/ready",
        metrics_url="http://unused/metrics",
        request_timeout=0.1,
        verify_timeout=0.1,
    )


class RollbackAtomicityTests(unittest.TestCase):
    def run_in_temp(self, restart_command: str, *, execute: bool) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history"
            history.mkdir()
            current_path = root / "current.json"
            log_path = root / "rollback.jsonl"
            current = manifest("current", "true")
            target = manifest("target", restart_command)
            current_path.write_text(json.dumps(current), encoding="utf-8")
            (history / "target.json").write_text(json.dumps(target), encoding="utf-8")
            with (
                mock.patch.object(rollback_release, "ROOT", root),
                mock.patch.object(rollback_release, "HISTORY_DIR", history),
                mock.patch.object(rollback_release, "CURRENT_PATH", current_path),
                mock.patch.object(rollback_release, "LOG_PATH", log_path),
                mock.patch.object(rollback_release, "_validate_promotable", return_value=[]),
                mock.patch.object(rollback_release, "_verify_manifest", return_value=[]),
            ):
                result = rollback_release.command_rollback(args(execute=execute))
            return result, json.loads(current_path.read_text(encoding="utf-8"))

    def test_failed_restart_does_not_mutate_current_release(self) -> None:
        result, current = self.run_in_temp("false", execute=True)
        self.assertEqual(result, 1)
        self.assertEqual(current["release_id"], "current")

    def test_dry_run_does_not_mutate_current_release(self) -> None:
        result, current = self.run_in_temp("true", execute=False)
        self.assertEqual(result, 0)
        self.assertEqual(current["release_id"], "current")

    def test_verify_requires_ready_and_metrics(self) -> None:
        candidate = {
            "source": {"git_sha": "1" * 40, "image_digest": "sha256:" + "2" * 64},
            "model": {
                "id": "Qwen/Qwen3-14B-AWQ",
                "revision": "3" * 40,
                "tokenizer_revision": "3" * 40,
            },
            "rag": {
                "prompt_revision": "prompt-v0.1",
                "eval_set_version": "eval-v0.1",
                "retriever_config_hash": "11d1f8cfeb42",
            },
        }
        responses = [
            (200, json.dumps({"data": [{"id": candidate["model"]["id"]}]})),
            (503, ""),
            (503, ""),
        ]
        with mock.patch.object(rollback_release, "_http_get", side_effect=responses):
            failures = rollback_release._verify_manifest(
                candidate,
                base_url="http://service/v1",
                ready_url="http://service/ready",
                metrics_url="http://service/metrics",
                timeout=0.1,
            )
        self.assertTrue(any("ready" in failure for failure in failures))
        self.assertTrue(any("provenance" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
