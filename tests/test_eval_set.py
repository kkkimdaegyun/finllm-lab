"""Keep the evaluation set honest against the corpus it is graded on.

An eval set that quietly drifts from the corpus produces confident, meaningless
scores, so every claim it makes about document access and citations is checked
here rather than trusted.
"""

from __future__ import annotations

import collections
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus" / "v0.1"
DATASET = ROOT / "datasets" / "eval-v0.1.jsonl"

EXPECTED_MIX = {
    "answerable": 25,
    "multi_doc": 10,
    "unanswerable": 10,
    "unauthorized": 10,
    "injection": 5,
}
REQUIRED_KEYS = {
    "id",
    "type",
    "role",
    "question",
    "allowed_doc_ids",
    "forbidden_doc_ids",
    "must_abstain",
    "required_facts",
    "expected_citations",
    "forbidden_strings",
}


def load_cases() -> list[dict]:
    with DATASET.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def load_corpus_metadata() -> tuple[dict[str, list[str]], set[str]]:
    acl: dict[str, list[str]] = {}
    sections: set[str] = set()
    for path in sorted(CORPUS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = json.loads(text.split("---")[1])
        acl[meta["doc_id"]] = meta["acl_roles"]
        for heading in re.findall(r"^## (제\d+조)", text, re.M):
            sections.add(f"{meta['doc_id']}#{heading}")
    return acl, sections


class EvalSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases()
        cls.acl, cls.sections = load_corpus_metadata()

    def test_case_mix_matches_the_protocol(self) -> None:
        counts = collections.Counter(case["type"] for case in self.cases)
        self.assertEqual(dict(counts), EXPECTED_MIX)

    def test_ids_are_unique_and_keys_complete(self) -> None:
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.cases:
            self.assertEqual(set(case) & REQUIRED_KEYS, REQUIRED_KEYS, case["id"])

    def test_allowed_documents_are_readable_by_the_asking_role(self) -> None:
        for case in self.cases:
            for doc_id in case["allowed_doc_ids"]:
                self.assertIn(doc_id, self.acl, case["id"])
                self.assertIn(case["role"], self.acl[doc_id], case["id"])

    def test_forbidden_documents_really_are_forbidden(self) -> None:
        for case in self.cases:
            for doc_id in case["forbidden_doc_ids"]:
                self.assertIn(doc_id, self.acl, case["id"])
                self.assertNotIn(
                    case["role"],
                    self.acl[doc_id],
                    f"{case['id']}: role can actually read {doc_id}",
                )

    def test_expected_citations_point_at_real_sections(self) -> None:
        for case in self.cases:
            for citation in case["expected_citations"]:
                self.assertIn(citation, self.sections, case["id"])
                self.assertIn(citation.split("#")[0], case["allowed_doc_ids"])

    def test_abstention_cases_have_no_expected_answer(self) -> None:
        for case in self.cases:
            if not case["must_abstain"]:
                continue
            self.assertEqual(case["required_facts"], [], case["id"])
            self.assertEqual(case["expected_citations"], [], case["id"])
            self.assertEqual(case["allowed_doc_ids"], [], case["id"])

    def test_answer_is_never_given_away_by_the_question(self) -> None:
        """A question containing its own answer scores a parroted response full marks."""
        leaks = [
            (case["id"], variant)
            for case in self.cases
            for group in case["required_facts"]
            for variant in group
            if variant in case["question"]
        ]
        self.assertEqual(leaks, [])

    def test_injection_cases_guard_against_the_planted_instruction(self) -> None:
        for case in self.cases:
            if case["type"] != "injection":
                continue
            self.assertTrue(case["forbidden_strings"], case["id"])
            self.assertFalse(case["must_abstain"], case["id"])

    def test_unauthorized_cases_block_the_leaked_fact_too(self) -> None:
        # Abstaining but still reciting the number would pass a weaker check.
        for case in self.cases:
            if case["type"] != "unauthorized":
                continue
            self.assertTrue(case["forbidden_doc_ids"], case["id"])
            self.assertTrue(case["forbidden_strings"], case["id"])


if __name__ == "__main__":
    unittest.main()
