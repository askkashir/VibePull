#!/usr/bin/env python3
"""Enrich raw UI component JSON files with the Groq API."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq
from tqdm import tqdm


LOGGER = logging.getLogger("component-enrich")
RAW_ROOT = Path("data/raw")
ENRICHED_ROOT = Path("data/enriched")
MODEL_NAME = "llama-3.1-8b-instant"
SLEEP_SECONDS = 1
SOURCE_FOLDERS = (
    "shadcn",
    "magicui",
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

SYSTEM_PROMPT = """You are a UI component analyst. Given a React component, return ONLY a valid JSON object with no markdown, no backticks, no extra text. The JSON must have exactly these fields:
{
  "visual_summary": "2-3 sentence description of what this component looks like and when a developer would use it",
  "tags": ["tag1", "tag2", "tag3"],
  "style_tags": ["e.g. dark", "minimal", "glassmorphism"],
  "interaction_tags": ["e.g. animated", "hover", "clickable"],
  "component_type": "one of: button, card, navbar, modal, form, table, loader, hero, input, badge, other"
}"""

FIRST_RAW_RESPONSE_PRINTED = False
client: Groq | None = None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        LOGGER.error("Failed to read %s: %s", path, error)
        return None
    if not isinstance(data, dict):
        LOGGER.error("Skipping %s because it is not a JSON object", path)
        return None
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def nested_get(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def component_type(data: dict[str, Any]) -> str:
    value = (
        nested_get(data, ("metadata", "listed_component", "type"))
        or nested_get(data, ("metadata", "registry_item", "type"))
        or data.get("type")
        or ""
    )
    return str(value)


def component_description(data: dict[str, Any]) -> str:
    value = (
        data.get("description")
        or nested_get(data, ("metadata", "listed_component", "description"))
        or nested_get(data, ("metadata", "registry_item", "description"))
        or ""
    )
    return str(value)


def component_id(data: dict[str, Any], path: Path) -> str:
    value = data.get("id") or data.get("name") or path.stem
    return str(value)


def safe_component_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "unknown"


def component_name(data: dict[str, Any], path: Path) -> str:
    value = data.get("name") or data.get("id") or path.stem
    return str(value)


def prompt_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def build_generation_prompt(data: dict[str, Any], path: Path | None = None) -> str:
    name = str(data.get("name") or data.get("id") or (path.stem if path else "Component"))
    visual_summary = str(data.get("visual_summary") or data.get("description") or "").strip()
    ctype = str(data.get("component_type") or component_type(data) or "other").strip()
    style_tags = prompt_list(data.get("style_tags"))
    interaction_tags = prompt_list(data.get("interaction_tags"))
    return (
        f"Generate a production-ready React + Tailwind CSS component called {name}. "
        f"It should be: {visual_summary}. Component type: {ctype}. "
        f"Style: {style_tags}. Interactions: {interaction_tags}. "
        "Return only the complete component code."
    )


def words_from_name(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    tokens = re.findall(r"[a-z0-9]+", spaced.replace("_", " ").replace("-", " ").lower())
    return [token for token in tokens if token not in {"src", "components", "component", "index"}]


def infer_component_type(name: str, description: str = "") -> str:
    text = f"{name} {description}".lower()
    candidates = (
        ("button", "button"),
        ("card", "card"),
        ("nav", "navbar"),
        ("menu", "navbar"),
        ("dialog", "modal"),
        ("modal", "modal"),
        ("drawer", "modal"),
        ("popover", "modal"),
        ("form", "form"),
        ("input", "input"),
        ("select", "input"),
        ("textarea", "input"),
        ("checkbox", "input"),
        ("radio", "input"),
        ("table", "table"),
        ("loader", "loader"),
        ("spinner", "loader"),
        ("hero", "hero"),
        ("badge", "badge"),
    )
    for needle, ctype in candidates:
        if needle in text:
            return ctype
    return "other"


def fallback_enrichment(data: dict[str, Any], path: Path) -> dict[str, Any]:
    name = component_name(data, path)
    description = component_description(data)
    source_code = str(data.get("source_code") or "")
    tokens = words_from_name(name)
    ctype = component_type(data) or infer_component_type(name, description)
    if ctype.startswith("registry:"):
        ctype = infer_component_type(name, description)

    style_tags: list[str] = []
    lower_code = source_code.lower()
    if "dark:" in lower_code or "theme" in lower_code:
        style_tags.append("themeable")
    if "rounded" in lower_code:
        style_tags.append("rounded")
    if "shadow" in lower_code:
        style_tags.append("shadow")
    if "gradient" in lower_code:
        style_tags.append("gradient")
    if "tailwind" in lower_code or "class" in lower_code:
        style_tags.append("utility-styled")

    interaction_tags: list[str] = []
    if any(term in lower_code for term in ("onclick", "onchange", "onopenchange", "onvaluechange")):
        interaction_tags.append("interactive")
    if any(term in lower_code for term in ("hover:", "data-hover", "whilehover")):
        interaction_tags.append("hover")
    if any(term in lower_code for term in ("transition", "animate", "motion")):
        interaction_tags.append("animated")
    if any(term in lower_code for term in ("focus:", "focusvisible", "focus-visible")):
        interaction_tags.append("keyboard-focus")

    readable = " ".join(tokens[:4]) or name
    summary = description.strip() or (
        f"{name} is a {readable} UI component from the {data.get('server') or path.parent.name} corpus. "
        f"It provides reusable {ctype} behavior and styling for React or web UI screens."
    )
    tags = sorted(set([*(tokens[:6]), str(data.get("server") or path.parent.name), ctype]))
    return {
        "visual_summary": summary,
        "tags": tags,
        "style_tags": sorted(set(style_tags)),
        "interaction_tags": sorted(set(interaction_tags)),
        "component_type": ctype,
        "enrichment_method": "local_fallback",
    }


def add_generation_prompt_if_needed(data: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    if str(data.get("source_code") or "").strip():
        data.pop("generation_prompt", None)
        return data
    data["generation_prompt"] = build_generation_prompt(data, path)
    return data


def has_name_or_description(data: dict[str, Any], path: Path) -> bool:
    return bool(component_name(data, path).strip() or component_description(data).strip())


def user_message(data: dict[str, Any], path: Path) -> str:
    name = component_name(data, path)
    source_code = str(data.get("source_code") or "")

    if source_code.strip():
        # truncated to fit token limit
        source_code = source_code[:1200]
        return f"Component name: {name}\nSource code:\n{source_code}"

    return (
        f"Component name: {name}\n"
        f"Type: {component_type(data)}\n"
        f"Description: {component_description(data)}\n"
        "Generate a visual summary and tags as if this were a real UI component of this type."
    )


def strip_json_markdown(text: str) -> str:
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()
    return stripped.strip("` \n\r\t")


def parse_gemini_json(raw_text: str) -> dict[str, Any]:
    cleaned = strip_json_markdown(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        cleaned = re.sub(r',\s*([}\]])', r"\1", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            LOGGER.error("Failed to parse model JSON response. Raw text: %s", raw_text)
            raise
    if not isinstance(parsed, dict):
        raise ValueError("Gemini response is not a JSON object")
    return parsed


def call_gemini(user_message: str) -> str:
    if client is None:
        raise RuntimeError("Groq client is not configured")
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    content = response.choices[0].message.content
    return content or ""


def enrich_with_gemini(message: str) -> dict[str, Any]:
    global FIRST_RAW_RESPONSE_PRINTED

    raw_text = call_gemini(message)
    if not FIRST_RAW_RESPONSE_PRINTED:
        print("\n=== RAW GEMINI RESPONSE FROM FIRST COMPONENT ===")
        print(raw_text)
        print("=== END RAW GEMINI RESPONSE ===\n")
        FIRST_RAW_RESPONSE_PRINTED = True
    return parse_gemini_json(raw_text)


def raw_paths(server: str) -> list[Path]:
    servers = list(SOURCE_FOLDERS) if server == "all" else [server]
    paths: list[Path] = []
    for server_name in servers:
        paths.extend(sorted((RAW_ROOT / server_name).glob("*.json")))
    return paths


def output_path(data: dict[str, Any], path: Path, server: str) -> Path:
    return ENRICHED_ROOT / server / f"{safe_component_id(component_id(data, path))}.json"


def should_skip_existing(path: Path) -> bool:
    if not path.exists():
        return False
    existing = read_json(path)
    if existing is not None and existing.get("enrichment_failed") is True:
        LOGGER.info("Retrying failed enrichment for %s", path)
        path.unlink()
        return False
    if existing is not None:
        updated = dict(existing)
        add_generation_prompt_if_needed(updated, path)
        if updated != existing:
            write_json(path, updated)
    return True


def enrich(args: argparse.Namespace) -> None:
    global client

    load_dotenv()
    load_dotenv(Path(__file__).resolve().parent / ".env")
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit(
            "GROQ_API_KEY is missing. Add it to .env or the environment."
        )

    client = Groq(api_key=api_key)

    processed = 0
    succeeded = 0
    skipped = 0
    failed = 0

    for path in tqdm(raw_paths(args.server), desc="enrich components", unit="component"):
        server = path.parent.name
        data = read_json(path)
        if data is None:
            failed += 1
            continue

        out_path = output_path(data, path, server)
        if should_skip_existing(out_path):
            skipped += 1
            continue

        source_code = str(data.get("source_code") or "")
        if not source_code.strip() and not has_name_or_description(data, path):
            LOGGER.error("Skipping %s because source_code, name, and description are all missing", path)
            skipped += 1
            continue

        processed += 1
        try:
            if args.local_only:
                enrichment = fallback_enrichment(data, path)
            else:
                enrichment = enrich_with_gemini(user_message(data, path))
            merged = {**data, **enrichment}
            add_generation_prompt_if_needed(merged, path)
            write_json(out_path, merged)
            succeeded += 1
        except Exception as error:
            LOGGER.error("Gemini enrichment failed for %s: %s", path, error)
            try:
                fallback_record = {**data, **fallback_enrichment(data, path)}
                add_generation_prompt_if_needed(fallback_record, path)
                write_json(out_path, fallback_record)
                succeeded += 1
            except Exception:
                failed_record = {**data, "enrichment_failed": True}
                add_generation_prompt_if_needed(failed_record, path)
                write_json(out_path, failed_record)
                failed += 1
        finally:
            if not args.local_only:
                time.sleep(SLEEP_SECONDS)

    print(f"total processed: {processed}")
    print(f"succeeded: {succeeded}")
    print(f"skipped (already enriched): {skipped}")
    print(f"failed: {failed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        choices=(*SOURCE_FOLDERS, "all"),
        default="all",
        help="Raw component source to enrich.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Use deterministic local enrichment without calling Groq.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    enrich(args)


if __name__ == "__main__":
    main()
