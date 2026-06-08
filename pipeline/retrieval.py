#!/usr/bin/env python3
"""Build and search the FAISS dense index for enriched UI components."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from tqdm import tqdm


ENRICHED_ROOT = Path("data/enriched")
INDEX_ROOT = Path("indexes")
INDEX_PATH = INDEX_ROOT / "text_index.faiss"
METADATA_PATH = INDEX_ROOT / "text_metadata.json"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
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
_EMBED_MODEL: SentenceTransformer | None = None
_SAVED_INDEX: tuple[faiss.Index, list[dict[str, Any]]] | None = None


def load_embed_model() -> SentenceTransformer:
    global _EMBED_MODEL
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        _EMBED_MODEL = SentenceTransformer(EMBED_MODEL_NAME, local_files_only=True)
    except Exception:
        _EMBED_MODEL = SentenceTransformer(EMBED_MODEL_NAME)
    return _EMBED_MODEL


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Skipping unreadable JSON {path}: {error}")
        return None
    if not isinstance(payload, dict):
        print(f"Skipping non-object JSON {path}")
        return None
    return payload


def as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def build_document_text(component: dict[str, Any]) -> str:
    parts = [
        as_text(component.get("name")),
        as_text(component.get("description")),
        as_text(component.get("visual_summary")),
        as_text(component.get("tags")),
        as_text(component.get("style_tags")),
        as_text(component.get("interaction_tags")),
        as_text(component.get("component_type")),
    ]
    return " ".join(part for part in parts if part)


def load_components() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for source in SOURCE_FOLDERS:
        folder = ENRICHED_ROOT / source
        if not folder.exists():
            print(f"Folder not found, skipping: {folder}")
            continue
        for path in sorted(folder.rglob("*.json")):
            component = load_json(path)
            if component is None:
                continue
            if component.get("enrichment_failed") is True:
                continue
            document_text = build_document_text(component)
            if not document_text:
                continue
            component["_path"] = str(path)
            component["_server"] = source
            component["_document_text"] = document_text
            components.append(component)
    return components


def normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    embeddings = embeddings.astype("float32", copy=False)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-12, None)
    return embeddings / norms


def embed_texts(model: SentenceTransformer, texts: list[str]) -> np.ndarray:
    vectors = model.encode(
        texts,
        convert_to_numpy=True,
        show_progress_bar=True,
        batch_size=32,
    )
    return normalize_embeddings(np.asarray(vectors, dtype="float32"))


def metadata_for(component: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": component.get("id"),
        "clean_id": component.get("clean_id"),
        "display_name": component.get("display_name"),
        "name": component.get("name"),
        "server": component.get("server") or component.get("_server"),
        "component_type": component.get("component_type"),
        "visual_summary": component.get("visual_summary"),
        "tags": component.get("tags", []),
        "style_tags": component.get("style_tags", []),
        "interaction_tags": component.get("interaction_tags", []),
        "generation_prompt": component.get("generation_prompt"),
        "has_source_code": bool(str(component.get("source_code") or "").strip()),
        "document_text": component.get("_document_text", ""),
    }


def build_index() -> None:
    components = load_components()
    if not components:
        raise SystemExit("No enriched components found to index.")

    documents = [str(component["_document_text"]) for component in components]
    model = load_embed_model()
    embeddings = embed_texts(model, documents)

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    INDEX_ROOT.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(INDEX_PATH))
    metadata = [metadata_for(component) for component in components]
    METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Indexed {len(metadata)} components")


def load_saved_index() -> tuple[faiss.Index, list[dict[str, Any]]]:
    global _SAVED_INDEX
    if _SAVED_INDEX is not None:
        return _SAVED_INDEX
    if not INDEX_PATH.exists() or not METADATA_PATH.exists():
        raise SystemExit("Index files missing. Run `python pipeline/retrieval.py` first.")
    index = faiss.read_index(str(INDEX_PATH))
    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(metadata, list):
        raise SystemExit(f"Invalid metadata file: {METADATA_PATH}")
    _SAVED_INDEX = (index, [item for item in metadata if isinstance(item, dict)])
    return _SAVED_INDEX


def search(query: str, top_k: int = 20) -> list[dict[str, Any]]:
    index, metadata = load_saved_index()
    if not metadata:
        return []
    model = load_embed_model()
    query_vector = model.encode(query, convert_to_numpy=True).astype("float32", copy=False)
    query_vector = normalize_embeddings(query_vector.reshape(1, -1))
    scores, indices = index.search(query_vector, min(top_k, len(metadata)))

    results: list[dict[str, Any]] = []
    for rank, (score, idx) in enumerate(zip(scores[0], indices[0], strict=True), start=1):
        idx = int(idx)
        if idx < 0 or idx >= len(metadata):
            continue
        item = dict(metadata[idx])
        item["score"] = float(score)
        item["faiss_score"] = float(score)
        item["rank"] = rank
        item["document_text"] = item.get("document_text") or build_document_text(item)
        results.append(item)
    return results


def print_results(results: list[dict[str, Any]]) -> None:
    for item in results:
        summary = as_text(item.get("visual_summary"))[:100]
        print(
            f"{item.get('rank', ''):>2} | {item.get('name') or item.get('id')} | "
            f"{item.get('server')} | {item.get('component_type')} | "
            f"{item.get('score', 0.0):.4f} | {summary}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-index", action="store_true", help="Build the FAISS index.")
    parser.add_argument("--query", help="Search query for the saved FAISS index.")
    parser.add_argument("--top-k", type=int, default=20, help="Number of results to return.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.query:
        print_results(search(args.query, top_k=args.top_k))
        return
    build_index()


if __name__ == "__main__":
    main()
