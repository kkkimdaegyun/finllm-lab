"""Regression tests for the title-aware retrieval policy."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_SCRIPT = ROOT / "scripts" / "rag_index.py"
CORPUS = ROOT / "corpus" / "v0.1"
DATASET = ROOT / "datasets" / "eval-v0.1.jsonl"


def load_module():
    spec = importlib.util.spec_from_file_location("rag_index_ranking", INDEX_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {INDEX_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RankingPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()
        cls.chunks = cls.module.load_corpus(CORPUS)
        cls.retriever = cls.module.Retriever(cls.chunks)

    def test_section_title_is_used_for_ranking(self) -> None:
        chunks = [
            {
                "chunk_id": "POL-2026-901#제1조",
                "section_title": "일반 조항",
                "text": "두 조문에 공통으로 들어 있는 본문이다.",
                "acl_roles": ["branch-staff"],
            },
            {
                "chunk_id": "POL-2026-902#제1조",
                "section_title": "희귀제목표현",
                "text": "두 조문에 공통으로 들어 있는 본문이다.",
                "acl_roles": ["branch-staff"],
            },
        ]
        hits = self.module.Retriever(chunks).search(
            "희귀제목표현", "branch-staff", top_k=1
        )
        self.assertEqual(hits[0]["chunk"]["chunk_id"], "POL-2026-902#제1조")
        self.assertGreater(hits[0]["score"], 0.0)

    def test_expected_citation_recall_at_8_is_complete(self) -> None:
        cases = []
        with DATASET.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                case = json.loads(line)
                if case["expected_citations"]:
                    cases.append(case)
        self.assertEqual(len(cases), 40)

        failures: dict[str, list[str]] = {}
        for case in cases:
            returned = {
                hit["chunk"]["chunk_id"]
                for hit in self.retriever.search(
                    case["question"], case["role"], top_k=8
                )
            }
            missing = sorted(set(case["expected_citations"]) - returned)
            if missing:
                failures[case["id"]] = missing
        self.assertEqual(failures, {})

    def test_chunk_text_excludes_heading_and_section_title(self) -> None:
        title = "제목에만있는식별자"
        body = "본문에만 있는 내용이다."
        metadata = {
            "doc_id": "POL-2026-999",
            "title": "청킹 회귀 테스트",
            "owner_department": "테스트부",
            "classification": "internal",
            "acl_roles": ["branch-staff"],
        }
        document = (
            "---\n"
            + json.dumps(metadata, ensure_ascii=False)
            + f"\n---\n\n## 제1조 ({title})\n\n{body}\n"
        )

        with tempfile.TemporaryDirectory() as directory:
            corpus = Path(directory) / "v-test"
            corpus.mkdir()
            (corpus / "POL-2026-999.md").write_text(document, encoding="utf-8")
            chunks = self.module.load_corpus(corpus)
            self.module.Retriever(chunks)

        self.assertEqual(chunks[0]["section_title"], title)
        self.assertEqual(chunks[0]["text"], body)
        self.assertNotIn(title, chunks[0]["text"])
        self.assertNotIn("## 제1조", chunks[0]["text"])


if __name__ == "__main__":
    unittest.main()
