#!/usr/bin/env python3
"""Corrective retrieval wrapper for VibePull search."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline import hybrid_retrieval, rerank


LOGGER = logging.getLogger("vibepull-crag")
CORRECTION_THRESHOLD = 0.15
SYNONYMS = {
    "loading": "spinner loader progress",
    "dark": "dark mode night black",
    "card": "card tile panel",
    "button": "button cta action",
    "hero": "hero landing banner section",
    "nav": "navbar navigation menu sidebar",
    "form": "form input field login signup",
    "table": "table grid data list",
    "animated": "animated motion transition effect",
    "glass": "glassmorphism frosted blur transparent",
}


def expand_query(query: str) -> str:
    words = query.split()
    expansions: list[str] = []
    lowered = query.lower()
    for key, value in SYNONYMS.items():
        if key in lowered:
            expansions.append(value)
    return " ".join(words + expansions)


def retrieve_once(query: str, top_k: int) -> list[dict]:
    candidates = hybrid_retrieval.search(query, top_k=rerank.INITIAL_TOP_K)
    return rerank.rerank(query, candidates, top_k=top_k)


def retrieve_with_correction(query: str, top_k: int = 5) -> list[dict]:
    initial_results = retrieve_once(query, top_k=top_k)
    top_raw_score = (
        float(initial_results[0].get("cross_encoder_score", 0.0))
        if initial_results
        else 0.0
    )
    correction_triggered = top_raw_score < CORRECTION_THRESHOLD
    print(
        f"[CRAG] correction triggered: {correction_triggered} "
        f"(top raw score: {top_raw_score:.2f})"
    )
    if not correction_triggered:
        LOGGER.info("CRAG correction not triggered; top raw score %.3f", top_raw_score)
        return initial_results

    LOGGER.info("CRAG correction triggered; top raw score %.3f", top_raw_score)
    expanded = expand_query(query)
    expanded_results = retrieve_once(expanded, top_k=top_k)
    expanded_score = (
        float(expanded_results[0].get("cross_encoder_score", 0.0))
        if expanded_results
        else 0.0
    )

    if expanded_score > top_raw_score:
        LOGGER.info("Using expanded query results: %s", expanded)
        return expanded_results

    LOGGER.info("Keeping original query results")
    return initial_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Natural language query to search.")
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    for item in retrieve_with_correction(args.query, top_k=args.top_k):
        print(
            f"{item['rank']:>2} | {item.get('name') or item.get('id')} | "
            f"{item.get('server')} | {item.get('component_type')} | {item.get('score', 0):.4f}"
        )


if __name__ == "__main__":
    main()
