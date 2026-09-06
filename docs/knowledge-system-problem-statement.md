# Frappe AI Knowledge System: Problem Statement

## Summary

`frappe_ai` is intended to provide an app-agnostic knowledge system that consumer
apps can use to load documents, create searchable knowledge, and retrieve relevant
content without implementing their own extraction, chunking, embedding, indexing,
error handling, or lifecycle logic.

The current implementation works end to end, but several concerns are coupled:

- A knowledge base must group related sources and provide a search boundary.
- A source must be extracted, split into passages, embedded, and indexed.
- Embedding failures must be visible and retryable.
- Repeated files should not create duplicate indexed content.
- Consumer apps need lifecycle hooks without adding consumer-specific fields to the
  core DocTypes.
- Operators need to know whether a source is actually searchable.
- The system must keep the embedding model and vector dimensions consistent.

The main architectural question is whether every indexed passage must be persisted
as an `AI Knowledge Chunk` Frappe DocType row, or whether chunks can remain internal
to the vector index.

## Domain model

### AI Knowledge Base

An `AI Knowledge Base` is a named collection of related knowledge. It is the logical
search scope used by retrieval.

Example:

```text
Tender Enquiry RAG: TE-0001
```

A knowledge base contains metadata such as:

- `title`
- `description`
- `enabled`

It does not represent one file and does not directly contain the extracted document
text.

### AI Knowledge Source

An `AI Knowledge Source` is one input into a knowledge base. A source can represent:

- a Frappe `File`
- a URL
- direct text
- a set of rows from another DocType

Examples:

```text
Tender-001.pdf
Tender-002.pdf
https://example.com/specification
A text document
A filtered set of ToDo records
```

A source stores the input and processing state, including:

- `knowledge_base`
- `source_type`
- `file`, `url`, `content`, or `reference_doctype`
- `status`
- `chunk_count`
- `is_embedded`
- `error_log`

### AI Knowledge Chunk

An `AI Knowledge Chunk` is one extracted passage from a source. A large document is
split into smaller passages so that each passage can be embedded and retrieved with
useful precision.

For example:

```text
Source: tender-001.pdf

Chunk 1: Tender title and issuing authority
Chunk 2: Eligibility requirements
Chunk 3: Technical specifications
Chunk 4: Commercial terms
```

The current chunk record contains the passage text, its source, its position, and
optional DocType provenance. It is a MariaDB record, not a user-created business
record.

### LanceDB index

LanceDB stores the vector representation used for similarity search. Its rows contain
the information required to search and identify a matching passage, including the
source and knowledge-base identifiers.

The current storage model treats MariaDB as authoritative for knowledge text and
metadata, while LanceDB is a disposable derived search index.

## Current ingestion flow

For a file source, the current flow is:

```text
Frappe File
    |
    v
AI Knowledge Source
    |
    v
Extract file text
    |
    v
Split text into chunks
    |
    v
Call Ollama embedding API
    |
    v
Persist AI Knowledge Chunk rows
    |
    v
Write vectors to LanceDB
    |
    v
Mark source Completed and Embedded
```

The public Python API supports both single-source and bulk loading:

```python
knowledge = Knowledge(title="Tender Enquiry RAG: TE-0001")
source_name = knowledge.add_file("/private/files/tender-001.pdf")
source_names = knowledge.add_files([
    "/private/files/tender-001.pdf",
    "/private/files/tender-002.pdf",
])
```

The convenience form is:

```python
knowledge = Knowledge.load_files(
    [
        "/private/files/tender-001.pdf",
        "/private/files/tender-002.pdf",
    ],
    kb_title="Tender Enquiry RAG: TE-0001",
)
```

`Knowledge.add_file` performs synchronous ingestion. Saving an
`AI Knowledge Source` through the Desk queues ingestion asynchronously after insert.

## Source state and embedding state

`status` describes the ingestion lifecycle:

```text
Pending -> Processing -> Completed
                     \-> Failed
```

`is_embedded` answers a different question:

> Does this source currently have at least one usable indexed embedding?

The intended state rules are:

| Source condition | `status` | `is_embedded` |
| --- | --- | --- |
| Newly created, not processed | `Pending` | `0` |
| Ingestion running | `Processing` | `0` |
| Successful ingestion with chunks | `Completed` | `1` |
| Successful ingestion with no extracted content | `Completed` | `0` |
| Ingestion or embedding failure | `Failed` | `0` |

`is_embedded` is read-only and maintained by the ingestion pipeline. It is not a
consumer custom field and should not be manually set by users.

## Problems being addressed

### 1. Consumer apps should not implement their own knowledge pipeline

Without a stable, app-agnostic API, each consumer app tends to duplicate:

- source creation
- file extraction
- chunking
- embedding calls
- vector storage
- source status updates
- failure handling
- duplicate detection

This creates inconsistent behavior and makes changes to the embedding backend or
search index expensive.

The core API should hide those details behind a small contract:

```text
input file(s) -> source name(s) or knowledge handle
```

Callers should not need to know how many chunks were created or how the vector index
is implemented.

### 2. Duplicate files waste work and storage

The same bytes may be uploaded more than once under different filenames. Without
content-based deduplication, the system creates duplicate sources and duplicate
embeddings.

Deduplication is scoped to the knowledge base:

```text
knowledge base + source type + content hash
```

The same bytes may intentionally appear in different knowledge bases because those
knowledge bases can have different access controls or business meaning.

### 3. Consumer-specific relationships should not be core schema

A consumer app may need to associate a source with one of its own records. For
example, a tender app may need to associate a source with a tender enquiry.

That metadata belongs to the consumer app, not to the generic knowledge system.
Consumers can add their own `Custom Field` fixtures, for example:

```text
custom_tender_enquiry
custom_project
custom_document_owner
```

The example consumer fixture in `frappe_ai/fixtures/` is only a documentation
pattern. It is not required by the core system, is not automatically loaded, and has
no effect on embedding.

### 4. Lifecycle events need to be observable

A source can fail after it has been created, especially when extraction or embedding
is performed asynchronously. A source row alone does not provide enough operational
context.

The system therefore needs:

- normal lifecycle logs for start and successful outcomes
- structured failure logs containing the source and traceback
- consumer hooks for source creation and index failure
- a retry path through resync or re-ingestion

The lifecycle hooks are:

```text
ai_knowledge_before_source_create
ai_knowledge_after_source_create
ai_knowledge_on_index_failed
```

These are name-keyed hooks that consumer apps register in their own `hooks.py`.
`frappe_ai` does not register consumer handlers itself.

### 5. Embedding availability must be explicit

A source can exist in the database but still be unusable for retrieval because:

- it is waiting to be processed
- extraction produced no text
- embedding failed
- its vectors were removed during rebuild or reconciliation

The `is_embedded` field provides a direct source-level availability signal, while
`chunk_count` provides the amount of indexed content.

### 6. Embedding dimensions must not be mixed

The configured embedding dimension must match the dimension returned by the active
embedding model.

The current model is:

```text
Provider: Ollama
Model:    nomic-embed-text
Dimension: 768
```

If `AI Settings.embedding_dimension` contains `4` while Ollama returns vectors of
length `768`, the system rejects the operation rather than mixing incompatible vector
spaces.

The error is expected in that situation:

```text
Configured embedding dimension is 4, but Ollama returned 768.
```

The correct recovery is to rebuild the knowledge index. The rebuild probes Ollama,
resets the stored dimension, recreates the LanceDB index, re-embeds existing chunks,
and records the detected dimension.

## The AI Knowledge Chunk design question

Chunking itself is generally necessary for retrieval quality. Embedding an entire
large document as one vector can produce poor matches and exceed model context
limits.

The question is whether chunks must be persisted as first-class MariaDB rows.

### Current design: persisted chunks

```text
Source
  -> extract and chunk
  -> persist AI Knowledge Chunk rows
  -> embed chunks
  -> persist LanceDB vectors
```

Benefits:

- MariaDB remains the source of truth for indexed text.
- Search results can be hydrated reliably.
- LanceDB can be rebuilt from MariaDB without the original file.
- Chunk provenance is explicit.
- Incremental DocType synchronization is easier.
- Source deletion and reconciliation have clear database records.

Costs:

- Large documents create many MariaDB rows.
- Cleanup and synchronization are more complex.
- Re-indexing must coordinate MariaDB and LanceDB.
- The internal implementation exposes more storage concepts than users need.

### Alternative design: chunks only in LanceDB

```text
Source
  -> extract and chunk in memory
  -> embed chunks
  -> persist chunk text and vectors only in LanceDB
```

Benefits:

- Fewer MariaDB rows.
- Less Frappe DocType overhead.
- A smaller relational schema.

Costs:

- LanceDB becomes responsible for both text and vectors.
- Rebuilding requires re-reading original sources.
- Deleted or inaccessible files cannot be rebuilt.
- Incremental DocType synchronization becomes harder.
- Search recovery depends more heavily on the derived index.
- Provenance and source-to-chunk relationships must be maintained in LanceDB.

Removing `AI Knowledge Chunk` is therefore not a local cleanup. It requires coordinated
changes to ingestion, retrieval hydration, source deletion, reconciliation, index
rebuilds, and the meaning of `chunk_count`.

## Optional embedding control

The ingestion API currently attempts to embed every source. An optional embedding
switch is a separate requirement from the source-level `is_embedded` flag.

A future API could expose an option such as:

```python
knowledge.add_file(file_url, embed=False)
```

or:

```python
ingest_source(source_name, embed=False)
```

The meaning of `embed=False` must be defined explicitly. Possible behaviors are:

### Store chunks without embeddings

```text
extract -> chunk -> persist text chunks -> skip vector generation
```

The source ingestion can complete, but:

```text
is_embedded = 0
```

The source cannot participate in vector retrieval until it is embedded later.

### Defer all processing

Only create the source and leave it pending. No extraction or chunking occurs until a
later embedding-enabled run.

### Dry run

Extract and chunk in memory but persist neither chunks nor vectors. This is useful for
validation but does not create searchable knowledge.

The choice is coupled to the chunk-storage decision. If persisted chunks are removed,
then `embed=False` needs another place to retain extracted text or must treat the
original source as the only durable input.

## Operational recovery

### Ollama unavailable

If the Frappe worker cannot reach the configured endpoint, ingestion fails and the
source remains unembedded:

```text
status = Failed
is_embedded = 0
```

The endpoint must be reachable from the Frappe worker, not merely from an operator's
interactive shell. In containerized deployments, `localhost` may refer to the Frappe
container rather than the host running Ollama.

### Dimension mismatch

If the configured dimension and returned vector dimension differ, run:

```bash
bench --site <site> execute frappe_ai.knowledge.rebuild_knowledge_index
```

Then retry the failed source through the source's Resync action.

### Failed source retry

A failed source does not become embedded automatically merely because Ollama is later
available. It must be explicitly reprocessed through Resync or another ingestion
trigger.

## Success criteria

The knowledge system is successful when:

1. A consumer app can load one or many files through `Knowledge` without knowing the
   internal extraction or vector-index implementation.
2. Repeated files in the same knowledge base reuse the existing completed source.
3. A source exposes reliable `status`, `chunk_count`, and `is_embedded` values.
4. Successful sources can be searched immediately after synchronous ingestion or after
   asynchronous ingestion completes.
5. Failed sources contain useful diagnostic information and can be retried.
6. Consumer apps can subscribe to lifecycle events without modifying core schema.
7. The embedding dimension is detected and enforced consistently.
8. Rebuilding the search index does not silently mix incompatible vector spaces.
9. The storage design preserves enough information to recover the search index under
   the chosen persistence and rebuild strategy.

## Scope boundaries

In scope for the core knowledge system:

- knowledge-base and source lifecycle
- file, text, URL, and DocType source ingestion
- extraction, chunking, and embedding orchestration
- deduplication
- source status and embedding state
- lifecycle events and audit logging
- visibility APIs
- vector-index consistency

Not core knowledge-system responsibilities:

- consumer-specific business fields
- tender-specific source ownership
- consumer-specific migration of existing records
- consumer-specific UI workflows
- choosing business access rules for each knowledge base

Consumer apps may add those behaviors through their own fields, hooks, and services.
