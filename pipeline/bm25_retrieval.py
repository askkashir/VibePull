#!/usr/bin/env python3
"""Build and search a BM25 index over enriched component text."""

from __future__ import annotations

import argparse
import json
import pickle
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ENRICHED_ROOT = Path("data/enriched")
INDEX_ROOT = Path("indexes")
BM25_PATH = INDEX_ROOT / "bm25_index.pkl"
SOURCE_FOLDERS = (
    "magicui",
    "shadcn",
    "aceternity",
    "originui",
    "cultui",
    "kokonutui",
    "lukachoui",
    "numberflow",
    "fancyui",
    "motionprimitives",
    "eldoraui",
    "radixuithemes",
    "tremor",
    "hyperui",
    "pinesui",
    "parkui",
    "heroui",
    "floatui",
    "mantine",
)
_SAVED_INDEX: tuple[Any, list[dict[str, Any]]] | None = None


def ensure_rank_bm25() -> Any:
    try:
        from rank_bm25 import BM25Okapi

        return BM25Okapi
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rank-bm25"])
        from rank_bm25 import BM25Okapi

        return BM25Okapi


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Skipping unreadable JSON {path}: {error}")
        return None
    return payload if isinstance(payload, dict) else None


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def component_text(component: dict[str, Any]) -> str:
    fields = [
        component.get("name"),
        component.get("description"),
        component.get("visual_summary"),
        component.get("tags"),
    ]
    return " ".join(as_text(field) for field in fields if as_text(field))


def document_text(component: dict[str, Any]) -> str:
    fields = [
        component.get("name"),
        component.get("description"),
        component.get("visual_summary"),
        component.get("tags"),
        component.get("style_tags"),
        component.get("interaction_tags"),
        component.get("component_type"),
    ]
    return " ".join(as_text(field) for field in fields if as_text(field))


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9-]*", text.lower())


def metadata_for(component: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "id": component.get("id"),
        "name": component.get("name"),
        "server": component.get("server") or source,
        "component_type": component.get("component_type"),
        "visual_summary": component.get("visual_summary"),
        "tags": component.get("tags", []),
        "style_tags": component.get("style_tags", []),
        "interaction_tags": component.get("interaction_tags", []),
        "document_text": document_text(component),
    }


def load_components() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for source in SOURCE_FOLDERS:
        folder = ENRICHED_ROOT / source
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.json")):
            component = load_json(path)
            if component is None or component.get("enrichment_failed") is True:
                continue
            text = component_text(component)
            if not text:
                continue
            item = metadata_for(component, source)
            item["_bm25_text"] = text
            components.append(item)
    return components


def build_index() -> None:
    BM25Okapi = ensure_rank_bm25()
    components = load_components()
    if not components:
        raise SystemExit("No enriched components found to index.")

    tokenized = [tokenize(str(component["_bm25_text"])) for component in components]
    bm25 = BM25Okapi(tokenized)
    payload = {"bm25": bm25, "metadata": components, "tokenized": tokenized}

    INDEX_ROOT.mkdir(parents=True, exist_ok=True)
    with BM25_PATH.open("wb") as handle:
        pickle.dump(payload, handle)
    print(f"Indexed {len(components)} components with BM25")


def load_saved_index() -> tuple[Any, list[dict[str, Any]]]:
    global _SAVED_INDEX
    if _SAVED_INDEX is not None:
        return _SAVED_INDEX
    ensure_rank_bm25()
    if not BM25_PATH.exists():
        raise SystemExit("BM25 index missing. Run `python pipeline/bm25_retrieval.py` first.")
    with BM25_PATH.open("rb") as handle:
        payload = pickle.load(handle)
    _SAVED_INDEX = (payload["bm25"], payload["metadata"])
    return _SAVED_INDEX


def search(query: str, top_k: int = 20) -> list[dict[str, Any]]:
    bm25, metadata = load_saved_index()
    scores = bm25.get_scores(tokenize(query))
    ranked = sorted(enumerate(scores), key=lambda pair: float(pair[1]), reverse=True)

    results: list[dict[str, Any]] = []
    for rank, (idx, score) in enumerate(ranked[:top_k], start=1):
        item = dict(metadata[idx])
        item.pop("_bm25_text", None)
        item["score"] = float(score)
        item["bm25_score"] = float(score)
        item["rank"] = rank
        results.append(item)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Optional query to test the saved BM25 index.")
    parser.add_argument("--top-k", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.query:
        for item in search(args.query, top_k=args.top_k):
            print(f"{item['rank']:>2} | {item.get('name') or item.get('id')} | {item['score']:.4f}")
        return
    build_index()


if __name__ == "__main__":
    main()
