#!/usr/bin/env python3
"""Cross-encoder reranking for hybrid retrieval candidates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import CrossEncoder


if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline import hybrid_retrieval


RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
INITIAL_TOP_K = 20
FINAL_TOP_K = 5
_CROSS_ENCODER: CrossEncoder | None = None


def load_cross_encoder() -> CrossEncoder:
    global _CROSS_ENCODER
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    try:
        _CROSS_ENCODER = CrossEncoder(RERANK_MODEL_NAME, local_files_only=True)
    except Exception:
        _CROSS_ENCODER = CrossEncoder(RERANK_MODEL_NAME)
    return _CROSS_ENCODER


def document_text(candidate: dict[str, Any]) -> str:
    value = candidate.get("document_text") or candidate.get("document") or ""
    if value:
        return str(value)
    fields = [
        candidate.get("name"),
        candidate.get("component_type"),
        candidate.get("visual_summary"),
        candidate.get("tags"),
        candidate.get("style_tags"),
        candidate.get("interaction_tags"),
    ]
    parts: list[str] = []
    for field in fields:
        if isinstance(field, list):
            parts.extend(str(item) for item in field)
        elif field is not None:
            parts.append(str(field))
    return " ".join(part.strip() for part in parts if part.strip())


def normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    array = np.asarray(scores, dtype="float32")
    min_score = float(array.min())
    max_score = float(array.max())
    if max_score - min_score < 1e-9:
        return [1.0 for _ in scores]
    return [float((score - min_score) / (max_score - min_score)) for score in array]


def rerank(query: str, candidates: list[dict[str, Any]], top_k: int = FINAL_TOP_K) -> list[dict[str, Any]]:
    if not candidates:
        return []

    model = load_cross_encoder()
    limited = candidates[:INITIAL_TOP_K]
    pairs = [(query, document_text(candidate)) for candidate in limited]
    raw_scores = [float(score) for score in model.predict(pairs)]
    normalized = normalize_scores(raw_scores)

    reranked: list[dict[str, Any]] = []
    for candidate, raw_score, normalized_score in zip(limited, raw_scores, normalized, strict=True):
        item = dict(candidate)
        item["cross_encoder_score"] = raw_score
        item["score"] = normalized_score
        item["normalized_score"] = normalized_score
        reranked.append(item)

    reranked.sort(key=lambda item: item["cross_encoder_score"], reverse=True)
    for rank, item in enumerate(reranked[:top_k], start=1):
        item["rank"] = rank
    return reranked[:top_k]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural language query to retrieve and rerank.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = hybrid_retrieval.search(args.query, top_k=INITIAL_TOP_K)
    results = rerank(args.query, candidates, top_k=FINAL_TOP_K)
    for item in results:
        print(
            f"{item['rank']:>2} | {item.get('name') or item.get('id')} | "
            f"{item.get('server')} | {item.get('component_type')} | {item.get('score', 0):.4f}"
        )


if __name__ == "__main__":
    main()
