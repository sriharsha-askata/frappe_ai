# Lesson 8 - Knowledge and RAG (`knowledge/`)

Files: `extract.py`, `chunker.py`, `embedder.py`, `store.py`, `retriever.py`, `ingest.py`, `attachment_store.py`; DocTypes `AI Knowledge Base`, `AI Knowledge Source`, `AI Knowledge Chunk`; tool `SearchKnowledgeTool` in `assistant_tools/native.py`.

## Why it exists
A model only knows its training data plus what is in the prompt. Company documents cannot all fit in the prompt, so we **retrieve** the few relevant passages per question and insert them: **retrieval-augmented generation (RAG)**.

## Concepts (from first principles)
- **Chunk**: a slice of a document (here 1000 characters, 200 overlap by default). Small enough to be specific, big enough to make sense.
- **Embedding**: a model turns text into a vector (a list of numbers, e.g. 768 floats for `nomic-embed-text`) such that texts with similar meaning have vectors that point in similar directions.
- **Cosine similarity**: the angle between two vectors; small angle = similar meaning. Search = "find the chunk vectors nearest to the question vector".
- **Full-text search (BM25-style keywords)**: exact word matching. **Hybrid search** combines vector and keyword rankings, so rare names/codes (keyword) and paraphrases (vector) both work.
- **Vector database**: stores vectors and finds nearest ones fast. Here: **LanceDB**, a folder of files on disk.

## Two stores, one source of truth (ADR 0002)
- **MariaDB** (`AI Knowledge Chunk`): authoritative text and metadata. Its autoincrement integer name **is** the LanceDB row id.
- **LanceDB** (`sites/<site>/private/files/lancedb`, table `chunks`: id, kb, source, content, vector): disposable index, rebuildable from MariaDB. Schema metadata records embedding provider, model, dimension; any mismatch is rejected ("rebuild the knowledge store").

## Ingestion (write path)
1. `enqueue_ingestion(source)` puts `ingest_source` on the `long` queue (after commit). A daily scheduler re-syncs DocType sources with `auto_sync`.
2. `ingest_source` sets status `Processing`; on any exception it rolls back, logs, marks `Failed` **first**, then calls the consumer hook `on_index_failed`, then re-raises.
3. `extract(source)` by type: Text, File (PDF via docling or pdfplumber with OCR fallback via rapidocr, DOCX, XLSX, HTML, text), URL (public URLs only, size-capped), DocType (selected fields of records).
4. `chunk_text`: character windows breaking on whitespace/newline, stepping back by the overlap, always advancing.
5. `embed_texts`: batches of 96 to Ollama's OpenAI-compatible `/embeddings`; records the vector width in `AI Settings.embedding_dimension` and rejects a changed width.
6. `store.ensure_table_for_dimension`, insert chunk rows in MariaDB, add `{id, kb, source, content, vector}` to LanceDB, create the FTS index once.
7. Status `Completed` with chunk count, or `Failed` if extraction yielded nothing. DocType sources use a modification watermark and content hashes, so only changed rows are re-embedded.

## Retrieval (read path)
`search_knowledge(query)` is a read-only tool. Dispatch injects the agent's knowledge-base list server-side (Lesson 5). `retriever.retrieve`:
1. Refuses an empty KB list (not read as "all"), drops disabled/deleted KBs.
2. Embeds the query; `store.search` runs hybrid (or vector-only per `AI Settings.search_type`) with cosine distance, prefiltered by `kb IN (...)`, limit 5 (max 100).
3. Hydrates the hit ids from MariaDB and `_permitted` drops chunks derived from a DocType record the acting user cannot read (`frappe.has_permission`, cached per document).
4. Returns `{chunk, content, score, source, reference_*}` for the model to use.

## Example (hypothetical)
User: "What is our refund window?" -> model calls `search_knowledge("refund window")` -> query vector -> hybrid search finds 3 chunks from "Returns Policy" -> Frappe filters by permission -> the model answers from those passages. Without the tool call, nothing is retrieved; the agent decides when to search.

## Production notes
- Handled: rebuildable index with dimension/model guards, per-KB scoping, document-level permission filter, failure state tracking, lazy embedding availability (chat works if Ollama is down).
- Partial: character chunking ignores document structure; one writer assumed (LanceDB); embedding model fixed (changing it needs a full rebuild); retrieved text goes straight into the prompt (prompt-injection surface; no relevance threshold or reranking).
- Missing: retrieval quality evaluation; citations in answers are the model's job.

## Checkpoint questions
1. Why keep chunk text in MariaDB when LanceDB also stores `content`?
2. Why does hybrid search help compared with vector-only?
3. What guards stop you mixing vectors from two different embedding models?
4. A user cannot read an Invoice; can an agent running as them surface an Invoice-derived chunk? Which function decides?
