#!/usr/bin/env python3
"""Compare BM25, FAISS dense retrieval, and full VibePull retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pipeline import bm25_retrieval, retrieval
from pipeline.crag import retrieve_with_correction


QUERIES_PATH = ROOT / "eval" / "queries.json"
RESULTS_PATH = ROOT / "eval" / "baseline_results.json"


def load_queries() -> list[dict[str, Any]]:
    payload = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Expected a list in {QUERIES_PATH}")
    return [item for item in payload if isinstance(item, dict)]


def normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def first_relevant_rank(results: list[dict[str, Any]], expected_ids: set[str]) -> int | None:
    for rank, result in enumerate(results[:5], start=1):
        result_ids = {
            normalize_id(result.get("id")),
            normalize_id(result.get("clean_id")),
        }
        result_ids.discard("")
        if any(
            expected_id == result_id
            or expected_id in result_id
            or result_id in expected_id
            for expected_id in expected_ids
            for result_id in result_ids
        ):
            return rank
    return None


def metrics_for(searcher: Callable[[str], list[dict[str, Any]]], queries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    hit1 = 0.0
    hit5 = 0.0
    mrr = 0.0

    for item in queries:
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        expected_ids = {normalize_id(value) for value in item.get("expected_ids", [])}
        expected_ids.discard("")
        results = searcher(query)[:5]
        relevant_rank = first_relevant_rank(results, expected_ids)
        hit1 += 1.0 if relevant_rank == 1 else 0.0
        hit5 += 1.0 if relevant_rank is not None else 0.0
        mrr += 0.0 if relevant_rank is None else 1.0 / relevant_rank
        rows.append(
            {
                "query": query,
                "expected_ids": sorted(expected_ids),
                "top_ids": [result.get("id") for result in results],
                "first_relevant_rank": relevant_rank,
            }
        )

    total = len(rows) or 1
    return {
        "hit_at_1": hit1 / total,
        "hit_at_5": hit5 / total,
        "mrr": mrr / total,
        "queries": rows,
    }


def bm25_only(query: str) -> list[dict[str, Any]]:
    return bm25_retrieval.search(query, top_k=5)


def faiss_only(query: str) -> list[dict[str, Any]]:
    return retrieval.search(query, top_k=5)


def vibepull_full(query: str) -> list[dict[str, Any]]:
    return retrieve_with_correction(query, top_k=5)


def evaluate() -> dict[str, Any]:
    queries = load_queries()
    systems: list[tuple[str, Callable[[str], list[dict[str, Any]]]]] = [
        ("BM25 only", bm25_only),
        ("FAISS only", faiss_only),
        ("VibePull full", vibepull_full),
    ]
    payload = {"systems": {}}
    for name, searcher in systems:
        payload["systems"][name] = metrics_for(searcher, queries)
    RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def print_table(payload: dict[str, Any]) -> None:
    print("System          | Hit@1 | Hit@5 | MRR")
    print("----------------|-------|-------|------")
    for name in ("BM25 only", "FAISS only", "VibePull full"):
        metrics = payload["systems"][name]
        print(
            f"{name.ljust(15)} | {metrics['hit_at_1']:.2f}  | "
            f"{metrics['hit_at_5']:.2f}  | {metrics['mrr']:.2f}"
        )


def main() -> None:
    print_table(evaluate())


if __name__ == "__main__":
    main()
