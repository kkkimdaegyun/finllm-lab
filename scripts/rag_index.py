#!/usr/bin/env python3
"""Build and query the deterministic lexical index used by the RAG evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORD_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
HANGUL_PATTERN = re.compile(r"[가-힣]{2,}")
HEADING_PATTERN = re.compile(
    r"^##[ \t]+(제\d+조)(?:[ \t]*\(([^)\r\n]*)\))?[ \t]*$", re.MULTILINE
)
FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)

TOKENIZER_VERSION = "ko-word-bigram-v1"
RANKING_INPUT = "section_title+text"
BM25_K1 = 1.2
BM25_B = 0.75
DEFAULT_TOP_K = 5


def _tokenize(text: str) -> list[str]:
    """Return normalized word tokens plus overlapping bigrams for Korean words."""
    tokens: list[str] = []
    for match in WORD_PATTERN.finditer(text):
        token = match.group(0).lower()
        tokens.append(token)
        if HANGUL_PATTERN.fullmatch(token):
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


def _read_document(path: Path) -> tuple[dict[str, Any], str]:
    raw_document = path.read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(raw_document)
    if match is None:
        raise ValueError(f"{path}: expected JSON frontmatter between --- lines")

    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON frontmatter: {error}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: frontmatter must be a JSON object")
    return metadata, raw_document[match.end() :]


def load_corpus(corpus_dir: Path) -> list[dict[str, Any]]:
    """Read Markdown documents in *corpus_dir* and return article-level chunks."""
    corpus_dir = Path(corpus_dir)
    chunks: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()

    for path in sorted(corpus_dir.glob("*.md")):
        metadata, body = _read_document(path)
        required = (
            "doc_id",
            "title",
            "owner_department",
            "classification",
            "acl_roles",
        )
        missing = [name for name in required if name not in metadata]
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"{path}: missing frontmatter fields: {fields}")

        headings = list(HEADING_PATTERN.finditer(body))
        if not headings:
            raise ValueError(f"{path}: no article headings found")

        for index, heading in enumerate(headings):
            section = heading.group(1)
            section_title = (heading.group(2) or "").strip()
            text_start = heading.end()
            text_end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(body)
            )
            chunk_text = body[text_start:text_end].strip()
            chunk_id = f"{metadata['doc_id']}#{section}"
            if chunk_id in seen_chunk_ids:
                raise ValueError(f"{path}: duplicate chunk_id {chunk_id}")
            seen_chunk_ids.add(chunk_id)

            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": metadata["doc_id"],
                    "doc_title": metadata["title"],
                    "section": section,
                    "section_title": section_title,
                    "text": chunk_text,
                    "owner_department": metadata["owner_department"],
                    "classification": metadata["classification"],
                    "acl_roles": list(metadata["acl_roles"]),
                    "contains_injection": metadata.get("contains_injection", False),
                    "corpus_version": corpus_dir.name,
                }
            )

    return chunks


class Retriever:
    """Deterministic BM25 retriever with document ACL filtering."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.k1 = BM25_K1
        self.b = BM25_B
        self.default_top_k = DEFAULT_TOP_K
        self.tokenizer_version = TOKENIZER_VERSION
        self.ranking_input = RANKING_INPUT

        self._document_tokens = [
            _tokenize(
                "\n".join(
                    (
                        str(chunk.get("section_title", "")),
                        str(chunk["text"]),
                    )
                )
            )
            for chunk in chunks
        ]
        self._term_frequencies = [Counter(tokens) for tokens in self._document_tokens]

    @classmethod
    def from_index_file(cls, path: Path) -> "Retriever":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            raise ValueError(f"{path}: index must contain a chunks list")
        return cls(chunks)

    def search(
        self, query: str, role: str, top_k: int = DEFAULT_TOP_K
    ) -> list[dict[str, Any]]:
        """Search only chunks visible to *role* and return ranked hit mappings."""
        if top_k <= 0:
            return []
        query_terms = Counter(_tokenize(query))
        if not query_terms:
            return []

        # The candidate collection is selected first so N, df, and average length
        # cannot be influenced by chunks that the caller is not allowed to see.
        candidates = [
            index
            for index, chunk in enumerate(self.chunks)
            if role in chunk.get("acl_roles", [])
        ]
        if not candidates:
            return []

        document_count = len(candidates)
        average_length = sum(len(self._document_tokens[index]) for index in candidates)
        average_length /= document_count

        document_frequencies = {
            term: sum(
                1 for index in candidates if term in self._term_frequencies[index]
            )
            for term in query_terms
        }

        scored: list[tuple[float, str, int]] = []
        for index in candidates:
            frequencies = self._term_frequencies[index]
            document_length = len(self._document_tokens[index])
            length_ratio = document_length / average_length if average_length else 0.0
            normalization = 1.0 - self.b + self.b * length_ratio
            score = 0.0

            for term, query_frequency in query_terms.items():
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                document_frequency = document_frequencies[term]
                inverse_document_frequency = math.log(
                    1.0
                    + (document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                term_score = inverse_document_frequency * (
                    frequency * (self.k1 + 1.0)
                    / (frequency + self.k1 * normalization)
                )
                score += query_frequency * term_score

            scored.append((float(score), str(self.chunks[index]["chunk_id"]), index))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"chunk": self.chunks[index], "score": score, "rank": rank}
            for rank, (score, _chunk_id, index) in enumerate(scored[:top_k], start=1)
        ]

    def config_hash(self) -> str:
        """Return a stable 12-character digest of settings and indexed content."""
        hasher = hashlib.sha256()
        settings = {
            "b": self.b,
            "default_top_k": self.default_top_k,
            "k1": self.k1,
            "ranking_input": self.ranking_input,
            "tokenizer_version": self.tokenizer_version,
        }
        serialized_settings = json.dumps(
            settings,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        hasher.update(serialized_settings.encode("utf-8"))
        hasher.update(b"\0")
        for chunk in sorted(self.chunks, key=lambda item: str(item["chunk_id"])):
            hasher.update(str(chunk["chunk_id"]).encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(str(chunk.get("section_title", "")).encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(str(chunk["text"]).encode("utf-8"))
            hasher.update(b"\0")
            acl_roles = sorted(str(role) for role in chunk.get("acl_roles", []))
            hasher.update(",".join(acl_roles).encode("utf-8"))
            hasher.update(b"\0")
        return hasher.hexdigest()[:12]


def save_index(chunks: list[dict[str, Any]], path: Path) -> None:
    """Write chunks and index metadata as UTF-8 JSON."""
    versions = {str(chunk["corpus_version"]) for chunk in chunks}
    if len(versions) > 1:
        raise ValueError("cannot save chunks from multiple corpus versions")
    corpus_version = next(iter(versions), "")
    payload = {
        "corpus_version": corpus_version,
        "built_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "chunks": chunks,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _print_hits(hits: list[dict[str, Any]]) -> None:
    if not hits:
        print("검색 결과가 없습니다.")
        return

    rows = [("순위", "점수", "chunk_id", "문서 / 조항")]
    for hit in hits:
        chunk = hit["chunk"]
        section = str(chunk["section"])
        if chunk.get("section_title"):
            section += f" ({chunk['section_title']})"
        rows.append(
            (
                str(hit["rank"]),
                f"{hit['score']:.6f}",
                str(chunk["chunk_id"]),
                f"{chunk['doc_title']} / {section}",
            )
        )

    widths = [max(len(row[column]) for row in rows) for column in range(4)]
    for row_index, row in enumerate(rows):
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
        if row_index == 0:
            print("-+-".join("-" * width for width in widths))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="build a JSON index")
    build_parser.add_argument("--corpus", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)

    search_parser = subparsers.add_parser("search", help="search a JSON index")
    search_parser.add_argument("--index", type=Path, required=True)
    search_parser.add_argument("--role", required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)

    hash_parser = subparsers.add_parser(
        "config-hash", help="print the retriever configuration hash"
    )
    hash_parser.add_argument("--index", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "build":
        chunks = load_corpus(args.corpus)
        save_index(chunks, args.output)
        print(f"{len(chunks)}개 chunk를 {args.output}에 저장했습니다.")
    elif args.command == "search":
        retriever = Retriever.from_index_file(args.index)
        _print_hits(retriever.search(args.query, args.role, args.top_k))
    elif args.command == "config-hash":
        retriever = Retriever.from_index_file(args.index)
        print(retriever.config_hash())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
