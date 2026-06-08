#!/usr/bin/env python3
"""Ingest UI component payloads from MCP and registry sources."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tqdm import tqdm


LOGGER = logging.getLogger("mcp-component-ingest")
RAW_FIRST_TOOL_CALL_PRINTED = False
DEFAULT_DATA_ROOT = Path("data/raw")
DEFAULT_SHADCN_REGISTRY = "@shadcn"
HTTP_DELAY_SECONDS = 0.5
REFETCH_SOURCE_DELAY_SECONDS = 0.3
HTTP_TIMEOUT_SECONDS = 20
SOURCE_FIX_TIMEOUT_SECONDS = 2
SOURCE_FIX_WORKERS = 32
SHADCN_REGISTRY_ITEM_URL = "https://registry.shadcn.com/r/{name}.json"
SHADCN_GITHUB_ITEM_URL = (
    "https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/www/public/r/{name}.json"
)
SHADCN_SOURCE_URLS = (
    "https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/www/public/registry/styles/default/{component_id}.json",
    "https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/www/public/registry/styles/new-york/{component_id}.json",
    "https://ui.shadcn.com/registry/styles/default/{component_id}.json",
)
MAGICUI_BASE_URL = "https://raw.githubusercontent.com/magicuidesign/magicui/main"
MAGICUI_INDEX_URL = f"{MAGICUI_BASE_URL}/registry.json"
MAGICUI_ITEM_URL = f"{MAGICUI_BASE_URL}/apps/www/public/r/{{name}}.json"
ACETERNITY_INDEX_URL = "https://raw.githubusercontent.com/aceternity/ui/main/registry.json"
ACETERNITY_GITHUB_CONTENTS_URL = (
    "https://api.github.com/repos/nickgraffis/aceternity-ui/contents/registry"
)
ACETERNITY_COMPONENT_REGISTRY_URL = "https://ui.aceternity.com/registry/{name}.json"
ACETERNITY_AI_CATALOG_URL = "https://ui.aceternity.com/ai-recommendations"
ADDITIONAL_SOURCES: dict[str, dict[str, Any]] = {
    "originui": {
        "primary": "https://raw.githubusercontent.com/origin-space/originui/main/registry.json",
        "fallback": "https://api.github.com/repos/origin-space/originui/contents/components",
        "fallback_kind": "github_contents",
    },
    "cultui": {
        "primary": "https://raw.githubusercontent.com/nolly-studio/cult-ui/main/apps/www/public/r/index.json",
        "fallback": "https://api.github.com/repos/nolly-studio/cult-ui/contents/apps/www/public/r",
        "fallback_kind": "github_contents",
    },
    "kokonutui": {
        "primary": None,
        "fallback": "https://api.github.com/repos/kokonutd/kokonutui/contents/registry",
        "fallback_kind": "github_contents",
    },
    "lukachoui": {
        "primary": None,
        "fallback": "https://api.github.com/repos/lukacho-ui/lukacho-ui/contents/registry",
        "fallback_kind": "github_contents",
    },
    "numberflow": {
        "primary": "https://raw.githubusercontent.com/barvian/number-flow/main/packages/react/src",
        "fallback": "https://api.github.com/repos/barvian/number-flow/contents/packages/react/src",
        "fallback_kind": "github_contents",
    },
    "fancyui": {
        "primary": None,
        "fallback": "https://api.github.com/repos/fancy-components/fancy-components/contents/registry",
        "fallback_kind": "github_contents",
    },
    "motionprimitives": {
        "primary": "https://raw.githubusercontent.com/ibelick/motion-primitives/main/registry.json",
        "fallback": "https://api.github.com/repos/ibelick/motion-primitives/contents/components/core",
        "fallback_kind": "github_contents",
    },
    "eldoraui": {
        "primary": "https://raw.githubusercontent.com/keshavg2/eldoraui/main/apps/www/public/r/index.json",
        "fallback": "https://api.github.com/repos/keshavg2/eldoraui/contents/apps/www/public/r",
        "fallback_kind": "github_contents",
    },
    "radixuithemes": {
        "primary": None,
        "fallback": "https://api.github.com/repos/radix-ui/themes/contents/packages/radix-ui-themes/src/components",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/radix-ui/themes/zip/refs/heads/main",
        "zip_prefix": "packages/radix-ui-themes/src/components/",
        "include_extensions": (".tsx", ".ts"),
        "exclude_patterns": ("/_internal/", ".props.", ".stories.", ".spec.", ".test."),
    },
    "tremor": {
        "primary": "https://raw.githubusercontent.com/tremorlabs/tremor/main/src/components",
        "fallback": "https://api.github.com/repos/tremorlabs/tremor/contents/src/components",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/tremorlabs/tremor/zip/refs/heads/main",
        "zip_prefix": "src/components/",
        "include_extensions": (".tsx", ".ts"),
        "exclude_patterns": (".stories.", ".spec.", ".test.", "test-utils"),
    },
    "hyperui": {
        "primary": None,
        "fallback": "https://api.github.com/repos/markmead/hyperui/contents/src/components",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/markmead/hyperui/zip/refs/heads/main",
        "zip_prefix": "src/components/",
        "include_extensions": (".astro", ".tsx", ".ts", ".jsx", ".js"),
        "recursive": False,
    },
    "pinesui": {
        "primary": "https://api.github.com/repos/thedevdojo/pines/contents/components",
        "fallback": "https://api.github.com/repos/thedevdojo/pines/contents/elements",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/thedevdojo/pines/zip/refs/heads/main",
        "zip_prefix": "elements/",
        "include_extensions": (".html",),
        "recursive": False,
    },
    "parkui": {
        "primary": "https://raw.githubusercontent.com/chakra-ui/park-ui/main/components/react/registry.json",
        "fallback": "https://api.github.com/repos/cschroeter/park-ui/contents/components",
        "fallback_kind": "github_contents",
        "file_base_url": "https://raw.githubusercontent.com/chakra-ui/park-ui/main/components/react/",
    },
    "heroui": {
        "primary": "https://api.github.com/repos/nextui-org/nextui/contents/packages/components",
        "fallback": "https://api.github.com/repos/heroui-inc/heroui/contents/packages/react/src/components?ref=v3",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/heroui-inc/heroui/zip/refs/heads/v3",
        "zip_prefix": "packages/react/src/components/",
        "include_extensions": (".tsx", ".ts"),
        "exclude_patterns": (".stories.", ".spec.", ".test.", ".types.", "__tests__"),
    },
    "floatui": {
        "primary": None,
        "fallback": "https://api.github.com/repos/MarsX-dev/floatui/contents/components",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/MarsX-dev/floatui/zip/refs/heads/main",
        "zip_prefix": "components/",
        "include_extensions": (".tsx", ".ts", ".jsx", ".js"),
    },
    "mantine": {
        "primary": "https://api.github.com/repos/mantinedev/mantine/contents/src/mantine-core/src/components",
        "fallback": "https://api.github.com/repos/mantinedev/mantine/contents/packages/@mantine/core/src/components",
        "fallback_kind": "github_contents",
        "zip_url": "https://codeload.github.com/mantinedev/mantine/zip/refs/heads/master",
        "zip_prefix": "packages/@mantine/core/src/components/",
        "include_extensions": (".tsx", ".ts"),
        "exclude_patterns": (".stories.", ".spec.", ".test.", ".demo.", ".demos.", ".styles-api.", "__tests__"),
    },
}
EXTERNAL_SOURCE_NAMES = tuple(ADDITIONAL_SOURCES)
LIST_LINE = re.compile(
    r"^- (?P<name>\S+) \((?P<type>[^)]+)\)"
    r"(?: - (?P<description>.*?))?"
    r"(?: \[(?P<registry>[^\]]+)\])?\s*$"
)
CODE_FENCE = re.compile(r"```[^\n]*\n(?P<code>.*?)```", re.DOTALL)
ACETERNITY_SLUG = re.compile(r"@aceternity/([a-z0-9-]+)|/components/([a-z0-9-]+)")


@dataclass(frozen=True)
class Server:
    name: str
    parameters: StdioServerParameters


def build_servers() -> list[Server]:
    """Return stdio MCP server commands.

    shadcn's CLI needs the ``mcp`` subcommand to stay open as an MCP server.
    ``npx -y`` avoids an install prompt when the package is not cached yet.
    """
    inherited_env = dict(os.environ)
    return [
        Server(
            name="shadcn",
            parameters=StdioServerParameters(
                command="npx",
                args=["-y", "shadcn@latest", "mcp"],
                env=inherited_env,
            ),
        ),
    ]


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def pretty(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, default=str)


def tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", ""))


def tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    return to_jsonable(schema)


async def call_tool_raw_first(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> Any:
    """Call an MCP tool and expose the first result before any parsing."""
    global RAW_FIRST_TOOL_CALL_PRINTED

    result = await session.call_tool(name, arguments)
    if not RAW_FIRST_TOOL_CALL_PRINTED:
        print("\n=== RAW RESPONSE FROM FIRST TOOL CALL ===")
        print(f"tool: {name}")
        print(f"arguments: {pretty(arguments)}")
        print(pretty(result))
        print("=== END RAW RESPONSE ===\n")
        RAW_FIRST_TOOL_CALL_PRINTED = True
    return result


def result_payloads(result: Any) -> list[Any]:
    """Extract structured JSON and text/JSON text blocks from a tool result."""
    payloads: list[Any] = []
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        payloads.append(to_jsonable(structured))

    for block in getattr(result, "content", []) or []:
        block_json = to_jsonable(block)
        text = block_json.get("text") if isinstance(block_json, dict) else None
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            payloads.append(json.loads(text))
        except json.JSONDecodeError:
            payloads.append(text)
    return payloads


def walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_json(nested)


def string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def candidate_id(candidate: dict[str, Any]) -> str | None:
    for key in ("id", "name", "component_id", "componentId", "slug", "title"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_component_candidates(payloads: list[Any]) -> list[dict[str, Any]]:
    """Find registry/component-like records in list-tool results."""
    components: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if isinstance(payload, str):
            for line in payload.splitlines():
                match = LIST_LINE.match(line.strip())
                if not match:
                    continue
                record = {key: value for key, value in match.groupdict().items() if value}
                record["tags"] = [record["type"]]
                components.setdefault(record["name"], record)
        for value in walk_json(payload):
            if not isinstance(value, dict):
                continue
            component_id = candidate_id(value)
            if not component_id:
                continue
            lower_keys = {str(key).lower() for key in value}
            looks_like_component = bool(
                lower_keys
                & {
                    "description",
                    "files",
                    "source",
                    "source_code",
                    "tags",
                    "type",
                    "registrydependencies",
                    "categories",
                }
            )
            if looks_like_component or "name" in lower_keys:
                components.setdefault(component_id, value)
    return list(components.values())


def required_fields(tool: Any) -> set[str]:
    required = tool_schema(tool).get("required", [])
    return {field for field in required if isinstance(field, str)}


def first_matching_tool(tools: list[Any], preferred_names: list[str]) -> Any | None:
    tools_by_name = {tool_name(tool): tool for tool in tools}
    for name in preferred_names:
        if name in tools_by_name:
            return tools_by_name[name]
    return None


def list_tool_candidates(server: str, tools: list[Any]) -> list[Any]:
    preferred = {
        "shadcn": ["list_items_in_registries", "search_items_in_registries"],
    }.get(server, [])
    candidates: list[Any] = []
    preferred_tool = first_matching_tool(tools, preferred)
    if preferred_tool is not None:
        candidates.append(preferred_tool)

    for tool in tools:
        name = tool_name(tool).lower()
        if tool in candidates:
            continue
        if any(word in name for word in ("list", "browse", "search")) and any(
            word in name for word in ("component", "item", "registry")
        ):
            candidates.append(tool)
    return candidates


def list_arguments(tool: Any, registry: str) -> dict[str, Any] | None:
    """Build conservative catalog arguments from the advertised JSON schema."""
    properties = tool_schema(tool).get("properties", {})
    required = required_fields(tool)
    arguments: dict[str, Any] = {}

    for field in required:
        lower = field.lower()
        if "registr" in lower:
            arguments[field] = [registry]
        elif lower in {"limit", "take", "page_size", "pagesize"}:
            arguments[field] = 1000
        elif lower in {"offset", "page"}:
            arguments[field] = 0
        elif "query" in lower or lower in {"search", "term"}:
            arguments[field] = ""
        else:
            return None

    for field in properties:
        lower = field.lower()
        if field not in arguments and "registr" in lower:
            arguments[field] = [registry]
        if field not in arguments and lower in {"limit", "take"}:
            arguments[field] = 1000
    return arguments


async def discover_components(
    session: ClientSession, server: str, tools: list[Any], registry: str
) -> list[dict[str, Any]]:
    for list_tool in list_tool_candidates(server, tools):
        arguments = list_arguments(list_tool, registry)
        if arguments is None:
            LOGGER.info(
                "Skipping %s.%s because it requires non-catalog inputs: %s",
                server,
                tool_name(list_tool),
                sorted(required_fields(list_tool)),
            )
            continue
        try:
            LOGGER.info("Listing %s components with %s", server, tool_name(list_tool))
            result = await call_tool_raw_first(session, tool_name(list_tool), arguments)
            candidates = extract_component_candidates(result_payloads(result))
            if candidates:
                return candidates
            LOGGER.warning("%s.%s returned no parseable components", server, tool_name(list_tool))
        except Exception:
            LOGGER.exception("Component listing failed through %s.%s", server, tool_name(list_tool))
    return []


def fetch_tool_candidates(tools: list[Any]) -> list[Any]:
    preferred = [
        "view_items_in_registries",
        "get_item",
        "get_component",
        "fetch_component",
        "read_component",
    ]
    candidates: list[Any] = []
    preferred_tool = first_matching_tool(tools, preferred)
    if preferred_tool is not None:
        candidates.append(preferred_tool)
    for tool in tools:
        name = tool_name(tool).lower()
        if tool in candidates:
            continue
        if any(word in name for word in ("get", "fetch", "read", "view")) and any(
            word in name for word in ("component", "item", "registry")
        ):
            candidates.append(tool)
    return candidates


def argument_for_type(field_schema: dict[str, Any], component_id: str) -> Any:
    schema_type = field_schema.get("type")
    if schema_type == "array":
        return [component_id]
    return component_id


def fetch_arguments(tool: Any, component_id: str, registry: str) -> dict[str, Any] | None:
    schema = tool_schema(tool)
    properties = schema.get("properties", {})
    required = required_fields(tool)
    arguments: dict[str, Any] = {}

    for field in properties:
        lower = field.lower()
        field_schema = properties.get(field, {})
        if "registr" in lower:
            arguments[field] = [registry] if field_schema.get("type") == "array" else registry
        elif lower in {"id", "item", "items", "name", "names", "slug", "slugs"}:
            arguments[field] = argument_for_type(field_schema, component_id)
        elif "component" in lower and "depend" not in lower:
            arguments[field] = argument_for_type(field_schema, component_id)

    if required - set(arguments):
        return None
    return arguments


async def fetch_component(
    session: ClientSession,
    component_id: str,
    registry: str,
    fetch_tools: list[Any],
) -> tuple[Any, str] | tuple[None, None]:
    for fetch_tool in fetch_tools:
        arguments = fetch_arguments(fetch_tool, component_id, registry)
        if arguments is None:
            continue
        try:
            return (
                await call_tool_raw_first(session, tool_name(fetch_tool), arguments),
                tool_name(fetch_tool),
            )
        except Exception:
            LOGGER.exception("Fetching %s with %s failed", component_id, tool_name(fetch_tool))
    return None, None


def find_strings(payloads: list[Any], keys: set[str]) -> list[str]:
    found: list[str] = []
    for payload in payloads:
        for value in walk_json(payload):
            if not isinstance(value, dict):
                continue
            for key, nested in value.items():
                if str(key).lower() in keys:
                    found.extend(string_list(nested))
    return found


def extract_source_code(payloads: list[Any]) -> str:
    source_keys = {"source", "sourcecode", "source_code", "code", "content"}
    chunks: list[str] = []
    for payload in payloads:
        if isinstance(payload, str):
            for match in CODE_FENCE.finditer(payload):
                code = match.group("code").strip()
                if code and code not in chunks:
                    chunks.append(code)
        for value in walk_json(payload):
            if not isinstance(value, dict):
                continue
            for key, nested in value.items():
                if str(key).lower() not in source_keys or not isinstance(nested, str):
                    continue
                if nested.strip() and nested not in chunks:
                    path = value.get("path") or value.get("file") or value.get("name")
                    if isinstance(path, str) and path.strip() and key.lower() == "content":
                        chunks.append(f"// {path.strip()}\n{nested}")
                    else:
                        chunks.append(nested)
    return "\n\n".join(chunks)


def metadata_from_payloads(payloads: list[Any]) -> Any:
    if not payloads:
        return {}
    return payloads[0] if len(payloads) == 1 else payloads


def build_record(
    server: str,
    candidate: dict[str, Any],
    result: Any,
    fetch_tool: str | None,
) -> dict[str, Any]:
    payloads = result_payloads(result)
    component_id = candidate_id(candidate) or "unknown"
    fetched_names = find_strings(payloads, {"name", "title"})
    descriptions = find_strings(payloads, {"description", "summary"})
    fetched_tags = find_strings(payloads, {"tags", "categories", "category"})
    return {
        "id": component_id,
        "name": fetched_names[0] if fetched_names else candidate.get("name", component_id),
        "source_code": extract_source_code(payloads),
        "description": descriptions[0] if descriptions else candidate.get("description", ""),
        "tags": sorted(set(fetched_tags or string_list(candidate.get("tags")))),
        "server": server,
        "metadata": {
            "listed_component": to_jsonable(candidate),
            "fetch_tool": fetch_tool,
            "response": metadata_from_payloads(payloads),
        },
    }


def component_path(data_root: Path, server: str, component_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", component_id).strip("._")
    return data_root / server / f"{safe_id or 'unknown'}.json"


def save_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty(record) + "\n", encoding="utf-8")


def get_registry_json(url: str) -> dict[str, Any] | None:
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status_code == 404:
            LOGGER.info("Registry item not found: %s", url)
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            LOGGER.warning("Registry JSON is not an object: %s", url)
            return None
        return payload
    except requests.RequestException as error:
        LOGGER.warning("Registry request failed for %s: %s", url, error)
    except ValueError as error:
        LOGGER.warning("Registry response was not valid JSON for %s: %s", url, error)
    return None


def first_registry_file_content(payload: dict[str, Any]) -> str:
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        return ""
    first_file = files[0]
    if not isinstance(first_file, dict):
        return ""
    content = first_file.get("content")
    return content if isinstance(content, str) else ""


def fetch_shadcn_source(component_id: str) -> tuple[str, dict[str, Any], str] | None:
    """Fetch shadcn source code from style registry URLs."""
    for template in SHADCN_SOURCE_URLS:
        url = template.format(component_id=component_id)
        try:
            response = requests.get(
                url,
                timeout=SOURCE_FIX_TIMEOUT_SECONDS,
                headers={"User-Agent": "VibePull-ingest"},
            )
            if response.status_code == 404:
                LOGGER.info("shadcn source not found for %s: %s", component_id, url)
                continue
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            LOGGER.info("shadcn source request failed for %s from %s: %s", component_id, url, error)
            continue
        if not isinstance(payload, dict):
            continue
        source_code = first_registry_file_content(payload)
        if source_code:
            return source_code, payload, url
        LOGGER.info("shadcn source URL had no first-file content for %s: %s", component_id, url)
    return None


def fix_shadcn_sources(data_root: Path) -> None:
    """Update empty shadcn raw JSON files in place with registry source code."""
    shadcn_root = data_root / "shadcn"
    if not shadcn_root.exists():
        LOGGER.error("No shadcn data directory found at %s", shadcn_root)
        return

    paths = sorted(shadcn_root.glob("*.json"))
    targets: list[tuple[Path, dict[str, Any]]] = []
    read_failed = 0
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not read %s: %s", path, error)
            read_failed += 1
            continue
        if str(record.get("source_code") or "").strip():
            continue
        targets.append((path, record))

    def fix_one(path: Path, record: dict[str, Any]) -> bool:
        component_id = record.get("id") or path.stem
        if not isinstance(component_id, str) or not component_id.strip():
            LOGGER.warning("Skipping %s because it has no component id", path)
            return False
        result = fetch_shadcn_source(component_id.strip())
        if not result:
            return False
        source_code, payload, url = result
        record["source_code"] = source_code
        record.setdefault("metadata", {})["shadcn_source_item"] = payload
        record["metadata"]["shadcn_source_url"] = url
        save_record(path, record)
        return True

    succeeded = 0
    failed = read_failed
    with ThreadPoolExecutor(max_workers=SOURCE_FIX_WORKERS) as executor:
        futures = [executor.submit(fix_one, path, record) for path, record in targets]
        for future in tqdm(as_completed(futures), total=len(futures), desc="fix shadcn source", unit="component"):
            try:
                if future.result():
                    succeeded += 1
                else:
                    failed += 1
            except Exception:
                LOGGER.exception("Fixing shadcn source failed")
                failed += 1

    print(
        "shadcn source fix complete: "
        f"{succeeded} succeeded, {failed} failed, {len(paths) - len(targets)} already had source"
    )


def shadcn_registry_source_fallback(path: Path, record: dict[str, Any]) -> None:
    if record.get("source_code"):
        return
    name = record.get("name") or record.get("id")
    if not isinstance(name, str) or not name:
        return

    try:
        registry_urls = (
            SHADCN_REGISTRY_ITEM_URL.format(name=name),
            SHADCN_GITHUB_ITEM_URL.format(name=name),
        )
        payload = None
        payload_url = None
        for registry_url in registry_urls:
            payload = get_registry_json(registry_url)
            if payload:
                payload_url = registry_url
                break
        if not payload:
            return
        source_code = first_registry_file_content(payload)
        if not source_code:
            LOGGER.info("shadcn registry returned no first-file source for %s", name)
            return
        record["source_code"] = source_code
        record.setdefault("metadata", {})["shadcn_registry_item"] = payload
        record["metadata"]["shadcn_registry_url"] = payload_url
        save_record(path, record)
    finally:
        time.sleep(HTTP_DELAY_SECONDS)


def fetch_shadcn_source_by_name(name: str) -> tuple[str, dict[str, Any], str] | None:
    registry_urls = (
        SHADCN_REGISTRY_ITEM_URL.format(name=name),
        SHADCN_GITHUB_ITEM_URL.format(name=name),
    )
    for registry_url in registry_urls:
        payload = get_registry_json(registry_url)
        if not payload:
            continue
        source_code = first_registry_file_content(payload)
        if source_code:
            return source_code, payload, registry_url
        LOGGER.info("shadcn registry returned no first-file source for %s from %s", name, registry_url)
    return None


def refetch_shadcn_sources(data_root: Path) -> None:
    shadcn_root = data_root / "shadcn"
    if not shadcn_root.exists():
        LOGGER.error("No shadcn data directory found at %s", shadcn_root)
        return

    paths = sorted(shadcn_root.glob("*.json"))
    targets: list[tuple[Path, dict[str, Any]]] = []
    failed = 0
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Could not read %s: %s", path, error)
            failed += 1
            continue
        if record.get("source_code"):
            continue
        targets.append((path, record))

    succeeded = 0
    for path, record in tqdm(targets, desc="refetch shadcn source", unit="component"):
        component_id = record.get("id")
        if not isinstance(component_id, str) or not component_id.strip():
            LOGGER.warning("Skipping %s because it has no string id", path)
            failed += 1
            continue
        try:
            result = fetch_shadcn_source_by_name(component_id.strip())
            if not result:
                failed += 1
                continue
            source_code, payload, registry_url = result
            record["source_code"] = source_code
            record.setdefault("metadata", {})["shadcn_registry_item"] = payload
            record["metadata"]["shadcn_registry_url"] = registry_url
            save_record(path, record)
            succeeded += 1
        except Exception:
            LOGGER.exception("Refetching source for %s failed", component_id)
            failed += 1
        finally:
            time.sleep(REFETCH_SOURCE_DELAY_SECONDS)

    LOGGER.warning(
        "shadcn source refetch complete: %s succeeded, %s failed, %s already had source",
        succeeded,
        failed,
        len(paths) - len(targets),
    )


def magicui_tags(item: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    tags = string_list(item.get("tags")) + string_list(payload.get("tags"))
    for registry_type in (item.get("type"), payload.get("type")):
        if isinstance(registry_type, str) and registry_type.strip():
            tags.append(registry_type.strip())
    return sorted(set(tags))


def build_magicui_record(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    component_id = candidate_id(payload) or candidate_id(item) or "unknown"
    name = payload.get("title") or item.get("title") or payload.get("name") or component_id
    description = payload.get("description") or item.get("description") or ""
    return {
        "id": component_id,
        "name": name,
        "source_code": first_registry_file_content(payload),
        "description": description,
        "tags": magicui_tags(item, payload),
        "server": "magicui",
        "metadata": {
            "listed_component": to_jsonable(item),
            "registry_item": to_jsonable(payload),
        },
    }


def ingest_magicui(data_root: Path, max_components: int | None) -> None:
    index = get_registry_json(MAGICUI_INDEX_URL)
    if not index:
        LOGGER.error("Could not load the Magic UI registry index")
        return
    items = [item for item in index.get("items", []) if isinstance(item, dict)]
    if max_components is not None:
        items = items[:max_components]

    progress = tqdm(items, desc="magicui components", unit="component")
    for item in progress:
        component_id = candidate_id(item)
        if not component_id:
            LOGGER.warning("Skipping Magic UI registry item without an id: %r", item)
            continue
        path = component_path(data_root, "magicui", component_id)
        if path.exists():
            progress.set_postfix_str("already saved")
            continue
        try:
            payload = get_registry_json(MAGICUI_ITEM_URL.format(name=component_id))
            if not payload:
                continue
            save_record(path, build_magicui_record(item, payload))
        except Exception:
            LOGGER.exception("Saving Magic UI component %s failed", component_id)
        finally:
            time.sleep(HTTP_DELAY_SECONDS)


def aceternity_tags(item: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    tags = ["aceternity"]
    tags.extend(string_list(item.get("tags")))
    tags.extend(string_list(payload.get("tags")))
    for registry_type in (item.get("type"), payload.get("type"), item.get("category")):
        if isinstance(registry_type, str) and registry_type.strip():
            tags.append(registry_type.strip())
    return sorted(set(tags))


def build_aceternity_record(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    component_id = candidate_id(payload) or candidate_id(item) or "unknown"
    name = payload.get("title") or payload.get("name") or item.get("title") or item.get("name") or component_id
    description = payload.get("description") or item.get("description") or ""
    return {
        "id": component_id,
        "name": name,
        "source_code": first_registry_file_content(payload),
        "description": description,
        "tags": aceternity_tags(item, payload),
        "server": "aceternity",
        "metadata": {
            "listed_component": to_jsonable(item),
            "registry_item": to_jsonable(payload),
        },
    }


def registry_file_contents(payload: dict[str, Any], file_base_url: str | None = None) -> str:
    files = payload.get("files")
    if not isinstance(files, list):
        return first_registry_file_content(payload)

    chunks: list[str] = []
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        path = file_item.get("path") or file_item.get("name") or file_item.get("target")
        content = file_item.get("content")
        if not isinstance(content, str) or not content.strip():
            if isinstance(file_base_url, str) and isinstance(path, str) and path.strip():
                content = get_url_text(file_base_url.rstrip("/") + "/" + path.lstrip("/"))
                if content:
                    file_item["content"] = content
        if not isinstance(content, str) or not content.strip():
            continue
        if isinstance(path, str) and path.strip():
            chunks.append(f"// {path.strip()}\n{content}")
        else:
            chunks.append(content)
    return "\n\n".join(chunks)


def title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", slug) if part)


def build_external_record(source: str, item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    component_id = candidate_id(payload) or candidate_id(item) or "unknown"
    name = (
        payload.get("title")
        or payload.get("name")
        or item.get("title")
        or item.get("name")
        or title_from_slug(component_id)
    )
    description = payload.get("description") or item.get("description") or ""
    tags = [source]
    tags.extend(string_list(item.get("tags")))
    tags.extend(string_list(payload.get("tags")))
    for registry_type in (item.get("type"), payload.get("type"), item.get("category"), payload.get("category")):
        if isinstance(registry_type, str) and registry_type.strip():
            tags.append(registry_type.strip())
    return {
        "id": component_id,
        "name": name,
        "source_code": registry_file_contents(
            payload,
            ADDITIONAL_SOURCES.get(source, {}).get("file_base_url"),
        ),
        "description": description,
        "tags": sorted(set(tags)),
        "server": source,
        "metadata": {
            "listed_component": to_jsonable(item),
            "registry_item": to_jsonable(payload),
        },
    }


def registry_items_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "components", "registry", "data"):
        value = index.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [
                {"name": name, **item} if isinstance(item, dict) else {"name": name}
                for name, item in value.items()
            ]
    if candidate_id(index) and "files" in index:
        return [index]
    return []


def get_url_json_any(url: str) -> Any:
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "VibePull-ingest"},
        )
        if response.status_code == 404:
            LOGGER.info("Registry URL not found: %s", url)
            return None
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        LOGGER.warning("Registry request failed for %s: %s", url, error)
    except ValueError as error:
        LOGGER.warning("Registry response was not valid JSON for %s: %s", url, error)
    return None


def get_url_text(url: str) -> str | None:
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "VibePull-ingest"},
        )
        if response.status_code == 404:
            LOGGER.info("Text URL not found: %s", url)
            return None
        response.raise_for_status()
        return response.text
    except requests.RequestException as error:
        LOGGER.warning("Text request failed for %s: %s", url, error)
    return None


def source_file_id(entry: dict[str, Any]) -> str:
    path = str(entry.get("path") or entry.get("name") or "component")
    without_extension = re.sub(r"\.[^.]+$", "", path)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", without_extension).strip("._") or "component"


def payload_from_source_file(entry: dict[str, Any], content: str) -> dict[str, Any]:
    name = str(entry.get("name") or "component")
    stem = re.sub(r"\.(tsx|ts|jsx|js|json|mdx?|astro|html?)$", "", name, flags=re.IGNORECASE)
    path = str(entry.get("path") or name)
    if stem.lower() == "index":
        stem = Path(path).parent.name or stem
    return {
        "id": source_file_id(entry),
        "name": stem,
        "type": "registry:ui",
        "files": [
            {
                "path": path,
                "content": content,
            }
        ],
    }


def registry_payload_from_download(
    entry: dict[str, Any],
    include_extensions: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    download_url = entry.get("download_url")
    name = str(entry.get("name") or "")
    path = str(entry.get("path") or name)
    if not isinstance(download_url, str) or not download_url:
        return None
    lower_path = path.lower().replace("\\", "/")
    if any(pattern.lower() in lower_path for pattern in exclude_patterns):
        return None

    if name.lower().endswith(".json"):
        if include_extensions is not None and ".json" not in include_extensions:
            return None
        payload = get_registry_json(download_url)
        if not payload:
            return None
        item = {"name": name.removesuffix(".json"), "source": "github_contents", **entry}
        return item, payload

    source_extensions = (".tsx", ".ts", ".jsx", ".js", ".mdx", ".astro", ".html")
    allowed_extensions = include_extensions or source_extensions
    if name.lower().endswith(allowed_extensions):
        content = get_url_text(download_url)
        if not content:
            return None
        item = {"id": source_file_id(entry), "name": re.sub(r"\.[^.]+$", "", name), "source": "github_contents", **entry}
        return item, payload_from_source_file(entry, content)

    return None


def github_contents_items(
    url: str,
    max_components: int | None = None,
    include_extensions: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] = (),
    recursive: bool = True,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    contents = get_url_json_any(url)
    if not isinstance(contents, list):
        return []

    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    queue = [entry for entry in contents if isinstance(entry, dict)]
    while queue:
        entry = queue.pop(0)
        entry_type = entry.get("type")
        if entry_type == "dir":
            if not recursive:
                continue
            nested_url = entry.get("url")
            if isinstance(nested_url, str):
                nested = get_url_json_any(nested_url)
                if isinstance(nested, list):
                    queue.extend(item for item in nested if isinstance(item, dict))
            continue
        if entry_type != "file":
            continue
        pair = registry_payload_from_download(
            entry,
            include_extensions=include_extensions,
            exclude_patterns=exclude_patterns,
        )
        if pair:
            items.append(pair)
            if max_components is not None and len(items) >= max_components:
                break
    return items


def zip_contents_items(
    zip_url: str,
    zip_prefix: str,
    max_components: int | None = None,
    include_extensions: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] = (),
    recursive: bool = True,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    try:
        response = requests.get(
            zip_url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "VibePull-ingest"},
        )
        response.raise_for_status()
    except requests.RequestException as error:
        LOGGER.warning("ZIP source request failed for %s: %s", zip_url, error)
        return []

    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    prefix = zip_prefix.strip("/")
    allowed_extensions = include_extensions or (".json", ".tsx", ".ts", ".jsx", ".js", ".mdx", ".astro", ".html")
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.split("/", 1)
                if len(parts) != 2:
                    continue
                relative_path = parts[1]
                relative_lower = relative_path.lower().replace("\\", "/")
                if not relative_lower.startswith(prefix.lower() + "/"):
                    continue
                local_path = relative_path[len(prefix) + 1 :]
                if not recursive and "/" in local_path:
                    continue
                if any(pattern.lower() in relative_lower for pattern in exclude_patterns):
                    continue
                if not relative_lower.endswith(allowed_extensions):
                    continue

                content = archive.read(info).decode("utf-8", errors="replace")
                entry = {
                    "id": source_file_id({"path": local_path, "name": Path(local_path).name}),
                    "name": Path(local_path).name,
                    "path": relative_path,
                    "source": "zip_archive",
                }
                if relative_lower.endswith(".json"):
                    try:
                        payload = json.loads(content)
                    except ValueError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    item = {**entry, "name": Path(local_path).stem}
                    items.append((item, payload))
                else:
                    item = {**entry, "name": Path(local_path).stem}
                    items.append((item, payload_from_source_file(entry, content)))
                if max_components is not None and len(items) >= max_components:
                    break
    except zipfile.BadZipFile as error:
        LOGGER.warning("ZIP source was invalid for %s: %s", zip_url, error)
        return []
    return items


def index_registry_pairs(
    source: str,
    index: dict[str, Any],
    max_components: int | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    items = registry_items_from_index(index)
    if max_components is not None:
        items = items[:max_components]
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in items:
        if "files" in item:
            pairs.append((item, item))
            continue
        component_id = candidate_id(item)
        if not component_id:
            continue
        registry_url = item.get("registry_url") or item.get("url")
        payload = None
        if isinstance(registry_url, str) and registry_url.endswith(".json"):
            payload = get_registry_json(registry_url)
        if payload is None:
            payload = item
        pairs.append((item, payload))
    LOGGER.info("%s primary registry produced %s component candidates", source, len(pairs))
    return pairs


def aceternity_items_from_github_contents() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    contents = get_url_json_any(ACETERNITY_GITHUB_CONTENTS_URL)
    if not isinstance(contents, list):
        return []

    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        download_url = entry.get("download_url")
        name = str(entry.get("name") or "").removesuffix(".json")
        if not isinstance(download_url, str) or not download_url:
            continue
        payload = get_registry_json(download_url)
        if payload:
            items.append(({"name": name, "source": "github_contents", **entry}, payload))
            time.sleep(HTTP_DELAY_SECONDS)
    return items


def aceternity_slugs_from_ai_catalog() -> list[str]:
    try:
        response = requests.get(
            ACETERNITY_AI_CATALOG_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "VibePull-ingest"},
        )
        response.raise_for_status()
    except requests.RequestException as error:
        LOGGER.warning("Could not load Aceternity AI catalog: %s", error)
        return []

    slugs: set[str] = set()
    for match in ACETERNITY_SLUG.finditer(response.text):
        slug = match.group(1) or match.group(2)
        if slug:
            slugs.add(slug)
    return sorted(slugs)


def aceternity_items_from_component_registry(max_components: int | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    slugs = aceternity_slugs_from_ai_catalog()
    if max_components is not None:
        slugs = slugs[:max_components]

    for slug in tqdm(slugs, desc="aceternity registry", unit="component"):
        payload = get_registry_json(ACETERNITY_COMPONENT_REGISTRY_URL.format(name=slug))
        if not payload:
            continue
        items.append(({"name": slug, "source": "ai_catalog"}, payload))
        time.sleep(HTTP_DELAY_SECONDS)
    return items


def aceternity_registry_items(max_components: int | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    index = get_registry_json(ACETERNITY_INDEX_URL)
    if index:
        items = registry_items_from_index(index)
        if max_components is not None:
            items = items[:max_components]
        fetched: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in items:
            component_id = candidate_id(item)
            if not component_id:
                continue
            if "files" in item:
                fetched.append((item, item))
                continue
            payload = get_registry_json(ACETERNITY_COMPONENT_REGISTRY_URL.format(name=component_id))
            if payload:
                fetched.append((item, payload))
                time.sleep(HTTP_DELAY_SECONDS)
        if fetched:
            return fetched

    github_items = aceternity_items_from_github_contents()
    if github_items:
        return github_items[:max_components] if max_components is not None else github_items

    LOGGER.warning(
        "Aceternity registry and GitHub contents endpoints were unavailable; "
        "falling back to public component registry URLs discovered from the AI catalog."
    )
    return aceternity_items_from_component_registry(max_components)


def ingest_aceternity(data_root: Path, max_components: int | None) -> None:
    items = aceternity_registry_items(max_components)
    if not items:
        LOGGER.error("Could not load any Aceternity registry components")
        return

    saved = 0
    skipped = 0
    for item, payload in tqdm(items, desc="aceternity components", unit="component"):
        component_id = candidate_id(payload) or candidate_id(item)
        if not component_id:
            LOGGER.warning("Skipping Aceternity item without an id: %r", item)
            continue
        path = component_path(data_root, "aceternity", component_id)
        if path.exists():
            skipped += 1
            continue
        save_record(path, build_aceternity_record(item, payload))
        saved += 1
    print(f"aceternity ingest complete: {saved} saved, {skipped} already existed")


def external_source_pairs(source: str, max_components: int | None) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], str]:
    config = ADDITIONAL_SOURCES[source]
    primary = config.get("primary")
    if isinstance(primary, str):
        index = get_registry_json(primary)
        if index:
            return index_registry_pairs(source, index, max_components), "primary"
        LOGGER.warning("%s primary source unavailable: %s", source, primary)

    fallback = config.get("fallback")
    if isinstance(fallback, str):
        pairs = github_contents_items(
            fallback,
            max_components=max_components,
            include_extensions=config.get("include_extensions"),
            exclude_patterns=tuple(config.get("exclude_patterns", ())),
            recursive=bool(config.get("recursive", True)),
        )
        if pairs:
            return pairs, "github_contents_fallback"
        LOGGER.warning("%s fallback source unavailable or empty: %s", source, fallback)

    zip_url = config.get("zip_url")
    zip_prefix = config.get("zip_prefix")
    if isinstance(zip_url, str) and isinstance(zip_prefix, str):
        pairs = zip_contents_items(
            zip_url,
            zip_prefix,
            max_components=max_components,
            include_extensions=config.get("include_extensions"),
            exclude_patterns=tuple(config.get("exclude_patterns", ())),
            recursive=bool(config.get("recursive", True)),
        )
        if pairs:
            return pairs, "zip_fallback"
        LOGGER.warning("%s ZIP source unavailable or empty: %s", source, zip_url)

    return [], "none"


def ingest_external_source(source: str, data_root: Path, max_components: int | None) -> None:
    pairs, method = external_source_pairs(source, max_components)
    if not pairs:
        print(f"{source} ingest complete: 0 saved, 0 already existed, source unavailable")
        return

    saved = 0
    skipped = 0
    for item, payload in tqdm(pairs, desc=f"{source} components", unit="component"):
        component_id = candidate_id(payload) or candidate_id(item)
        if not component_id:
            LOGGER.warning("Skipping %s item without an id: %r", source, item)
            continue
        path = component_path(data_root, source, component_id)
        if path.exists():
            skipped += 1
            continue
        record = build_external_record(source, item, payload)
        record.setdefault("metadata", {})["ingest_method"] = method
        save_record(path, record)
        saved += 1
    print(f"{source} ingest complete: {saved} saved, {skipped} already existed, method={method}")


async def ingest_server(
    server: Server, data_root: Path, registry: str, max_components: int | None
) -> None:
    LOGGER.info("Connecting to %s through %s %s", server.name, server.parameters.command, server.parameters.args)
    try:
        async with stdio_client(server.parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tools = list(getattr(tools_result, "tools", []))
                print(f"\n{server.name} tools:")
                for available_tool in tools:
                    print(f"- {tool_name(available_tool)}")

                candidates = await discover_components(session, server.name, tools, registry)
                if not candidates:
                    LOGGER.warning("%s exposes no safely enumerable component list", server.name)
                    return
                if max_components is not None:
                    candidates = candidates[:max_components]

                fetch_tools = fetch_tool_candidates(tools)
                if not fetch_tools:
                    LOGGER.warning("%s has components but no matching fetch/get/view tool", server.name)
                    return

                progress = tqdm(candidates, desc=f"{server.name} components", unit="component")
                for candidate in progress:
                    component_id = candidate_id(candidate)
                    if not component_id:
                        LOGGER.warning("Skipping %s candidate without an id: %r", server.name, candidate)
                        continue
                    path = component_path(data_root, server.name, component_id)
                    if path.exists():
                        progress.set_postfix_str("already saved")
                        continue
                    result, fetch_tool = await fetch_component(
                        session, component_id, registry, fetch_tools
                    )
                    if result is None:
                        LOGGER.error("Skipping %s.%s after fetch failures", server.name, component_id)
                        continue
                    try:
                        record = build_record(server.name, candidate, result, fetch_tool)
                        save_record(path, record)
                        if server.name == "shadcn":
                            shadcn_registry_source_fallback(path, record)
                    except Exception:
                        LOGGER.exception("Saving %s failed", path)
    except Exception:
        LOGGER.exception("Could not ingest from %s", server.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="Directory for server/component JSON payloads.",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_SHADCN_REGISTRY,
        help="Registry name used for registry-aware MCP tools.",
    )
    parser.add_argument(
        "--server",
        choices=("all", "shadcn", "magicui", "aceternity", *EXTERNAL_SOURCE_NAMES),
        default="all",
        help="Limit ingestion to one source.",
    )
    parser.add_argument(
        "--max-components",
        type=int,
        default=None,
        help="Stop after this many listed components; useful for smoke tests.",
    )
    parser.add_argument(
        "--refetch-source",
        action="store_true",
        help="Update saved shadcn JSONs with missing source_code from registry fallbacks.",
    )
    parser.add_argument(
        "--fix-shadcn",
        action="store_true",
        help="Update saved shadcn JSONs with missing source_code from style registry URLs.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable info logging.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.fix_shadcn:
        fix_shadcn_sources(args.data_root)
        return
    if args.refetch_source:
        refetch_shadcn_sources(args.data_root)
        return
    servers = [server for server in build_servers() if args.server in ("all", server.name)]
    for server in servers:
        await ingest_server(server, args.data_root, args.registry, args.max_components)
    if args.server in ("all", "magicui"):
        ingest_magicui(args.data_root, args.max_components)
    if args.server in ("all", "aceternity"):
        ingest_aceternity(args.data_root, args.max_components)
    for source in EXTERNAL_SOURCE_NAMES:
        if args.server in ("all", source):
            ingest_external_source(source, args.data_root, args.max_components)


if __name__ == "__main__":
    asyncio.run(main())
