"""End-to-end run of scripts/rag_eval.py against a stub OpenAI-compatible server.

The unit tests score hand-written answer strings. This one exercises the parts
they cannot reach: retrieval wiring, the chat payload, the HTTP path, the output
file, and the frozen-retrieval round trip.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_SCRIPT = ROOT / "scripts" / "rag_eval.py"
INDEX_SCRIPT = ROOT / "scripts" / "rag_index.py"
CITATION = re.compile(r"\[([A-Z]{3}-\d{4}-\d{3}#제\d+조)\]")


class StubHandler(BaseHTTPRequestHandler):
    """Answers by quoting the first chunk id it was given, or abstaining."""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        prompt = body["messages"][-1]["content"]
        found = CITATION.findall(prompt)
        if found:
            content = f"제공된 근거에 따르면 해당 내용은 다음과 같습니다 [{found[0]}]."
        else:
            content = "제공된 문서에서 근거를 찾을 수 없습니다."
        payload = {
            "id": "stub",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {"completion_tokens": 16},
        }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: object) -> None:
        return


@unittest.skipUnless(INDEX_SCRIPT.exists(), "scripts/rag_index.py not implemented yet")
class RagEvalIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        cls.directory = tempfile.TemporaryDirectory()
        cls.index = Path(cls.directory.name) / "index.json"
        subprocess.run(
            [
                sys.executable,
                str(INDEX_SCRIPT),
                "build",
                "--corpus",
                str(ROOT / "corpus" / "v0.1"),
                "--output",
                str(cls.index),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def run_eval(self, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        output = Path(self.directory.name) / f"eval-{len(extra)}-{extra!r:.20}.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(EVAL_SCRIPT),
                "--index",
                str(self.index),
                "--base-url",
                self.base_url,
                "--model",
                "stub-model",
                "--output",
                str(output),
                *extra,
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        return completed, output

    def test_full_run_produces_a_scored_report(self) -> None:
        retrieval = Path(self.directory.name) / "retrieval.json"
        completed, output = self.run_eval("--save-retrieval", str(retrieval))
        self.assertEqual(completed.returncode, 0, completed.stderr)

        report = json.loads(output.read_text(encoding="utf-8"))
        summary = report["summary"]
        self.assertEqual(summary["case_count"], 60)
        self.assertEqual(summary["generation_errors"], 0)
        # The stub never invents content, so nothing may leak and nothing may
        # be retrieved that the asking role cannot read.
        self.assertEqual(summary["acl_violations"], 0)
        self.assertEqual(summary["injection_successes"], 0)
        self.assertEqual(summary["overall_status"], "pass")
        self.assertEqual(set(summary["by_type"]), {
            "answerable",
            "multi_doc",
            "unanswerable",
            "unauthorized",
            "injection",
        })

        # The abstain cases are not "empty retrieval" tests. ACL filtering hides
        # the document that holds the answer, but lexical search still returns
        # other documents the role may read, so the model is handed plausible
        # but irrelevant evidence and has to decline anyway. The stub always
        # answers when handed anything, so it must score zero here — that is the
        # behaviour these cases exist to catch.
        self.assertEqual(summary["by_type"]["unauthorized"]["quality"], 0.0)
        self.assertEqual(summary["by_type"]["unanswerable"]["quality"], 0.0)
        self.assertGreater(summary["by_type"]["answerable"]["quality"], 0.0)

    def test_abstain_cases_are_handed_irrelevant_evidence_not_silence(self) -> None:
        retrieval = Path(self.directory.name) / "abstain-retrieval.json"
        completed, _ = self.run_eval("--save-retrieval", str(retrieval))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        saved = json.loads(retrieval.read_text(encoding="utf-8"))["retrieval"]

        cases = [
            json.loads(line)
            for line in (ROOT / "datasets" / "eval-v0.1.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        for case in cases:
            if case["type"] not in {"unauthorized", "unanswerable"}:
                continue
            hits = saved[case["id"]]
            self.assertTrue(
                hits, f"{case['id']}: abstention would be trivial with no evidence"
            )
            returned = {hit["chunk"]["doc_id"] for hit in hits}
            self.assertEqual(returned & set(case["forbidden_doc_ids"]), set())

        self.assertTrue(retrieval.exists())
        saved = json.loads(retrieval.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["retrieval"]), 60)
        self.assertRegex(saved["retriever_config_hash"], r"^[0-9a-f]{12}$")

    def test_human_review_sample_is_blind(self) -> None:
        completed, output = self.run_eval("--human-review-sample", "0.2")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        sample = output.with_name(f"human-review-{output.stem}.jsonl")
        rows = [
            json.loads(line)
            for line in sample.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 12)
        for row in rows:
            self.assertEqual(set(row), {
                "id",
                "question",
                "role",
                "answer",
                "human_verdict",
                "human_note",
            })
            # A reviewer who can see the gold answer or the automatic score is
            # not reviewing blind.
            self.assertNotIn("required_facts", row)
            self.assertNotIn("quality", row)

    def test_frozen_retrieval_reproduces_the_same_evidence(self) -> None:
        retrieval = Path(self.directory.name) / "frozen.json"
        first, first_output = self.run_eval("--save-retrieval", str(retrieval))
        self.assertEqual(first.returncode, 0, first.stderr)
        second, second_output = self.run_eval("--frozen-retrieval", str(retrieval))
        self.assertEqual(second.returncode, 0, second.stderr)

        left = json.loads(first_output.read_text(encoding="utf-8"))
        right = json.loads(second_output.read_text(encoding="utf-8"))
        self.assertEqual(left["answers"], right["answers"])
        self.assertEqual(left["summary"]["quality_score"], right["summary"]["quality_score"])
        self.assertEqual(
            right["metadata"]["retriever_config_hash"],
            left["metadata"]["retriever_config_hash"],
        )


if __name__ == "__main__":
    unittest.main()
