"""Tests for the peak-VRAM sampler."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def import_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gpu_watch = import_script("gpu_watch", ROOT / "scripts" / "gpu_watch.py")


class ParseTests(unittest.TestCase):
    def test_plain_value(self) -> None:
        self.assertEqual(gpu_watch.parse_used_mib("21504\n"), 21504)

    def test_float_value(self) -> None:
        self.assertEqual(gpu_watch.parse_used_mib("21504.00"), 21504)

    def test_empty_and_garbage(self) -> None:
        self.assertIsNone(gpu_watch.parse_used_mib(""))
        self.assertIsNone(gpu_watch.parse_used_mib("   \n"))
        self.assertIsNone(gpu_watch.parse_used_mib("[N/A]"))


class ReportTests(unittest.TestCase):
    def test_peak_not_last_value(self) -> None:
        report = gpu_watch.build_report(1, [1024, 23552, 2048], "t0", 0)
        self.assertEqual(report["peak_vram_mib"], 23552)
        self.assertEqual(report["peak_vram_gib"], 23.0)

    def test_no_samples_does_not_crash(self) -> None:
        report = gpu_watch.build_report(0, [], "t0", 3)
        self.assertEqual(report["peak_vram_gib"], 0)
        self.assertEqual(report["failed_query_count"], 3)


if __name__ == "__main__":
    unittest.main()
