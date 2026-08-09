"""Executable contract for the indexing/retrieval part (A파트).

Written before the implementation exists. Every test skips while
`scripts/rag_index.py` is missing, so the suite stays green until that part
lands and then holds it to `docs/cross-review/interface-contract.md`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_SCRIPT = ROOT / "scripts" / "rag_index.py"
CORPUS = ROOT / "corpus" / "v0.1"
DATASET = ROOT / "datasets" / "eval-v0.1.jsonl"

CHUNK_KEYS = {
    "chunk_id",
    "doc_id",
    "doc_title",
    "section",
    "section_title",
    "text",
    "owner_department",
    "classification",
    "acl_roles",
    "contains_injection",
    "corpus_version",
}

ALL_ROLES = [
    "compliance-officer",
    "internal-audit",
    "whistleblow-admin",
    "branch-staff",
    "vendor-contractor",
]


def load_module():
    spec = importlib.util.spec_from_file_location("rag_index", INDEX_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {INDEX_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases() -> list[dict]:
    with DATASET.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def corpus_acl() -> dict[str, list[str]]:
    """doc_id -> acl_roles, read straight from the corpus frontmatter."""
    mapping: dict[str, list[str]] = {}
    for path in sorted(CORPUS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, _, rest = text.partition("---\n")
        raw, _, _ = rest.partition("\n---")
        meta = json.loads(raw)
        mapping[meta["doc_id"]] = meta["acl_roles"]
    return mapping


@unittest.skipUnless(INDEX_SCRIPT.exists(), "scripts/rag_index.py not implemented yet")
class RetrievalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()
        cls.chunks = cls.module.load_corpus(CORPUS)
        cls.retriever = cls.module.Retriever(cls.chunks)
        cls.acl = corpus_acl()
        cls.cases = load_cases()

    def test_chunks_have_the_contract_keys(self) -> None:
        self.assertTrue(self.chunks, "load_corpus returned nothing")
        for chunk in self.chunks:
            self.assertEqual(set(chunk) & CHUNK_KEYS, CHUNK_KEYS, chunk.get("chunk_id"))

    def test_chunk_id_format(self) -> None:
        for chunk in self.chunks:
            self.assertRegex(chunk["chunk_id"], r"^[A-Z]{3}-\d{4}-\d{3}#제\d+조$")
            self.assertEqual(
                chunk["chunk_id"], f"{chunk['doc_id']}#{chunk['section']}"
            )

    def test_every_corpus_document_is_indexed(self) -> None:
        indexed = {chunk["doc_id"] for chunk in self.chunks}
        self.assertEqual(indexed, set(self.acl))

    def test_injection_documents_stay_in_the_index(self) -> None:
        # Removing them would make the red-team half of the eval set meaningless.
        indexed = {chunk["doc_id"] for chunk in self.chunks}
        self.assertIn("POL-2026-006", indexed)
        self.assertIn("CMP-2026-005", indexed)
        flagged = {
            chunk["doc_id"] for chunk in self.chunks if chunk["contains_injection"]
        }
        self.assertEqual(flagged, {"POL-2026-006", "CMP-2026-005"})

    def test_heading_line_is_not_part_of_the_chunk_text(self) -> None:
        for chunk in self.chunks:
            self.assertNotIn("## 제", chunk["text"])

    def test_search_never_returns_a_chunk_the_role_cannot_see(self) -> None:
        queries = ["보고 기한", "한도", "점검", "감사", "제보", "위탁", "문서 등급"]
        for role in ALL_ROLES:
            for query in queries:
                for hit in self.retriever.search(query, role, top_k=10):
                    doc_id = hit["chunk"]["doc_id"]
                    self.assertIn(
                        role,
                        self.acl[doc_id],
                        f"{role} received {doc_id} for query {query!r}",
                    )

    def test_unauthorized_eval_cases_never_surface_their_forbidden_documents(
        self,
    ) -> None:
        checked = 0
        for case in self.cases:
            if case["type"] != "unauthorized":
                continue
            checked += 1
            hits = self.retriever.search(case["question"], case["role"], top_k=5)
            returned = {hit["chunk"]["doc_id"] for hit in hits}
            leaked = returned & set(case["forbidden_doc_ids"])
            self.assertEqual(leaked, set(), f"{case['id']} leaked {leaked}")
        self.assertEqual(checked, 10, "eval set should hold 10 unauthorized cases")

    def test_hit_shape_and_ranking(self) -> None:
        hits = self.retriever.search("고액현금거래 보고", "branch-staff", top_k=3)
        self.assertLessEqual(len(hits), 3)
        self.assertTrue(hits, "a plain keyword query returned nothing")
        for index, hit in enumerate(hits, start=1):
            self.assertEqual(set(hit) & {"chunk", "score", "rank"}, {"chunk", "score", "rank"})
            self.assertEqual(hit["rank"], index)
            self.assertIsInstance(hit["score"], float)
        scores = [hit["score"] for hit in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_known_item_retrieval(self) -> None:
        expected = [
            ("고액현금거래 금융정보분석원 보고", "branch-staff", "POL-2026-001#제3조"),
            ("여신위원회 승인 한도", "branch-staff", "POL-2026-002#제2조"),
            ("표본추출 모집단 건수", "compliance-officer", "CMP-2026-004#제3조"),
        ]
        for query, role, chunk_id in expected:
            hits = self.retriever.search(query, role, top_k=3)
            found = [hit["chunk"]["chunk_id"] for hit in hits]
            self.assertIn(chunk_id, found, f"{query!r} -> {found}")

    def test_search_is_deterministic(self) -> None:
        first = self.retriever.search("점검주기", "compliance-officer", top_k=5)
        second = self.retriever.search("점검주기", "compliance-officer", top_k=5)
        self.assertEqual(
            [hit["chunk"]["chunk_id"] for hit in first],
            [hit["chunk"]["chunk_id"] for hit in second],
        )

    def test_ties_break_on_chunk_id(self) -> None:
        hits = self.retriever.search("보존", "compliance-officer", top_k=10)
        groups: dict[float, list[str]] = {}
        for hit in hits:
            groups.setdefault(hit["score"], []).append(hit["chunk"]["chunk_id"])
        for score, ids in groups.items():
            self.assertEqual(ids, sorted(ids), f"tie at {score} is not sorted")

    def test_degenerate_inputs_do_not_raise(self) -> None:
        self.assertEqual(self.retriever.search("", "branch-staff", top_k=5), [])
        self.assertEqual(
            self.retriever.search("보고", "no-such-role", top_k=5), []
        )
        self.assertEqual(self.retriever.search("보고", "branch-staff", top_k=0), [])

    def test_vendor_contractor_only_ever_sees_public_documents(self) -> None:
        for query in ["보고", "한도", "규정", "감사", "점검", "민원"]:
            for hit in self.retriever.search(query, "vendor-contractor", top_k=10):
                self.assertTrue(
                    hit["chunk"]["doc_id"].startswith("PUB-"),
                    hit["chunk"]["doc_id"],
                )

    def test_config_hash_is_stable_and_content_sensitive(self) -> None:
        digest = self.retriever.config_hash()
        self.assertRegex(digest, r"^[0-9a-f]{12}$")
        self.assertEqual(digest, self.module.Retriever(self.chunks).config_hash())

        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "v0.1"
            shutil.copytree(CORPUS, copy)
            target = copy / "POL-2026-001.md"
            target.write_text(
                target.read_text(encoding="utf-8").replace("30일", "31일"),
                encoding="utf-8",
            )
            changed = self.module.Retriever(
                self.module.load_corpus(copy)
            ).config_hash()
        self.assertNotEqual(digest, changed)

    def test_config_hash_reacts_to_permission_changes(self) -> None:
        """Added by review 2026-08-08 (work/review-claude-rag_index.json).

        The hash is recorded as `rag.retriever_config_hash` in every result, so
        two runs whose permission models differ must not be indistinguishable.
        """
        widened = json.loads(json.dumps(self.chunks))
        for chunk in widened:
            if chunk["doc_id"] == "AUD-2026-001":
                chunk["acl_roles"] = sorted(set(chunk["acl_roles"]) | {"branch-staff"})
        self.assertNotEqual(
            self.retriever.config_hash(),
            self.module.Retriever(widened).config_hash(),
        )

    def test_index_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            self.module.save_index(self.chunks, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("chunks", payload)
            self.assertIn("corpus_version", payload)
            restored = self.module.Retriever.from_index_file(path)
        self.assertEqual(restored.config_hash(), self.retriever.config_hash())


class DependencyPolicyTests(unittest.TestCase):
    """The deployment target is an air-gapped node, so the dependency set is fixed."""

    def test_no_new_runtime_dependencies(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        block = re.search(r"dependencies = \[(.*?)\]", text, re.S)
        assert block is not None
        names = re.findall(r'"([A-Za-z0-9_.-]+)', block.group(1))
        self.assertEqual({name.lower() for name in names}, {"httpx", "jsonschema"})


if __name__ == "__main__":
    unittest.main()
