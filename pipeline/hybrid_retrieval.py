#!/usr/bin/env python3
"""Hybrid retrieval with Reciprocal Rank Fusion over FAISS and BM25."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline import bm25_retrieval, retrieval


RRF_K = 60
DEFAULT_TOP_K = 20


def id_for(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("name") or "")


def merge_candidate(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if key not in merged or merged[key] in (None, "", []):
            merged[key] = value
    return merged


def fuse_rankings(rankings: list[list[dict[str, Any]]], top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    candidates: dict[str, dict[str, Any]] = {}

    for ranked_list in rankings:
        for item in ranked_list:
            component_id = id_for(item)
            if not component_id:
                continue
            rank = int(item.get("rank") or len(scores) + 1)
            # RRF score(d) = sum(1 / (60 + rank_r(d))) for each ranker r
            scores[component_id] = scores.get(component_id, 0.0) + (1.0 / (RRF_K + rank))
            candidates[component_id] = (
                merge_candidate(candidates[component_id], item)
                if component_id in candidates
                else dict(item)
            )

    fused = []
    for component_id, score in scores.items():
        item = dict(candidates[component_id])
        item["score"] = score
        item["rrf_score"] = score
        fused.append(item)
    fused.sort(key=lambda item: item["rrf_score"], reverse=True)

    for rank, item in enumerate(fused[:top_k], start=1):
        item["rank"] = rank
    return fused[:top_k]


def search(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
    dense_results = retrieval.search(query, top_k=DEFAULT_TOP_K)
    bm25_results = bm25_retrieval.search(query, top_k=DEFAULT_TOP_K)
    return fuse_rankings([dense_results, bm25_results], top_k=top_k)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="animated loading spinner")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for item in search(args.query, top_k=args.top_k):
        print(
            f"{item['rank']:>2} | {item.get('name') or item.get('id')} | "
            f"{item.get('server')} | {item.get('rrf_score', 0.0):.4f}"
        )


if __name__ == "__main__":
    main()
