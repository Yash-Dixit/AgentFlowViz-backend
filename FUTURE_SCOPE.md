# Future Scope

This document lists optional improvements that can make AgentFlowViz more production-ready over time. The current app does not require these additions to run locally, but they are useful next steps if the platform grows beyond a local demo.

## Current Baseline

The current backend already includes:

```text
FastAPI API layer
LangGraph workflow execution
Postgres persistence with Alembic
pgvector semantic search
Ollama local text, embedding, and vision models
Telegram integration
MinIO object storage
RSS RAG scheduler
Semantic cache
Conversation memory
Streamlit monitoring through backend APIs
Run logs, messages, tool calls, token metrics, and local energy-cost estimates
```

The ideas below should be treated as future enhancements, not missing requirements.

## 1. Optional Observability With LangSmith Or Langfuse

### Why Consider It

The app already stores observability data locally in Postgres, but a dedicated tracing platform could make debugging easier as workflows become more complex.

LangSmith could help with:

```text
LangGraph node-level traces
Prompt and response inspection
Routing-debug views
Dataset-based evaluation
Regression checks for agent behavior
```

Langfuse could help with:

```text
Self-hosted LLM tracing
Prompt versioning
Latency dashboards
Cost dashboards
Session-level analytics
Human feedback scores
```

### How It Could Be Added Later

1. Add optional environment flags:

```env
TRACING_ENABLED=false
LANGSMITH_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=
```

2. Add a tracing adapter behind an interface such as:

```text
app/runtime/tracing.py
```

3. Emit trace events from:

```text
app/runtime/executor.py
app/runtime/graph.py
app/ollama_service.py
app/runtime/tools.py
```

4. Keep Postgres logs as the source of truth so the app remains fully local when tracing is disabled.

### Recommendation

Do not add this until the local monitoring UI is no longer enough. It adds setup work and may capture sensitive prompts or uploaded document summaries if not configured carefully.

## 2. LlamaIndex Or Another RAG Framework

### Why Consider It

The current RAG layer is custom and intentionally lightweight:

```text
Ollama embeddings
Postgres pgvector
Custom RSS ingestion
Custom attachment context extraction
Custom semantic cache
```

LlamaIndex could become useful if the app needs richer document workflows.

Potential use cases:

```text
Large document collections
Multiple document loaders
Chunking and metadata pipelines
Hybrid keyword plus vector search
Reranking
Query engines
Knowledge-base connectors
Multi-document citations
```

### How It Could Be Added Later

1. Keep existing Postgres and MinIO storage.
2. Add LlamaIndex only around document ingestion and retrieval.
3. Store parsed chunks in a new table, for example:

```text
document_chunks
  id
  attachment_id
  run_id
  chunk_text
  metadata_json
  embedding
```

4. Replace or extend `attachment_context_reader` with a retrieval tool:

```text
document_rag_retriever
```

5. Keep RSS RAG separate unless there is a clear reason to merge it.

### Recommendation

Do not add LlamaIndex now. The current custom RAG layer is easier to explain and enough for RSS, semantic cache, and uploaded file context. Add LlamaIndex only if document search becomes a primary feature.

## 3. SFT Or LoRA Fine-Tuning

### Why Consider It

Supervised fine-tuning is not needed for the current product. Prompting, tools, memory, and RAG are better levers right now.

Fine-tuning could be considered if the app later has:

```text
Hundreds or thousands of high-quality example conversations
Stable target output formats
Repeated failures that prompts and tools cannot fix
Domain-specific language that local models handle poorly
Evaluation sets to prove the fine-tuned model improved behavior
```

### How It Could Be Added Later

1. Persist approved examples from successful runs.
2. Add a human review flag in the monitoring UI.
3. Export accepted examples into an SFT dataset.
4. Fine-tune a small local model or LoRA adapter.
5. Add an evaluation suite before switching production workflows.

Potential dataset shape:

```json
{
  "workflow": "support_triage_router",
  "agent": "Support Reply Writer",
  "input": "User request plus selected specialist output",
  "expected_output": "Approved final response"
}
```

### Recommendation

Treat SFT as a later optimization. For now, improve prompts, routing conditions, RAG context, and output formatting first.

## 4. Stronger Image And Document Intelligence

### Why Consider It

The app supports PDFs and images today, but local vision quality depends on the configured Ollama vision model.

Future improvements:

```text
Use a stronger local vision model when VRAM allows
Add OCR for screenshots and scanned PDFs
Add table extraction for PDFs
Add document chunking for long files
Store page-level or image-region metadata
Support multi-file comparison
Add file preview links in the monitoring UI
```

### Suggested Implementation Path

1. Add OCR as a separate optional tool:

```text
ocr_text_extractor
```

2. Store extracted OCR text in `AttachmentContext.extracted_text`.
3. Keep MinIO as the raw-file store.
4. Add page or image metadata to `metadata_json`.
5. Add vector embeddings per chunk if files become too large for one context block.

## 5. Queue And Concurrency Improvements

### Why Consider It

The current backend uses FastAPI background tasks for Streamlit runs and synchronous execution inside the Telegram polling thread. This is simple and works locally, but a real queue would be better for more users.

Future options:

```text
Redis Queue
Celery
Dramatiq
Arq
Postgres-backed job queue
```

### Suggested Implementation Path

1. Add a `jobs` table or Redis-backed queue.
2. Move `execute_demo_run` into a worker process.
3. Keep run status in Postgres.
4. Let Telegram return quickly while workers process jobs.
5. Add retry limits and dead-letter handling.

This would improve simultaneous-user behavior and make long-running workflows easier to manage.

## 6. Real-Time Streaming

### Why Consider It

Telegram currently shows typing indicators and sends the final answer. Streamlit currently polls backend state. Token streaming could make the app feel faster.

Potential upgrades:

```text
Server-Sent Events for Streamlit or a future web UI
WebSocket run updates
Partial final-answer streaming
Telegram message editing for controlled partial updates
Live node-status updates in the workflow canvas
```

### Recommendation

Start with Streamlit polling improvements or Server-Sent Events. Telegram token streaming should be added carefully because frequent message edits can hit Telegram limits and create noisy UX.

## 7. Workflow Evaluation Harness

### Why Consider It

As more workflows are added, it becomes useful to know whether routing and final answers are improving or regressing.

Future additions:

```text
Golden test prompts per workflow
Expected route assertions
Expected tool-call assertions
Output quality rubrics
Latency thresholds
Cache-hit checks
Memory behavior checks
```

Suggested file structure:

```text
evals/
  smart_task_router.yml
  image_document_router.yml
  media_news_research_router.yml
```

This would make demos safer and make future prompt changes easier to validate.

## 8. Security And Access Control

### Why Consider It

The local app currently has a Telegram allow-list and local services. If it becomes multi-user or externally hosted, more controls will be needed.

Future additions:

```text
User accounts
Role-based access control
Per-user workflow visibility
Per-user file buckets or prefixes
Audit logs
Secret redaction in traces and logs
Rate limiting
Upload type restrictions
Virus scanning for uploaded files
```

## 9. Better Template Marketplace

### Why Consider It

Workflow creation currently happens through the Streamlit UI. A template gallery would make it easier for non-technical users.

Future additions:

```text
Prebuilt template gallery
One-click template install
Template import/export as JSON
Template versioning
Template validation before save
Examples for support, finance, research, media, and operations
```

## Suggested Priority Order

If continuing development, the most practical order is:

```text
1. Workflow evaluation harness
2. Stronger OCR and document chunking
3. Real queue for concurrent runs
4. Template import/export and validation
5. Optional Langfuse or LangSmith tracing
6. LlamaIndex only if document RAG becomes large-scale
7. SFT only after collecting high-quality reviewed examples
```

This keeps the platform useful and explainable while avoiding heavy additions before they are needed.
