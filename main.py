#!/usr/bin/env python3
"""VibePull command-line demo: FAISS retrieval plus cross-encoder rerank."""

from __future__ import annotations

import argparse
from typing import Any

from pipeline.crag import retrieve_with_correction
from pipeline.hybrid_retrieval import search as hybrid_search
from pipeline.rerank import rerank


FINAL_TOP_K = 5


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def print_table(results: list[dict[str, Any]]) -> None:
    separator = "=" * 80
    inner_separator = "-" * 80
    for item in results:
        print(separator)
        print(
            f"Rank {item.get('rank', '')} | "
            f"{as_text(item.get('display_name') or item.get('name') or item.get('id'))} | "
            f"{as_text(item.get('server'))} | "
            f"{as_text(item.get('component_type'))} | "
            f"Score: {float(item.get('score', 0.0)):.2f}"
        )
        print(as_text(item.get("visual_summary")))
        print(inner_separator)
        print("GENERATION PROMPT:")
        print(as_text(item.get("generation_prompt")) or "No generation prompt available.")
    if results:
        print(separator)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help='Example: python main.py "animated loading spinner"')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _ = hybrid_search, rerank
    print_table(retrieve_with_correction(args.query, top_k=FINAL_TOP_K))


if __name__ == "__main__":
    main()
