# VibePull - Semantic UI Component Retrieval

VibePull is an information retrieval project for finding UI components from natural language queries. A developer can type something like `dark glassmorphism card` or `data table with sorting`, and VibePull searches a 2,029 component corpus using hybrid retrieval, reranking, and a CRAG-style fallback.

The browser demo is served locally with a Flask API and a self-contained dark component-gallery frontend.

## Project Structure

```text
VibePull/
  backend/      Flask API for the local browser demo
  web/          Single-file frontend plus generated component preview images
  pipeline/     Ingestion, enrichment, FAISS, BM25, hybrid search, reranking, CRAG
  data/         Raw and enriched component corpus JSON files
  indexes/      Built FAISS, BM25, and metadata indexes used by the demo
  eval/         Query set, evaluation scripts, and saved metric results
  tests/        Backend smoke tests
  docs/         Report and presentation/design mockups
```

Keep presentation PDFs or the final report PDF in `docs/` or the root, depending on submission rules. The code folders above are the runnable project.

## Setup

```powershell
pip install -r requirements.txt
```

For enrichment and AI explanations, copy `.env.example` to `pipeline/.env` or `.env` and add your local keys. Real `.env` files are intentionally ignored by Git.

```text
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
```

## Run The Demo

PowerShell:

```powershell
.\run.ps1
```

Windows cmd:

```bat
run.bat
```

Manual:

```powershell
python backend/server.py
```

Then open:

```text
http://localhost:8766
```

## Pipeline

```text
query
  -> FAISS dense retrieval
  -> BM25 lexical retrieval
  -> Reciprocal Rank Fusion
  -> cross-encoder reranking
  -> CRAG query correction if the raw reranker confidence is too low
  -> final ranked components with scores, prompts, and explanations
```

Important files:

- `pipeline/ingest.py`: collects component JSON from UI libraries and source registries.
- `pipeline/enrich.py`: adds summaries, tags, component types, clean IDs, display names, and generation prompts.
- `pipeline/retrieval.py`: builds the FAISS semantic index.
- `pipeline/bm25_retrieval.py`: builds the BM25 lexical index.
- `pipeline/hybrid_retrieval.py`: combines FAISS and BM25 with RRF.
- `pipeline/rerank.py`: reranks candidates with a cross-encoder.
- `pipeline/crag.py`: retries with expanded/corrected queries when confidence is low.
- `backend/server.py`: exposes the full pipeline to the web UI.

## Corpus Stats

| Source | Raw JSON files | Indexed records | Records with source code |
|---|---:|---:|---:|
| Aceternity UI | 109 | 109 | 109 |
| Cult UI | 158 | 158 | 157 |
| Float UI | 64 | 64 | 64 |
| HeroUI | 180 | 180 | 180 |
| HyperUI | 26 | 26 | 26 |
| Magic UI | 190 | 190 | 189 |
| Mantine | 638 | 638 | 638 |
| Motion Primitives | 33 | 33 | 33 |
| Number Flow | 2 | 2 | 2 |
| Park UI | 118 | 118 | 118 |
| Pines UI | 47 | 47 | 47 |
| Radix UI Themes | 60 | 60 | 60 |
| shadcn/ui | 414 | 364 | 0 |
| Tremor | 40 | 40 | 40 |
| **Total** | **2079** | **2029** | **1663** |

All 2,029 indexed records include generation prompts for the demo.

## Evaluation Results

Saved in `eval/results.json`.

| Metric | Score |
|---|---:|
| Hit@1 | 0.7875 |
| Hit@5 | 0.9625 |
| MRR | 0.8556 |
| NDCG@5 | 0.9558 |
| Total queries | 80 |

Baseline comparison from `eval/baseline_results.json`:

| System | Hit@1 | Hit@5 | MRR |
|---|---:|---:|---:|
| BM25 only | 0.6500 | 0.8750 | 0.7490 |
| FAISS only | 0.8000 | 0.9750 | 0.8658 |
| VibePull full | 0.7875 | 0.9625 | 0.8588 |

## Useful Commands

```powershell
python pipeline/retrieval.py
python pipeline/bm25_retrieval.py
python main.py "animated loading spinner"
python eval/metrics.py
python eval/baseline_comparison.py
```

## Notes

- The local browser demo uses Gemini for natural-language result explanations when `GEMINI_API_KEY` is available.
- Enrichment uses Groq when `GROQ_API_KEY` is available.
- The existing indexes are included so the demo can run without rebuilding everything.
- `pipeline/.env` is local only and must not be uploaded.

