#!/usr/bin/env python3
"""Generate synthetic developer queries for Magic UI components with Groq."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq
from tqdm import tqdm


MAGICUI_ROOT = Path("data/enriched/magicui")
OUTPUT_PATH = Path("data/synthetic_queries.json")
MODEL_NAME = "llama-3.1-8b-instant"
SLEEP_SECONDS = 1
SYSTEM_PROMPT = "Return ONLY a JSON array of 3 strings. No markdown, no explanation."


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_existing() -> list[dict[str, Any]]:
    if not OUTPUT_PATH.exists():
        return []
    payload = load_json(OUTPUT_PATH)
    return payload if isinstance(payload, list) else []


def clean_json_array(text: str) -> list[str]:
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, list):
        raise ValueError("Groq response was not a JSON array")
    queries = [str(item).strip() for item in payload if str(item).strip()]
    if len(queries) != 3:
        raise ValueError(f"Expected 3 queries, got {len(queries)}")
    return queries


def user_prompt(component: dict[str, Any]) -> str:
    return (
        "Given this UI component, generate 3 natural language queries a developer might type "
        f"to find it. Component: {component.get('name')}. "
        f"Summary: {component.get('visual_summary')}. Tags: {component.get('tags')}"
    )


def generate_queries(client: Groq, component: dict[str, Any]) -> list[str]:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(component)},
        ],
    )
    content = response.choices[0].message.content or ""
    return clean_json_array(content)


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY is missing. Add it to .env or the environment.")

    client = Groq(api_key=api_key)
    existing = load_existing()
    processed = {str(item.get("component_id")) for item in existing}
    output = list(existing)

    paths = sorted(MAGICUI_ROOT.glob("*.json"))
    for path in tqdm(paths, desc="synthetic queries", unit="component"):
        component = load_json(path)
        if not isinstance(component, dict) or component.get("enrichment_failed") is True:
            continue
        component_id = str(component.get("id") or path.stem)
        if component_id in processed:
            continue
        queries = generate_queries(client, component)
        output.append({"component_id": component_id, "queries": queries})
        processed.add(component_id)
        write_json(OUTPUT_PATH, output)
        time.sleep(SLEEP_SECONDS)

    write_json(OUTPUT_PATH, output)
    print(f"Saved synthetic queries for {len(output)} components to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
