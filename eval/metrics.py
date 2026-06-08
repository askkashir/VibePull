#!/usr/bin/env python3
"""Evaluate VibePull hybrid retrieval plus cross-encoder reranking."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from pipeline import hybrid_retrieval, rerank


QUERIES_PATH = ROOT / "eval" / "queries.json"
RESULTS_PATH = ROOT / "eval" / "results.json"


def load_queries() -> list[dict[str, Any]]:
    payload = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit(f"Expected a list in {QUERIES_PATH}")
    return [item for item in payload if isinstance(item, dict)]


def normalize_id(value: Any) -> str:
    return str(value or "").strip().lower()


def id_matches(expected_id: str, result: dict[str, Any]) -> bool:
    result_ids = {
        normalize_id(result.get("id")),
        normalize_id(result.get("clean_id")),
    }
    result_ids.discard("")
    for result_id in result_ids:
        if expected_id == result_id:
            return True
        if expected_id in result_id or result_id in expected_id:
            return True
    return False


def is_relevant(result: dict[str, Any], expected_ids: set[str]) -> bool:
    return any(id_matches(expected_id, result) for expected_id in expected_ids)


def first_relevant_rank(results: list[dict[str, Any]], expected_ids: set[str]) -> int | None:
    for rank, result in enumerate(results[:5], start=1):
        if is_relevant(result, expected_ids):
            return rank
    return None


def ndcg_at_5(results: list[dict[str, Any]], expected_ids: set[str]) -> float:
    dcg = 0.0
    for rank, result in enumerate(results[:5], start=1):
        relevance = 1.0 if is_relevant(result, expected_ids) else 0.0
        if relevance:
            dcg += relevance / math.log2(rank + 1)
    ideal_relevant = min(len(expected_ids), 5)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_relevant + 1))
    return 0.0 if idcg == 0 else dcg / idcg


def evaluate_query(query_item: dict[str, Any]) -> dict[str, Any]:
    query = str(query_item.get("query") or "").strip()
    expected_ids = {normalize_id(value) for value in query_item.get("expected_ids", [])}
    expected_ids.discard("")

    candidates = hybrid_retrieval.search(query, top_k=rerank.INITIAL_TOP_K)
    results = rerank.rerank(query, candidates, top_k=rerank.FINAL_TOP_K)
    relevant_rank = first_relevant_rank(results, expected_ids)
    ndcg = ndcg_at_5(results, expected_ids)

    return {
        "query": query,
        "expected_ids": sorted(expected_ids),
        "results": [
            {
                "rank": result.get("rank"),
                "id": result.get("id"),
                "clean_id": result.get("clean_id"),
                "display_name": result.get("display_name"),
                "name": result.get("name"),
                "server": result.get("server"),
                "component_type": result.get("component_type"),
                "score": result.get("score"),
                "cross_encoder_score": result.get("cross_encoder_score"),
                "rrf_score": result.get("rrf_score"),
            }
            for result in results
        ],
        "hit_at_1": 1.0 if relevant_rank == 1 else 0.0,
        "hit_at_5": 1.0 if relevant_rank is not None else 0.0,
        "mrr": 0.0 if relevant_rank is None else 1.0 / relevant_rank,
        "ndcg_at_5": ndcg,
        "first_relevant_rank": relevant_rank,
    }


def average(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(float(row[key]) for row in rows) / len(rows)


def evaluate() -> dict[str, Any]:
    queries = load_queries()
    rows = [evaluate_query(item) for item in queries if str(item.get("query") or "").strip()]
    metrics = {
        "hit_at_1": average(rows, "hit_at_1"),
        "hit_at_5": average(rows, "hit_at_5"),
        "mrr": average(rows, "mrr"),
        "ndcg_at_5": average(rows, "ndcg_at_5"),
        "total_queries": len(rows),
    }
    payload = {"metrics": metrics, "queries": rows}
    RESULTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def print_summary(metrics: dict[str, Any]) -> None:
    print("Metric    | Score")
    print("----------|------")
    print(f"Hit@1     | {metrics['hit_at_1']:.2f}")
    print(f"Hit@5     | {metrics['hit_at_5']:.2f}")
    print(f"MRR       | {metrics['mrr']:.2f}")
    print(f"NDCG@5    | {metrics['ndcg_at_5']:.2f}")
    print(f"Total queries evaluated: {metrics['total_queries']}")


def main() -> None:
    payload = evaluate()
    print_summary(payload["metrics"])


if __name__ == "__main__":
    main()
