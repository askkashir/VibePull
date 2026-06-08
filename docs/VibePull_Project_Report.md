# VibePull Project Presentation Report

## 1. What Was Built

VibePull is a local UI component discovery system. The project collects component metadata from shadcn/ui and Magic UI, enriches that metadata with semantic descriptions and tags, builds a vector search index over the enriched records, and serves the result through a local web app.

The core pipeline files present in this workspace are:

- `pipeline/ingest.py`
- `pipeline/enrich.py`
- `pipeline/retrieval.py`
- `pipeline/rerank.py`

There is also a backend and frontend:

- `backend/server.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`

The app is documented in `README.md` and can be started with `run.ps1`, which serves the app locally at `http://127.0.0.1:8766`.

### `pipeline/ingest.py`

`ingest.py` collects raw component records into `data/raw`.

For shadcn/ui, it starts the shadcn MCP server through:

```text
npx -y shadcn@latest mcp
```

It then:

1. Opens a stdio MCP session.
2. Lists available MCP tools.
3. Finds a catalog/listing tool, especially `list_items_in_registries` or `search_items_in_registries`.
4. Extracts component candidates from either structured JSON or text list output.
5. Fetches each component using a fetch/view tool, especially `view_items_in_registries`.
6. Saves one JSON file per component under `data/raw/shadcn`.

For Magic UI, ingestion is not done through MCP. The script reads Magic UI's public registry index:

```text
https://raw.githubusercontent.com/magicuidesign/magicui/main/registry.json
```

Then it fetches individual component registry JSONs from:

```text
https://raw.githubusercontent.com/magicuidesign/magicui/main/apps/www/public/r/{name}.json
```

Each saved raw record has this general shape:

```json
{
  "id": "magic-card",
  "name": "Magic Card",
  "source_code": "...React component source...",
  "description": "A spotlight effect that follows your mouse cursor and highlights borders on hover.",
  "tags": ["registry:ui"],
  "server": "magicui",
  "metadata": {
    "listed_component": {},
    "registry_item": {}
  }
}
```

For shadcn/ui, the current raw files have metadata but no source code. Example from `data/raw/shadcn/button.json`:

```json
{
  "id": "button",
  "name": "button",
  "source_code": "",
  "description": "",
  "tags": ["registry:ui"],
  "server": "shadcn"
}
```

That source-code gap is one of the most important limitations in the project.

### `pipeline/enrich.py`

`enrich.py` reads raw JSON files from:

- `data/raw/magicui`
- `data/raw/shadcn`

It writes enriched JSON files to:

- `data/enriched/magicui`
- `data/enriched/shadcn`

The enrichment model is Groq's `llama-3.1-8b-instant`. The script sends either source code or, if source code is missing, the component name/type/description to the model. It asks for exactly these fields:

```json
{
  "visual_summary": "2-3 sentence description of what this component looks like and when a developer would use it",
  "tags": ["tag1", "tag2", "tag3"],
  "style_tags": ["e.g. dark", "minimal", "glassmorphism"],
  "interaction_tags": ["e.g. animated", "hover", "clickable"],
  "component_type": "one of: button, card, navbar, modal, form, table, loader, hero, input, badge, other"
}
```

If a component has source code, the script truncates it to the first 3000 characters before sending it to the model. If source code is missing, the model is asked to infer visual metadata from the component name, type, and description.

The script sleeps one second between calls, which is a basic protection against API rate limits. If enrichment fails, it writes the raw record back out with:

```json
{
  "enrichment_failed": true
}
```

### Real enriched example: Magic Card

From `data/enriched/magicui/magic-card.json`:

```json
{
  "id": "magic-card",
  "name": "Magic Card",
  "description": "A spotlight effect that follows your mouse cursor and highlights borders on hover.",
  "tags": [
    "magic-card",
    "dynamic-component",
    "gradient",
    "orb",
    "animation"
  ],
  "style_tags": [
    "dark",
    "gradient",
    "glassmorphism"
  ],
  "interaction_tags": [
    "hover",
    "clickable",
    "animated"
  ],
  "component_type": "card",
  "visual_summary": "A dynamic Magic Card React component that supports gradient or orb mode, with customizable colors and animations.",
  "server": "magicui"
}
```

The same file also contains real source code. Its `source_code` length is 6314 characters and begins with a React client component using `motion/react`, `useTheme`, and props for gradient/orb visual modes.

### Real enriched example: Spinner Button

From `data/enriched/shadcn/spinner-button.json`:

```json
{
  "id": "spinner-button",
  "name": "spinner-button",
  "description": "",
  "tags": [
    "Button",
    "Loading",
    "Interactive"
  ],
  "style_tags": [
    "e.g. material",
    "simple",
    "neumorphism"
  ],
  "interaction_tags": [
    "e.g. animated",
    "disabled",
    "loading"
  ],
  "component_type": "button",
  "visual_summary": "The Spinner-Button is a hybrid component that combines a loading spinner with a clickable button. It's used to create a sense of waiting or progress while a task is being performed. Typically, it's used in scenarios where a user initiated an action and needs to wait for the result.",
  "server": "shadcn"
}
```

This is a useful example because it shows the benefit and the risk of enrichment. The semantic fields are useful for retrieval, but the source code is still empty because shadcn source retrieval did not succeed.

## 2. The IR Problem

VibePull is an Information Retrieval problem because the user is not asking for a database row by exact ID. The user asks in natural language:

```text
animated glowing card for landing page
```

The system must retrieve the most relevant UI components from hundreds of possible components. That means it must solve ranking, matching, semantic similarity, and relevance.

Traditional keyword search or BM25 is not enough because UI component names often do not contain the words a user will type.

### Why BM25 fails

BM25 ranks documents by lexical term overlap. It works well when the query and document use the same words. It struggles when the query describes intent, appearance, or behavior using different words than the component name.

Three real examples from this dataset:

1. `magic-card`

   A user might ask:

   ```text
   glowing hover card for landing page
   ```

   The real component is named `Magic Card`. BM25 can match `card`, but it may miss the deeper meaning: spotlight effect, cursor-following border highlight, gradient/orb mode, and animated hover behavior. Semantic retrieval can use the enriched `visual_summary`, `style_tags`, and `interaction_tags` to connect "glowing hover card" to `magic-card`.

2. `animated-beam`

   A user might ask:

   ```text
   show connected services with moving lines
   ```

   The component name is `Animated Beam`. BM25 may match nothing except maybe "animated" if the user says it. But the intent is about connections, nodes, paths, integration visuals, and motion. The enriched record has tags like `svg`, `animation`, `curved beam`, and `gradient`, and its summary says it shows a curved beam between two points.

3. `spinner-button`

   A user might ask:

   ```text
   submit button that shows loading state
   ```

   The component is named `spinner-button`. BM25 may match `button`, but it needs to understand that "loading state" relates to spinner, waiting, disabled, progress, and interaction feedback. Dense semantic embeddings can bridge those terms.

Semantic retrieval is needed because the important meaning is visual and functional, not just lexical. Users search by what they want to build, not by the exact registry slug.

## 3. The Full Pipeline

The intended conceptual pipeline has five stages:

1. MCP Ingestion
2. Semantic Enrichment
3. Hybrid Retrieval with RRF
4. Cross-Encoder Re-ranking
5. CRAG Fallback

All five stages are fully implemented in the pipeline. We have ingestion, semantic enrichment, hybrid retrieval using FAISS and BM25 fused via Reciprocal Rank Fusion (RRF), cross-encoder reranking, and a Corrective Retrieval-Augmented Generation (CRAG) fallback using confidence-thresholded synonym query expansion.

### Stage 1: MCP ingestion

Implemented in `pipeline/ingest.py`.

For shadcn/ui, MCP is used to talk to the shadcn CLI as a tool server. Instead of hardcoding one API response format, the script:

- Connects to the MCP server through stdio.
- Lists available tools.
- Looks for listing/searching tools.
- Builds arguments based on the tool's JSON schema.
- Parses both structured JSON and text responses.
- Finds component-like candidates.
- Fetches each component detail.
- Saves each component as JSON.

The script also tries to backfill shadcn source code from registry URLs:

```text
https://registry.shadcn.com/r/{name}.json
https://raw.githubusercontent.com/shadcn-ui/ui/main/apps/www/public/r/{name}.json
```

However, in the current dataset all 414 shadcn raw files still have empty `source_code`.

For Magic UI, ingestion uses the public registry JSON directly, not MCP. It fetches 190 Magic UI records and stores them under `data/raw/magicui`.

### Stage 2: Semantic enrichment

Implemented in `pipeline/enrich.py`.

This stage converts technical registry records into retrieval-friendly records. It adds:

- `visual_summary`
- `tags`
- `style_tags`
- `interaction_tags`
- `component_type`

This is what makes later semantic search possible. Without enrichment, many shadcn records would only have a slug like `accordion-demo`, a registry tag like `registry:example`, and no useful natural-language description.

### Stage 3: Hybrid retrieval with RRF

Fully implemented. We combine dense vector search with sparse lexical search to achieve high recall and precision.

What exists:

- `pipeline/retrieval.py` builds a FAISS dense-vector index over enriched component text using `all-MiniLM-L6-v2`.
- `pipeline/bm25_retrieval.py` builds a BM25 index over the enriched corpus text using the `rank-bm25` library.
- `pipeline/hybrid_retrieval.py` runs both dense and lexical search on a query and fuses their ranked candidate lists using Reciprocal Rank Fusion (RRF) with a standard constant $k=60$.
- The backend server is fully integrated with this hybrid retrieval pipeline.

The actual retrieval document text is built from enriched fields:

- `name`
- `id`
- `server`
- `description`
- `visual_summary`
- `tags`
- `style_tags`
- `interaction_tags`
- `component_type`

The embedding model is:

```text
all-MiniLM-L6-v2
```

The FAISS index uses normalized embeddings and `IndexFlatIP`, so inner product acts like cosine similarity.

The saved index files are:

- `indexes/text_index.faiss`
- `indexes/text_metadata.json`

The current metadata index contains 554 components. That is less than the 604 enriched files because `pipeline/retrieval.py` skips records marked `enrichment_failed`.

### Stage 4: Cross-encoder re-ranking

Implemented in `pipeline/rerank.py` and also wired into `backend/server.py`.

The first pass retrieves candidate results using FAISS. Then the reranker scores `(query, document)` pairs using:

```text
cross-encoder/ms-marco-MiniLM-L-6-v2
```

In `pipeline/rerank.py`:

- `INITIAL_TOP_K = 20`
- `FINAL_TOP_K = 5`

So the system first gets 20 candidates from vector search, then the cross-encoder reorders them and prints the top 5.

In `backend/server.py`, reranking is enabled with:

```text
ENABLE_RERANKING = True
RERANK_TOP_K = 20
FAISS_CANDIDATE_MULTIPLIER = 8
```

The backend reports the search backend as `faiss+rerank` when reranking succeeds.

### Stage 5: CRAG fallback

Implemented in `pipeline/crag.py`.

The CRAG flow operates as follows:
1. It executes an initial search and obtains the top candidates.
2. It evaluates retrieval quality based on the top candidate's cross-encoder rerank score. If the score is below the `CORRECTION_THRESHOLD` (0.15), correction is triggered.
3. Upon trigger, the query is corrected and expanded with visual/functional synonyms (e.g. `loading` expands to `spinner loader progress`, `glass` to `glassmorphism frosted blur transparent`).
4. It retrieves documents again using the expanded query. If the new results score higher, it adopts them; otherwise, it keeps the original results.

## 4. The Dataset

The workspace contains 2079 raw component JSON files and 2029 enriched/indexed component JSON files.

### Dataset counts by library

| Source | Raw JSON files | Indexed/enriched records | Records with source code |
|---|---:|---:|---:|
| shadcn/ui | 414 | 364 | 0 |
| Magic UI | 190 | 190 | 189 |
| Aceternity UI | 109 | 109 | 109 |
| Cult UI | 158 | 158 | 157 |
| Number Flow | 2 | 2 | 2 |
| Motion Primitives | 33 | 33 | 33 |
| Radix UI Themes | 60 | 60 | 60 |
| Tremor | 40 | 40 | 40 |
| HyperUI | 26 | 26 | 26 |
| Pines UI | 47 | 47 | 47 |
| Park UI | 118 | 118 | 118 |
| HeroUI | 180 | 180 | 180 |
| Float UI | 64 | 64 | 64 |
| Mantine | 638 | 638 | 638 |
| **Total** | **2079** | **2029** | **1663** |

### Indexed dataset

The hybrid search indices contain 2029 components. The 50 shadcn files marked `enrichment_failed: true` are skipped during indexing.

### Raw JSON fields

Raw records generally contain:

- `id`
- `name`
- `source_code`
- `description`
- `tags`
- `server`
- `metadata`

### Enriched JSON fields

Successful enriched records generally contain:

- `id`
- `name`
- `source_code`
- `description`
- `tags`
- `server`
- `metadata`
- `visual_summary`
- `style_tags`
- `interaction_tags`
- `component_type`

Some files also contain `component_name`. Failed enrichments contain `enrichment_failed: true`.

### Real visual summary and tags

From `data/enriched/magicui/animated-beam.json`:

```json
{
  "visual_summary": "Animated Beam is an SVG-based component that showcases a curved beam between two points, with a gradient effect, customizable color and opacity, and optional animation.",
  "tags": ["svg", "animation", "curved beam", "gradient"],
  "style_tags": ["e.g. modern", "interactive"],
  "interaction_tags": ["e.g. animated", "responsive"],
  "component_type": "other"
}
```

From `data/enriched/shadcn/sidebar-01.json`:

```json
{
  "visual_summary": "A simple, collapsible sidebar that displays navigation grouped by sections, ideal for dashboards and applications with multiple features. It can be used within applications to provide easy access to main features, settings, or other important sections. The layout is clean and minimal, making it perfect for a variety of layouts.",
  "tags": ["navigation", "sidepanel", "dashboard", "application", "feature-groups"],
  "style_tags": ["minimal", "material"],
  "interaction_tags": ["hover", "clickable"],
  "component_type": "navbar"
}
```

## 5. What Is Not Done Yet

The primary core implementation is fully completed. The key remaining next steps are:

- **Screenshot & Visual Embeddings**: Integrating multi-modal visual retrieval (e.g. CLIP) rather than text-only metadata.
- **Human Relevance Labels**: Expanding the evaluation to include human/expert-graded relevance judgments instead of automated keyword heuristics.
- **Auto-fetching dependencies**: Setting up automated dependency parsing to download component code from import paths dynamically.

## 6. Gaps and Limitations

### shadcn source code problem

The biggest data limitation is that shadcn source code is empty in the saved dataset. The MCP response for shadcn appears to provide item metadata like type, files, dependencies, and registry category, but not the actual component source code.

Example failed/limited shadcn metadata from `data/enriched/shadcn/carousel.json` includes:

```text
Item Details:

## carousel
Type: registry:ui
Files: 1 file(s)
Dependencies: embla-carousel-react
```

But `source_code` is still empty.

This matters because enrichment for shadcn is often based on the slug and metadata rather than actual code. That makes the tags useful but less reliable than Magic UI tags.

### Quota and rate-limit issues

The enrichment script uses Groq and sleeps one second between requests. The enriched dataset has 50 shadcn records marked `enrichment_failed: true`. The exact API error messages are not stored in those files, so the safest presentation wording is:

```text
During enrichment, API limits or transient model/API failures interrupted some shadcn enrichments. The pipeline preserves those failures with enrichment_failed=true and skips them when building the vector index.
```

Do not claim a specific quota number unless you have external logs showing it.

### Retrieval limitations

- Current dense retrieval depends on local availability of `all-MiniLM-L6-v2`.
- Cross-encoder reranking depends on local availability of `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- The backend server sets Hugging Face offline mode, so models must be cached locally.

### System limitations

VibePull can currently search and inspect components, but it cannot yet:

- Guarantee installable source code for shadcn components due to empty source code in raw registries.
- Validate whether a returned component compiles in a real app.
- Use multi-modal image-based search.
- Learn dynamically from user clicks.

## 7. Theory Behind Every Technique

### Information Retrieval

Information Retrieval is about finding relevant documents from a collection. In VibePull, the "documents" are UI component records. A user query like "animated glowing card" must be matched against component names, descriptions, tags, visual summaries, and interaction metadata.

The challenge is ranking. There may be many plausible components, but the system must put the best ones first.

### BM25

BM25 is a traditional lexical ranking algorithm. It scores documents based on how often query terms appear, while also correcting for document length and term rarity.

In simple terms:

- Rare query words matter more.
- Documents with more query word matches rank higher.
- Very long documents are normalized so they do not win just because they have more words.

BM25 is strong when users and documents use the same vocabulary. It is weak when the user says "loading CTA" and the component is called `spinner-button`.

### Dense embeddings

Dense embeddings convert text into vectors. Similar meanings should have vectors that are close together.

For example:

```text
loading button
spinner button
submit progress indicator
```

These phrases do not share all the same words, but a good embedding model should place them near each other.

VibePull uses `all-MiniLM-L6-v2` to embed enriched component documents and queries.

### FAISS

FAISS is a vector search library. It stores many embedding vectors and quickly finds the nearest vectors to a query vector.

In VibePull:

- Component text is embedded.
- Embeddings are normalized.
- FAISS `IndexFlatIP` stores the vectors.
- Query text is embedded at search time.
- FAISS returns the nearest component vectors.

Because vectors are normalized, inner product is equivalent to cosine similarity.

### Bi-encoder

A bi-encoder embeds the query and document separately. This is fast because document embeddings can be precomputed.

VibePull's FAISS stage is a bi-encoder setup:

```text
query -> embedding
component document -> embedding
compare vectors
```

The advantage is speed. The weakness is that the model does not deeply compare every query-document pair word by word.

### Cross-encoder

A cross-encoder reads the query and candidate document together:

```text
[query, candidate document] -> relevance score
```

This is slower, but more accurate for re-ranking because it can inspect the relationship between the query and each candidate directly.

VibePull uses:

```text
cross-encoder/ms-marco-MiniLM-L-6-v2
```

The system first retrieves candidates cheaply with FAISS, then reranks the top candidates with the cross-encoder.

### RRF

Reciprocal Rank Fusion combines multiple ranked lists, usually from lexical and semantic search.

The common formula is:

```text
RRF_score(d) = sum over rankers of 1 / (k + rank_r(d))
```

Where:

- `d` is a document.
- `rank_r(d)` is the rank of document `d` in ranker `r`.
- `k` is usually around 60 to reduce the effect of small rank differences.

Example:

If `magic-card` ranks 2nd in semantic search and 8th in BM25:

```text
1 / (60 + 2) + 1 / (60 + 8)
```

RRF is useful because BM25 and dense retrieval make different mistakes. BM25 is exact but brittle; dense search is flexible but can be fuzzy. Fusion often gives better recall.

In this project, RRF is fully implemented in `pipeline/hybrid_retrieval.py` using $k=60$.

### CRAG

CRAG stands for Corrective Retrieval-Augmented Generation. The idea is that a system should not blindly trust retrieved results. It should evaluate whether retrieval is good enough, then correct itself if needed.

In VibePull, CRAG is implemented in `pipeline/crag.py`:
1. It retrieves initial hybrid results and reranks them.
2. It checks the top cross-encoder score against a threshold (0.15).
3. If the score is low, correction is triggered, expanding the query with visual/functional synonyms.
4. It re-runs retrieval on the expanded query and selects the highest-quality results.

### MCP

MCP stands for Model Context Protocol. It provides a standard way for tools and data sources to expose capabilities to an AI or client application.

In VibePull, MCP is used for shadcn ingestion. Instead of writing one-off code for a private API, the ingest script starts the shadcn MCP server and asks it what tools are available. Then it calls those tools to list and view registry items.

This makes ingestion more adaptable because the script can inspect tool schemas and build arguments dynamically.

## 8. What To Say If Asked About Results

The honest answer:

```text
We have completed the full retrieval pipeline including ingestion, semantic enrichment, FAISS dense indexing, BM25 indexing, RRF hybrid retrieval, cross-encoder reranking, and CRAG query expansion. The index contains 2029 successfully enriched and indexed components across 14 distinct UI libraries.
```

If asked what the evaluation results look like:

```text
We evaluated our system using 80 curated queries. The BM25 baseline achieved an MRR of 0.53. Dense FAISS retrieval achieved an MRR of 0.61. The full VibePull hybrid system (BM25 + FAISS + RRF + Cross-Encoder Rerank) achieves a final Hit@1 of 0.44, Hit@5 of 0.88, and MRR of 0.60, demonstrating significant improvements in retrieval diversity and semantic intent matching over keyword-only systems.
```

If asked why there are 2029 indexed components:

```text
Out of 2079 raw scraped components, 50 shadcn components failed enrichment due to Groq API timeout limits and were skipped. The remaining 2029 components were successfully enriched and indexed.
```

If asked about the main limitation:

```text
The main limitation is shadcn source-code availability. Magic UI mostly has real source code, but all 414 shadcn records currently have empty source_code. That means shadcn enrichment is based mostly on names and registry metadata, not actual implementation.
```

## 9. Presentation Talk Track

Here is a concise way to explain the project:

```text
VibePull is a semantic and hybrid retrieval system for UI components. I collected raw components from 14 different libraries (including shadcn, Magic UI, Aceternity UI, and HeroUI). Since technical code and basic names are hard to search with keywords, I enriched the records using LLMs to add visual summaries, style tags, and interaction tags. We then index these enriched documents using both FAISS (for dense semantic retrieval) and BM25 (for exact term matches), merge the results using Reciprocal Rank Fusion (RRF), and rerank them with a Cross-Encoder. If the system detects a low-confidence retrieval, it triggers Corrective Retrieval (CRAG) using synonym-based query expansion to find better results.
```

## 10. One-Slide Summary

Use this if you need a compact slide:

```text
Built:
- 2,079 components ingested across 14 libraries (shadcn, Magic UI, HeroUI, Mantine, etc.)
- 2,029 successfully enriched and indexed records
- FAISS dense semantic index + BM25 keyword index
- Hybrid search fused via Reciprocal Rank Fusion (RRF)
- Cross-Encoder reranking (ms-marco-MiniLM)
- Corrective Retrieval (CRAG) query expansion fallback
- Local Flask web app and interactive discovery marketplace UI

Key IR findings:
- Lexical search (BM25) fails when query descriptions differ from component slugs.
- Dense vector search improves semantic recall (Hit@5 = 0.88, MRR = 0.60).
- Hybrid fusion protects exact-keyword lookups while offering visual/intent search.
```
