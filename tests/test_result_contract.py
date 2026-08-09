"""Regression tests for the result contract and the evidence separation.

These cover defects found in v0.1: the published JSON Schema was never applied,
`new-result` emitted records that violated it, `native-gpu-validation` could be
claimed on the emulation host, TTFT hid client queueing, and the serve command
left the tokenizer revision unpinned.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "finllm_profile.py"


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile_cli = import_script("finllm_profile", CLI)
load_test = import_script("load_test", ROOT / "scripts" / "load_test.py")
capture_environment = import_script(
    "capture_environment", ROOT / "scripts" / "capture_environment.py"
)

CONFIG = profile_cli.load_config()


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def make_template(directory: Path, *extra: str, **overrides: str) -> dict[str, Any]:
    output = directory / "record.json"
    completed = run_cli(
        "new-result",
        "--profile",
        overrides.get("profile", "profile-a"),
        "--model",
        overrides.get("model", "Qwen/Qwen3-14B-AWQ"),
        "--revision",
        "31c69efc29464b6bb0aee1398b5a7b50a99340c3",
        "--quantization",
        "awq",
        "--evidence",
        overrides.get("evidence", "memory-budget-emulation"),
        "--output",
        str(output),
        *extra,
    )
    if completed.returncode != 0:
        raise AssertionError(f"new-result failed: {completed.stderr}")
    return json.loads(output.read_text(encoding="utf-8"))


def fill_placeholders(record: dict[str, Any]) -> dict[str, Any]:
    """Turn a template into a plausibly complete, gate-passing record."""
    record["hardware"]["driver_version"] = "535.288.01"
    record["hardware"]["cuda_version"] = "12.2"
    record["software"]["vllm_version"] = "0.0.0-test"
    record["software"]["torch_version"] = "2.5.1"
    record["vllm"]["command"] = "vllm serve test"
    record["rag"] = {
        "corpus_version": "corpus-v0.1",
        "eval_set_version": "eval-v0.1",
        "retriever_config_hash": "0" * 12,
        "prompt_revision": "prompt-v0.1",
    }
    record["metrics"].update(
        {
            "quality_score": 92,
            "p50_ttft_ms": 300,
            "p95_ttft_ms": 1600,
            "p95_e2e_ms": 4000,
            "aggregate_output_tokens_per_s": 120,
            "peak_vram_gib": 20.5,
            "error_rate": 0.0,
            "oom_count": 0,
        }
    )
    record["decision"]["reason"] = "test fixture"
    return record


class ResultTemplateTests(unittest.TestCase):
    def test_emulation_template_satisfies_published_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = make_template(Path(directory))
        self.assertEqual(profile_cli.schema_errors(record), [])

    def test_native_template_satisfies_published_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = make_template(
                Path(directory), evidence="native-gpu-validation"
            )
        self.assertEqual(profile_cli.schema_errors(record), [])
        self.assertEqual(record["vllm"]["memory_budget_mode"], "native")
        self.assertEqual(record["hardware"]["physical_vram_gib"], 24)

    def test_template_inherits_parameter_size_from_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = make_template(Path(directory))
        self.assertEqual(record["model"]["parameter_billions"], 14.8)
        self.assertEqual(
            record["model"]["tokenizer_revision"], record["model"]["revision"]
        )

    def test_unlisted_model_requires_explicit_parameter_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = run_cli(
                "new-result",
                "--profile",
                "profile-a",
                "--model",
                "vendor/not-in-candidates",
                "--revision",
                "a" * 40,
                "--quantization",
                "awq",
                "--evidence",
                "memory-budget-emulation",
                "--output",
                str(Path(directory) / "record.json"),
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--parameter-billions", completed.stderr)

    def test_deployment_matched_template_uses_matched_utilization(self) -> None:
        expected = CONFIG["deployment_profiles"]["profile-a"][
            "deployment_matched_a6000_utilization"
        ]
        with tempfile.TemporaryDirectory() as directory:
            record = make_template(
                Path(directory), "--budget-mode", "deployment-matched"
            )
        self.assertEqual(record["vllm"]["memory_budget_mode"], "deployment-matched")
        self.assertAlmostEqual(record["vllm"]["gpu_memory_utilization"], expected)


class EvidenceSeparationTests(unittest.TestCase):
    def validate(self, record: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8"
            )
            return run_cli("validate-result", str(path))

    def test_filled_emulation_record_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(make_template(Path(directory)))
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Gate result: PASS", completed.stdout)

    def test_a6000_run_cannot_be_relabelled_as_native(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(make_template(Path(directory)))
        record["evidence_type"] = "native-gpu-validation"
        record["claim_scope"] = "native-target-gpu-performance"
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("emulation host", completed.stderr)

    def test_native_record_must_match_the_profile_card_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(
                make_template(Path(directory), evidence="native-gpu-validation")
            )
        record["hardware"]["gpu_model"] = "NVIDIA GeForce RTX 4090"
        record["hardware"]["physical_vram_gib"] = 48
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("~24GB card", completed.stderr)

    def test_unwritten_decision_reason_is_rejected(self) -> None:
        """Found by the 2026-08-08b sweep: nine records validated OK while every
        `decision.reason` was still FILL_ME, because the field was missing from
        the required list."""
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(make_template(Path(directory)))
        record["decision"]["reason"] = "FILL_ME"
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("placeholder remains: decision.reason", completed.stderr)

    def test_annotated_placeholder_is_also_rejected(self) -> None:
        """The equality check let "FILL_ME: <instruction>" through, so nine
        records from the 2026-08-08b sweep validated with no rationale."""
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(make_template(Path(directory)))
        record["decision"]["reason"] = "FILL_ME: 측정값을 보고 직접 작성한다"
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("placeholder remains: decision.reason", completed.stderr)

    def test_schema_violation_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = fill_placeholders(make_template(Path(directory)))
        record["metrics"]["error_rate"] = 7.5  # schema maximum is 1
        completed = self.validate(record)
        self.assertEqual(completed.returncode, 1)
        self.assertIn("schema: metrics.error_rate", completed.stderr)


class ServeCommandTests(unittest.TestCase):
    def test_serve_command_pins_tokenizer_revision(self) -> None:
        revision = "b968826d9c46dd6066d109eabc6255188de91218"
        completed = run_cli(
            "serve-command",
            "--profile",
            "profile-a",
            "--model",
            "Qwen/Qwen3-8B",
            "--revision",
            revision,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f"--tokenizer-revision {revision}", completed.stdout)


class EnforceEagerTests(unittest.TestCase):
    def test_flag_is_absent_by_default(self) -> None:
        completed = run_cli(
            "serve-command", "--profile", "profile-a", "--model", "Qwen/Qwen3-8B",
            "--revision", "b" * 40,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("--enforce-eager", completed.stdout)

    def test_flag_is_emitted_when_requested(self) -> None:
        completed = run_cli(
            "serve-command", "--profile", "profile-a", "--model", "Qwen/Qwen3-8B",
            "--revision", "b" * 40, "--enforce-eager",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--enforce-eager", completed.stdout)


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _FakeResponse:
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def stream(self, *args: object, **kwargs: object) -> _FakeStream:
        return _FakeStream(self._lines)


class QueueWaitTests(unittest.TestCase):
    def test_client_queue_time_is_reported_separately(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"근거"}}]}',
            'data: {"choices":[{"delta":{"content":" 조항"}}]}',
            'data: {"usage":{"completion_tokens":2}}',
            "data: [DONE]",
        ]

        async def scenario() -> dict[str, Any]:
            semaphore = asyncio.Semaphore(1)
            await semaphore.acquire()
            task = asyncio.create_task(
                load_test.send_one(
                    _FakeClient(lines),
                    semaphore,
                    "http://127.0.0.1:8000/v1/chat/completions",
                    "token",
                    "test-model",
                    {"id": "case-1", "messages": [{"role": "user", "content": "q"}]},
                    64,
                    0,
                    False,
                    0.7,
                    0.8,
                    20,
                    0.0,
                    42,
                )
            )
            await asyncio.sleep(0.08)
            semaphore.release()
            return await task

        sample = asyncio.run(scenario())
        self.assertIsNone(sample["error"])
        self.assertGreaterEqual(sample["client_queue_ms"], 70)
        # The server-side view must not absorb the queue wait...
        self.assertLess(sample["ttft_ms"], sample["client_queue_ms"])
        # ...and the user-facing view must include it.
        self.assertAlmostEqual(
            sample["user_ttft_ms"],
            sample["client_queue_ms"] + sample["ttft_ms"],
            delta=1.0,
        )


class EnvironmentRedactionTests(unittest.TestCase):
    def test_process_table_is_removed(self) -> None:
        raw = (
            "|   0  NVIDIA RTX A6000  Off | 2757MiB / 49140MiB |\n"
            "| Processes:                                     |\n"
            "|    0   N/A  4175087  C  /opt/conda/bin/python3.11  1374MiB |\n"
        )
        redacted = capture_environment.redact_process_table(raw)
        self.assertIn("NVIDIA RTX A6000", redacted)
        self.assertNotIn("/opt/conda", redacted)
        self.assertNotIn("4175087", redacted)
        self.assertIn("[process table redacted]", redacted)

    def test_text_without_a_process_table_is_unchanged(self) -> None:
        raw = "|   0  NVIDIA RTX A6000  Off |"
        self.assertEqual(capture_environment.redact_process_table(raw), raw)


if __name__ == "__main__":
    unittest.main()
