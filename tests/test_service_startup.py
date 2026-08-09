"""Fail-fast startup validation tests that do not require a GPU."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from service.config import ServiceConfig, StartupValidationError
from service.runtime import ServiceRuntime
from tests.service_test_support import FakeInference, make_config


class ServiceStartupTests(unittest.TestCase):
    def test_missing_config_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                StartupValidationError, "config file does not exist"
            ):
                ServiceConfig.load(Path(directory) / "missing.json", environ={})

    def test_missing_index_fails_during_config_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            payload = json.loads(config.source_path.read_text(encoding="utf-8"))
            payload["rag"]["index_path"] = str(Path(directory) / "missing-index.json")
            config.source_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                StartupValidationError, "retriever index does not exist"
            ):
                ServiceConfig.load(config.source_path, environ={})

    def test_empty_index_fails_retriever_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            config.index_path.write_text(
                json.dumps({"corpus_version": "v0.1", "chunks": []}),
                encoding="utf-8",
            )
            runtime = ServiceRuntime(config, inference=FakeInference())
            with self.assertRaisesRegex(StartupValidationError, "contains no chunks"):
                runtime.initialize()
            runtime.close()

    def test_index_corpus_version_mismatch_fails_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            payload = json.loads(config.index_path.read_text(encoding="utf-8"))
            payload["corpus_version"] = "v9.9"
            config.index_path.write_text(json.dumps(payload), encoding="utf-8")
            runtime = ServiceRuntime(config, inference=FakeInference())
            with self.assertRaisesRegex(StartupValidationError, "corpus_version"):
                runtime.initialize()
            runtime.close()

    def test_unreachable_inference_fails_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory), startup_timeout_seconds=0)
            runtime = ServiceRuntime(
                config, inference=FakeInference(reachable=False)
            )
            with self.assertRaisesRegex(
                StartupValidationError, "inference startup validation failed"
            ):
                runtime.initialize()
            runtime.close()

    def test_unknown_prompt_revision_fails_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory), prompt_revision="prompt-unknown")
            runtime = ServiceRuntime(config, inference=FakeInference())
            with self.assertRaisesRegex(
                StartupValidationError, "invalid prompt configuration"
            ):
                runtime.initialize()
            runtime.close()

    def test_unpinned_model_revision_fails_config_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            payload = json.loads(config.source_path.read_text(encoding="utf-8"))
            payload["inference"]["model_revision"] = "main"
            config.source_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                StartupValidationError, "40-character commit SHA"
            ):
                ServiceConfig.load(config.source_path, environ={})

    def test_invalid_scalar_types_do_not_silently_coerce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            original = json.loads(config.source_path.read_text(encoding="utf-8"))
            cases = (
                (("service", "port"), 8080.5, "must be an integer"),
                (("lifecycle", "startup_timeout_seconds"), float("inf"), "finite"),
                (("generation", "thinking_mode"), "false", "must be a boolean"),
            )
            for keys, value, expected in cases:
                with self.subTest(field=".".join(keys)):
                    payload = copy.deepcopy(original)
                    payload[keys[0]][keys[1]] = value
                    config.source_path.write_text(
                        json.dumps(payload), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(StartupValidationError, expected):
                        ServiceConfig.load(config.source_path, environ={})


if __name__ == "__main__":
    unittest.main()
