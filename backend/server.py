#!/usr/bin/env python3
"""Flask API server for the VibePull presentation demo."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib import error, request as urlrequest

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

WEB_ROOT = ROOT / "web"
METADATA_PATH = ROOT / "indexes" / "text_metadata.json"
ENRICHED_ROOT = ROOT / "data" / "enriched"
PREVIEW_ROOT = WEB_ROOT / "previews"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
ENV_PATHS = [ROOT / ".env", ROOT / "pipeline" / ".env", ROOT / "backend" / ".env"]

from pipeline.crag import retrieve_with_correction  # noqa: E402


app = Flask(__name__, static_folder=str(WEB_ROOT), static_url_path="")
CORS(app)
ENRICHED_BY_ID: dict[str, dict[str, Any]] | None = None
METADATA_CACHE: list[dict[str, Any]] | None = None


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def parse_crag_triggered(output: str) -> bool:
    match = re.search(r"\[CRAG\]\s+correction triggered:\s+(True|False)", output)
    return bool(match and match.group(1) == "True")


def parse_top_raw_score(output: str) -> float | None:
    match = re.search(r"top raw score:\s+(-?\d+(?:\.\d+)?)", output)
    return float(match.group(1)) if match else None


def get_env_value(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    for path in ENV_PATHS:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, raw_value = stripped.split("=", 1)
                if key.strip() == name:
                    return raw_value.strip().strip('"').strip("'")
        except OSError:
            continue
    if sys.platform.startswith("win"):
        try:
            import winreg

            for root, path in (
                (winreg.HKEY_CURRENT_USER, "Environment"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            ):
                try:
                    with winreg.OpenKey(root, path) as key:
                        registry_value, _ = winreg.QueryValueEx(key, name)
                    if registry_value:
                        return str(registry_value)
                except OSError:
                    continue
        except Exception:
            return ""
    return ""


def component_key(item: dict[str, Any]) -> str:
    return f"{str(item.get('server') or '').lower()}::{str(item.get('id') or '')}"


def enriched_by_id() -> dict[str, dict[str, Any]]:
    global ENRICHED_BY_ID
    if ENRICHED_BY_ID is not None:
        return ENRICHED_BY_ID
    records: dict[str, dict[str, Any]] = {}
    for path in ENRICHED_ROOT.rglob("*.json"):
        payload = load_json(path, {})
        if isinstance(payload, dict) and payload.get("id"):
            records[component_key(payload)] = payload
            records.setdefault(str(payload["id"]), payload)
    ENRICHED_BY_ID = records
    return records


def metadata_records() -> list[dict[str, Any]]:
    global METADATA_CACHE
    if METADATA_CACHE is not None:
        return METADATA_CACHE
    METADATA_CACHE = [
        item for item in load_json(METADATA_PATH, [])
        if isinstance(item, dict) and item.get("id")
    ]
    return METADATA_CACHE


def slugify(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "component"


def preview_url_for(merged: dict[str, Any]) -> str:
    server = slugify(merged.get("server") or "unknown")
    identity = slugify(merged.get("id") or merged.get("clean_id") or merged.get("display_name") or merged.get("name"))
    preview_path = PREVIEW_ROOT / server / f"{identity}.png"
    if preview_path.exists():
        return f"/previews/{server}/{identity}.png"
    explicit = str(merged.get("preview_url") or "").strip()
    if explicit:
        return explicit
    return ""


def search_with_crag(query: str, top_k: int) -> tuple[list[dict[str, Any]], bool, str, float]:
    started = time.perf_counter()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        results = retrieve_with_correction(query, top_k=top_k)
    elapsed_ms = (time.perf_counter() - started) * 1000
    crag_output = captured.getvalue()
    crag_triggered = parse_crag_triggered(crag_output)
    print(crag_output, end="")
    return results, crag_triggered, crag_output, elapsed_ms


def normalize_result(item: dict[str, Any], rank: int, crag_triggered: bool) -> dict[str, Any]:
    enriched_lookup = enriched_by_id()
    enriched = enriched_lookup.get(component_key(item)) or enriched_lookup.get(str(item.get("id") or ""), {})
    merged = {**enriched, **item}
    score = float(item.get("score", 0.0) or 0.0)
    return {
        "rank": rank,
        "display_name": merged.get("display_name") or merged.get("name") or merged.get("clean_id") or merged.get("id"),
        "clean_id": merged.get("clean_id") or "",
        "id": merged.get("id") or "",
        "server": merged.get("server") or "unknown",
        "component_type": merged.get("component_type") or "other",
        "visual_summary": merged.get("visual_summary") or "",
        "tags": as_list(merged.get("tags")),
        "style_tags": as_list(merged.get("style_tags")),
        "interaction_tags": as_list(merged.get("interaction_tags")),
        "generation_prompt": merged.get("generation_prompt") or "",
        "preview_url": preview_url_for(merged),
        "has_source_code": bool(merged.get("has_source_code") or str(merged.get("source_code") or "").strip()),
        "score": max(0.0, min(1.0, score)),
        "normalized_score": item.get("normalized_score", score),
        "cross_encoder_score": item.get("cross_encoder_score"),
        "faiss_score": item.get("faiss_score"),
        "bm25_score": item.get("bm25_score"),
        "rrf_score": item.get("rrf_score"),
        "crag_triggered": crag_triggered,
    }


def local_score_explanation(query: str, results: list[dict[str, Any]], crag_triggered: bool, response_time_ms: float) -> str:
    if not results:
        return (
            "No candidates survived the retrieval pipeline. Try a broader English description, "
            "for example 'animated loading spinner' or 'data table with sorting'."
        )
    top = results[0]
    final_score = float(top.get("score") or 0.0)
    raw_score = top.get("cross_encoder_score")
    rrf_score = top.get("rrf_score")
    source_bits = []
    if top.get("faiss_score") is not None:
        source_bits.append("FAISS found it semantically close")
    if top.get("bm25_score") is not None:
        source_bits.append("BM25 matched exact words or component terms")
    source_text = "; ".join(source_bits) if source_bits else "hybrid retrieval selected it from the fused candidate list"
    return (
        f"For the English query '{query}', VibePull first retrieved candidates with FAISS dense search and BM25 lexical search, "
        f"combined them with RRF, then reranked them with the cross-encoder. The top result is {top.get('display_name')} "
        f"from {top.get('server')} because {source_text}. Its final normalized score is {final_score:.2f}; "
        f"raw cross-encoder score is {float(raw_score):.3f}." if raw_score is not None else
        f"For the English query '{query}', the top result is {top.get('display_name')} from {top.get('server')} with final score {final_score:.2f}."
    ) + (
        f" RRF score is {float(rrf_score):.4f}." if rrf_score is not None else ""
    ) + (
        " CRAG triggered because confidence was low, so the query was expanded before returning results."
        if crag_triggered else
        " CRAG did not trigger because the cross-encoder confidence passed the threshold."
    ) + f" Total backend retrieval time was {response_time_ms:.0f} ms."


def gemini_score_explanation(query: str, results: list[dict[str, Any]], crag_triggered: bool, response_time_ms: float) -> dict[str, Any]:
    local = local_score_explanation(query, results, crag_triggered, response_time_ms)
    api_key = get_env_value("GEMINI_API_KEY") or get_env_value("GOOGLE_API_KEY")
    if not api_key:
        return {
            "provider": "local",
            "configured": False,
            "model": None,
            "text": local,
            "note": "Set GEMINI_API_KEY to enable Gemini's live explanation.",
        }

    compact_results = [
        {
            "rank": item.get("rank"),
            "name": item.get("display_name"),
            "library": item.get("server"),
            "type": item.get("component_type"),
            "final_score": item.get("score"),
            "cross_encoder_raw": item.get("cross_encoder_score"),
            "rrf_score": item.get("rrf_score"),
            "faiss_score": item.get("faiss_score"),
            "bm25_score": item.get("bm25_score"),
            "summary": item.get("visual_summary"),
            "tags": item.get("tags"),
        }
        for item in results[:5]
    ]
    prompt = (
        "You are explaining an information retrieval demo to a professor. "
        "Explain why these UI component results are relevant for the natural-language query. "
        "Mention FAISS semantic retrieval, BM25 lexical matching, RRF fusion, cross-encoder reranking, "
        "and CRAG fallback. Keep it concise, honest, and presentation-friendly. "
        "Do not invent evaluation numbers.\n\n"
        f"Query: {query}\n"
        f"CRAG triggered: {crag_triggered}\n"
        f"Backend response time ms: {response_time_ms:.2f}\n"
        f"Top results JSON: {json.dumps(compact_results, ensure_ascii=False)}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 360},
    }
    req = urlrequest.Request(
        GEMINI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlrequest.urlopen(req, timeout=22) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = "".join(
                part.get("text", "")
                for candidate in data.get("candidates", [])
                for content in [candidate.get("content", {})]
                for part in content.get("parts", [])
            ).strip()
            return {
                "provider": "gemini",
                "configured": True,
                "model": GEMINI_MODEL,
                "text": text or local,
                "note": "Generated with Gemini API.",
            }
        except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
                continue
    return {
        "provider": "local",
        "configured": True,
        "model": GEMINI_MODEL,
        "text": local,
        "note": f"Gemini request failed after retries, using local explanation: {last_error}",
    }


def pipeline_payload(crag_triggered: bool, crag_output: str, elapsed_ms: float) -> dict[str, Any]:
    return {
        "query_language": "plain English natural-language query",
        "steps": [
            {"name": "FAISS dense retrieval", "status": "used", "detail": "Semantic embeddings retrieve visually/conceptually similar components."},
            {"name": "BM25 lexical retrieval", "status": "used", "detail": "Exact words like spinner, card, table, modal, and library terms are matched."},
            {"name": "RRF fusion", "status": "used", "detail": "Dense and lexical ranks are fused with 1 / (60 + rank)."},
            {"name": "Cross-encoder reranking", "status": "used", "detail": "Query-result pairs are rescored for final ranking."},
            {"name": "CRAG fallback", "status": "triggered" if crag_triggered else "not triggered", "detail": "If raw reranker confidence is low, the query is expanded and searched again."},
        ],
        "crag_triggered": crag_triggered,
        "top_raw_score": parse_top_raw_score(crag_output),
        "response_time_ms": round(elapsed_ms, 2),
    }


def corpus_stats() -> dict[str, Any]:
    metadata = metadata_records()
    source_counts = Counter(str(item.get("server") or "unknown") for item in metadata)
    type_counts = Counter(str(item.get("component_type") or "other") for item in metadata)
    source_code_count = sum(1 for item in metadata if item.get("has_source_code"))
    prompt_count = sum(1 for item in metadata if item.get("generation_prompt"))
    preview_count = sum(1 for path in PREVIEW_ROOT.rglob("*.png")) if PREVIEW_ROOT.exists() else 0
    raw_enriched = Counter(path.parent.name for path in ENRICHED_ROOT.rglob("*.json"))
    return {
        "indexed_components": len(metadata),
        "components_with_source_code": source_code_count,
        "components_with_generation_prompt": prompt_count,
        "components_with_preview_images": preview_count,
        "sources": [{"name": name, "count": count} for name, count in sorted(source_counts.items())],
        "component_types": [{"name": name, "count": count} for name, count in type_counts.most_common()],
        "enriched_json_by_source": [
            {"name": name, "count": count} for name, count in sorted(raw_enriched.items())
        ],
    }


@app.get("/")
def index() -> Any:
    return send_from_directory(WEB_ROOT, "index.html")


@app.get("/health")
def health() -> Any:
    indexed_components = len(metadata_records())
    return jsonify({"status": "ok", "indexed_components": indexed_components})


@app.get("/stats")
def stats() -> Any:
    return jsonify(corpus_stats())


@app.post("/search")
def search() -> Any:
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    component_type = str(payload.get("component_type") or "all").strip().lower()
    try:
        top_k = int(payload.get("top_k") or 10)
    except (TypeError, ValueError):
        top_k = 10
    top_k = max(1, min(top_k, 50))
    include_explanation = bool(payload.get("include_explanation"))

    if not query:
        return jsonify({"query": query, "results": [], "total": 0, "crag_triggered": False})

    retrieval_top_k = max(top_k, 20) if component_type != "all" else top_k
    raw_results, crag_triggered, crag_output, elapsed_ms = search_with_crag(query, retrieval_top_k)
    if component_type != "all":
        raw_results = [
            item for item in raw_results
            if str(item.get("component_type") or "").strip().lower() == component_type
        ]
    normalized = [
        normalize_result(item, rank, crag_triggered)
        for rank, item in enumerate(raw_results[:top_k], start=1)
    ]
    pipeline = pipeline_payload(crag_triggered, crag_output, elapsed_ms)
    explanation = (
        gemini_score_explanation(query, normalized, crag_triggered, elapsed_ms)
        if include_explanation else
        local_score_explanation(query, normalized, crag_triggered, elapsed_ms)
    )
    return jsonify(
        {
            "query": query,
            "results": normalized,
            "total": len(normalized),
            "crag_triggered": crag_triggered,
            "crag_log": crag_output.strip(),
            "response_time_ms": round(elapsed_ms, 2),
            "pipeline": pipeline,
            "explanation": explanation,
        }
    )


@app.post("/explain")
def explain() -> Any:
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    crag_triggered = bool(payload.get("crag_triggered"))
    try:
        response_time_ms = float(payload.get("response_time_ms") or 0.0)
    except (TypeError, ValueError):
        response_time_ms = 0.0
    return jsonify(gemini_score_explanation(query, results, crag_triggered, response_time_ms))


@app.get("/components")
def components() -> Any:
    source = str(request.args.get("source") or "all").strip().lower()
    component_type = str(request.args.get("component_type") or "all").strip().lower()
    query = str(request.args.get("q") or "").strip().lower()
    try:
        limit = int(request.args.get("limit") or 48)
    except (TypeError, ValueError):
        limit = 48
    try:
        offset = int(request.args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 120))
    offset = max(0, offset)

    records: list[dict[str, Any]] = []
    for item in metadata_records():
        if source != "all" and str(item.get("server") or "").lower() != source:
            continue
        if component_type != "all" and str(item.get("component_type") or "").lower() != component_type:
            continue
        if query:
            haystack = " ".join(
                str(value)
                for value in (
                    item.get("display_name"),
                    item.get("clean_id"),
                    item.get("id"),
                    item.get("server"),
                    item.get("component_type"),
                    item.get("visual_summary"),
                    " ".join(as_list(item.get("tags"))),
                    " ".join(as_list(item.get("style_tags"))),
                    " ".join(as_list(item.get("interaction_tags"))),
                )
            ).lower()
            if query not in haystack:
                continue
        records.append(item)

    records.sort(key=lambda item: (
        str(item.get("server") or ""),
        str(item.get("display_name") or item.get("name") or item.get("id") or ""),
    ))
    total = len(records)
    page = records[offset:offset + limit]
    normalized = [
        normalize_result({**item, "score": 1.0}, rank=offset + idx, crag_triggered=False)
        for idx, item in enumerate(page, start=1)
    ]
    return jsonify(
        {
            "results": normalized,
            "total": total,
            "limit": limit,
            "offset": offset,
            "source": source,
            "component_type": component_type,
            "query": query,
        }
    )


@app.get("/<path:path>")
def static_files(path: str) -> Any:
    return send_from_directory(WEB_ROOT, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
