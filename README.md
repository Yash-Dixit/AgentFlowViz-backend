# AgentFlowViz Backend

Runtime and API layer for the AgentFlowViz AI Agent Orchestration Platform.

This repository is intentionally isolated from the Streamlit frontend. Keep backend runtime code, API routes, persistence models, messaging-channel integrations, tests, and backend dependencies here.

For the shortest local setup path, use [STARTUP.md](STARTUP.md). For optional longer-term improvements, see [FUTURE_SCOPE.md](FUTURE_SCOPE.md).

## Repository Pairing And Setup Order

AgentFlowViz is split into two sibling repositories:

```text
AgentFlowViz-backend     FastAPI, LangGraph runtime, Postgres, Telegram, Ollama calls
AgentFlowViz-frontend    Streamlit UI, workflow editor, monitoring, settings
```

Set up the backend repo first. It owns Docker Compose for the full local stack, starts Postgres, applies backend migrations, exposes the API, and builds the frontend container from the sibling frontend repo.

## Role In The System

The backend is responsible for:

- Running real multi-agent workflows
- Managing asynchronous agent-to-agent communication
- Persisting agents, workflows, messages, tool calls, logs, and run history
- Exposing APIs consumed by the Streamlit frontend
- Integrating external messaging channels such as Telegram
- Tracking run status, token usage, cost estimates, and failures

## Recommended Architecture

![AgentFlowViz architecture](docs/architecture.svg)

The frontend is the control plane: it creates and edits workflows, agent configs, JSON packages, and monitoring views. The backend is the execution plane: it loads the saved graph, runs LangGraph nodes, calls local Ollama models, persists all run artifacts, and bridges Telegram, RSS, Postgres, pgvector, and MinIO.

## Backend Package Structure

```text
app/
  api/              FastAPI routers grouped by feature
  runtime/          LangGraph execution, routing, tools, persistence helpers
  services/         Business logic behind API routes
  config.py         Environment-backed settings
  database.py       Postgres SQLModel engine and sessions
  models.py         SQLModel persistence models
  schemas.py        API request schemas
  serializers.py    Response serializers
  seed.py           Default agents and workflow templates
  telegram_bot.py   Telegram polling channel adapter
  main.py           App creation, lifespan, CORS, router registration
tests/
  test_api.py       Critical-path API and runtime tests
```

## Technology Choice Justification

- Python keeps the agent runtime, API, tests, and Streamlit UI in one easy-to-review language.
- FastAPI gives a small typed API surface for the Streamlit frontend and Telegram adapter.
- Streamlit was chosen for fast iteration and visual configurability without a heavy frontend build system.
- LangGraph satisfies the required real agent runtime while keeping workflow state inspectable.
- Postgres gives durable persistence and safer concurrency for multiple users.
- SQLModel keeps the Python data model readable, while Alembic owns database schema migrations.
- Ollama keeps all LLM calls local on the target rig, avoiding hosted API keys and recurring inference cost.

## Local Hosting Target

This project is designed to run on the personal rig currently available:

```text
OS       Windows 11 Pro 64-bit
CPU      Intel Core i5-14600K, 14 cores / 20 logical processors
RAM      32 GB system memory
GPU      NVIDIA GeForce RTX 5080, 16 GB VRAM
CUDA     13.1 according to nvidia-smi
Disk     About 392 GB free on C:
Ollama   0.23.2
```

Installed Ollama models found locally:

```text
gpt-oss:20b            20.9B params, MXFP4, tools, thinking, 128K context
qwen3.5:9b             9.7B params, Q4_K_M, tools, vision, thinking, 256K context
qwen3-embedding:4b     4.0B params, Q4_K_M, embeddings, 40K context
llava:7b               7B vision model for image attachment summaries
```

Recommended defaults:

- `gpt-oss:20b` for final-answer agents where response quality matters.
- `qwen3.5:9b` for intake, planning, routing, classifiers, specialists, UI chat previews, and lower-latency tasks. Send `think=false` when the UI needs a direct visible answer.
- `qwen3-embedding:4b` for memory search, workflow-template retrieval, and RAG over saved run history.
- `llava:7b` for image uploads through Streamlit or Telegram. Set `OLLAMA_VISION_MODEL=llava:7b`; without this, image uploads are stored but only receive a generic fallback summary.

Short local probes on this rig completed successfully:

```text
qwen3.5:9b      120 output tokens in about 1.5s, around 109 tok/s with think=false
gpt-oss:20b      53 output tokens in about 6.1s, around 165 tok/s with low thinking
```

The RTX 5080's 16 GB VRAM is enough for the current single-model demo flow. For reliability during local demos, run one primary chat model plus the embedding model, and avoid loading multiple large chat models concurrently unless there is enough free VRAM.

## Simplicity-First Scope

Build one clean end-to-end path first:

```text
Telegram user message
    -> Intake Agent
    -> Router Agent
    -> One chosen Specialist Agent
    -> Response Agent
    -> Telegram reply
```

Keep the implementation easy to understand:

- Use FastAPI for a small API surface.
- Use LangGraph for executable, inspectable workflow runtime behavior.
- Use the official `ollama` Python client directly for model calls.
- Use Postgres through SQLModel for persistence.
- Use Alembic for schema creation and future migrations.
- Use one queue/table for inter-agent messages instead of a complex broker.
- Use plain Python tools first: web fetch, calculator, file/report writer, and knowledge lookup.
- Add abstractions only after the first demo workflow works.

## Capability Checklist

```text
Agent CRUD                         /agents endpoints + Postgres Agent table + Streamlit editor
Agent config                       Prompt, model, tools, channels, schedules, memory, skills,
                                   interaction rules, guardrails, and execution/token limits
Visual workflow builder            Streamlit flow canvas with conditions, feedback loops,
                                   node inspector, edge inspector, and saved positions
Workflow templates                 smart_task_router and image_document_router
Workflow lifecycle                 Create, edit, enable/disable, delete, and reseed templates
Orchestration decision node        Router agents choose exactly one specialist branch per run
Real runtime                       LangGraph runtime loads saved workflow graph and agent configs
Real tools                         ToolCall records for local_research, support_knowledge_lookup,
                                   calculator, report_writer, rss_rag_retriever,
                                   and attachment_context_reader
Async agent communication          Runs execute in FastAPI background tasks and persist messages
Message history visible in UI      /runs/{id}/messages consumed by Streamlit Monitoring
External channel                   Telegram polling bot with allow-listed user IDs
Live monitoring                    Logs, messages, tool calls, token metrics, status, and cost
Conversation memory                Telegram user/chat scoped short-term turns plus rolling summary
Context window management          Short, long, and large Ollama num_ctx profiles with truncation logs
Final output guard                 Larger final-agent budgets plus a repair pass for clipped replies
Async handoff queue                Persisted agent mailbox with queued and delivered states
RSS RAG lead/news index            5-minute RSS ingestion into Postgres pgvector
Semantic caching                   Postgres pgvector cache with HNSW cosine index for repeated prompts
Media/object storage               MinIO-backed image and document uploads with extracted context
Cache savings telemetry            Cache-hit runs show saved LLM calls and prompt/output tokens
Local energy telemetry             Wattage-based kWh and cost estimate from Ollama durations
2+ agent demo                      Telegram -> multi-agent workflow -> Telegram response
Tests                              Agent creation, graph-driven workflow execution, message delivery,
                                   Telegram status, Telegram formatting
Local LLM operation                Ollama locally; no hosted LLM API key required
Production-style persistence       Postgres database managed by Alembic migrations
Documentation                      Architecture, setup, runtime choice, Telegram, templates, channels
```

## Suggested Setup

For a quick Docker startup, use [STARTUP.md](STARTUP.md). The commands below are for manual development.

One-command Windows setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Manual setup:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
docker compose up -d postgres
python -m alembic upgrade head
ollama pull gpt-oss:20b
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:4b
ollama pull llava:7b
```

## Suggested Run Command

Scripted:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Runtime stdout/stderr logs are written under `logs/`.

Manual:

```bash
python -m alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

## Docker Run

For a one-command local demo across both repositories, keep the frontend and backend folders as siblings:

```text
AgentFlowViz/
  AgentFlowViz-backend/
  AgentFlowViz-frontend/
```

Then run from `AgentFlowViz-backend`:

```powershell
docker compose up --build
```

This starts Postgres with pgvector, the FastAPI backend, and the Streamlit frontend. Ollama is expected to run on the host machine at `http://localhost:11434`; the containers reach it through `host.docker.internal`. Docker Compose automatically reads backend `.env` when present, so Telegram secrets can stay there. If `.env` is missing, the app still starts with Telegram setup disabled.

Important Ollama caveat: Docker Compose starts the application services and database, but it does not run the Ollama model server. Start Ollama on the host machine before launching the stack, and make sure the configured models are already pulled:

```powershell
ollama serve
ollama pull gpt-oss:20b
ollama pull qwen3.5:9b
ollama pull qwen3-embedding:4b
ollama pull llava:7b
```

If Ollama is not reachable from the containers, backend health will show the model server as unavailable and workflow runs will fail or fall back depending on the local settings.

## Tests And Coverage

Run the backend tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The default pytest configuration measures coverage for `app/` and fails if total coverage drops below 40%.
The coverage target is intentionally focused on critical backend behavior: API flows, Postgres persistence,
LangGraph runtime execution, routing, tools, Telegram authorization, message formatting, and Ollama fallback handling.

## Environment Variables

Create a local `.env` file for secrets and runtime settings:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
DATABASE_URL=postgresql+psycopg://agentflowviz:agentflowviz@localhost:5433/agentflowviz
TEST_DATABASE_URL=postgresql+psycopg://agentflowviz:agentflowviz@localhost:5433/agentflowviz_test
OLLAMA_HOST=http://localhost:11434
OLLAMA_DEFAULT_MODEL=gpt-oss:20b
OLLAMA_FAST_MODEL=qwen3.5:9b
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b
SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.92
SEMANTIC_CACHE_TTL_HOURS=24
SEMANTIC_CACHE_EMBEDDING_DIMENSIONS=1024
OLLAMA_CONTEXT_WINDOW_TOKENS=8192
CONTEXT_WINDOW_OVERHEAD_TOKENS=192
FINAL_RESPONSE_MIN_OUTPUT_TOKENS=360
FINAL_RESPONSE_DEFAULT_OUTPUT_TOKENS=420
FINAL_RESPONSE_MAX_OUTPUT_TOKENS=1024
FINAL_RESPONSE_REPAIR_TOKENS=180
INFERENCE_POWER_WATTS=450
ELECTRICITY_COST_PER_KWH_USD=0.15
RSS_SCHEDULER_ENABLED=true
RSS_CRAWL_INTERVAL_MINUTES=5
RSS_FEED_URLS=https://news.ycombinator.com/rss,https://export.arxiv.org/rss/cs.AI,https://export.arxiv.org/rss/cs.LG
RSS_MAX_FEEDS=5
RSS_MAX_ITEMS_PER_FEED=10
RSS_MAX_NEW_ITEMS_PER_RUN=25
RSS_ITEM_MAX_CHARS=1200
RSS_RETRIEVAL_LIMIT=3
RSS_RETRIEVAL_CANDIDATES=20
RSS_FRESHNESS_HOURS=24
RSS_CONTEXT_MAX_CHARS=1800
CONVERSATION_MEMORY_RECENT_RUNS=4
CONVERSATION_MEMORY_SUMMARY_MAX_CHARS=1600
CONVERSATION_MEMORY_RECENT_MAX_CHARS=1800
```

## Postgres And Alembic

This backend is Postgres-only. The app does not create tables at startup; Alembic owns schema creation and schema changes.

Start the included local Postgres container:

```powershell
docker compose up -d postgres
```

The container listens on host port `5433` to avoid clashing with other local Postgres services.

Apply migrations:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## Semantic Cache

Repeated, semantically similar prompts can skip full LangGraph/Ollama execution. The backend embeds the user prompt with `qwen3-embedding:4b`, searches Postgres through `pgvector`, and returns the cached final output when cosine similarity is at or above `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`.

Implementation details:

```text
Table          semanticcache
Embedding      vector(1024)
Index          HNSW using vector_cosine_ops
TTL            SEMANTIC_CACHE_TTL_HOURS
Scope          template_name + prompt similarity
Savings        source_run_id, model_calls, prompt_tokens, output_tokens, hit_count
```

The cache is intentionally conservative. Prompts containing numbers or time-sensitive terms such as `latest`, `today`, `price`, `weather`, or `status` skip semantic caching to avoid stale or unsafe reuse.

Telegram runs that use conversation memory also skip semantic caching. Memory-aware answers are user-specific, so the backend avoids reusing a cached answer across Telegram users or chats.

When a cache entry is stored, the backend saves the source run's model-call count and token footprint in the cache row. A cache hit records how many LLM calls and prompt/output tokens were avoided, and `/runs/{id}/metrics` plus `/health` expose those savings.

## Conversation Memory

Telegram memory has two layers:

```text
Short-term context   Last CONVERSATION_MEMORY_RECENT_RUNS completed Telegram turns for the same user
Long-term context    Rolling ConversationMemory summary keyed by channel + Telegram user ID
Scope                Telegram source channel and TELEGRAM_ALLOWED_USER_IDS identity
Storage              conversationmemory table plus telegram_user_id/chat_id on each run
```

When a Telegram prompt starts a run, the runtime loads the private memory for that Telegram user and injects it into agent prompts only when the agent has `memory_enabled=true`. After the final Telegram answer is sent, the backend updates the rolling summary in a background thread with the latest user prompt and final answer.

The memory prompt is private runtime context. Normal Telegram replies do not show memory internals, workflow logs, route decisions, or run metadata.

## Context Window Management

Every agent call goes through `app/runtime/context.py` before reaching Ollama. The context builder estimates token usage, reserves room for the agent's configured output limit, sets Ollama `num_ctx`, and trims lower-priority sections when the prompt would exceed the available input budget.

Context priority is:

```text
System prompt             Always sent as the Ollama system message
Original user request     Preserved as high-priority user intent
Current agent config      Role, interaction rules, and guardrails
Response budget           Visible token budget so the model plans a complete answer
Incoming agent context    Outputs from predecessor agents
Conversation memory       Telegram user summary and recent turns, when enabled
Tool results              Tool output for the current agent
Workflow context          Graph description and edge conditions
```

The runtime logs a `context_window` event for every agent call. The log stores token estimates, `num_ctx`, output reservation, and which sections were truncated. It does not store the full prompt text, so private memory and user content are not exposed in logs.

Final response agents get a safer minimum output budget even if an older saved Agent row still has a small `max_output_tokens` value. If a terminal answer still appears clipped, the runtime runs a short completion guard pass and logs `final_output_repaired` with metadata only. This prevents Telegram and Streamlit from showing replies that end mid-sentence or inside an unfinished list.

## Async Agent Handoffs

Agent-to-agent handoffs are persisted through the `agentmessagequeue` table. When one agent finishes, it enqueues a message for the next agent with a route condition and `queued` status. The recipient agent then delivers its mailbox messages, marks them `delivered`, and uses that payload as incoming context.

The regular `message` table remains the human-readable transcript for Streamlit Monitoring. The queue table plus `agent_message_enqueued` and `agent_messages_delivered` log events make the asynchronous handoff path auditable during a code walkthrough.

## RSS RAG Lead And News Index

The backend runs an optional APScheduler job every `RSS_CRAWL_INTERVAL_MINUTES` minutes. It fetches the configured RSS feeds, caps feed/item volume, deduplicates by URL/title hash, embeds each new item with `qwen3-embedding:4b`, and stores it in the `rssitem` table with a pgvector HNSW cosine index.

The Smart Task Router's `Research Specialist` includes the `rss_rag_retriever` tool. When a user asks for "top 3 news", "latest AI updates", or "top 3 leads", the tool retrieves from the indexed RSS store instead of live-fetching feeds inside the chat request. Retrieval is hybrid: it filters to the freshness window first, ranks by vector similarity, applies a recency boost, and includes deterministic lead-score tags for lead requests.

Default no-key feeds:

```text
https://news.ycombinator.com/rss
https://export.arxiv.org/rss/cs.AI
https://export.arxiv.org/rss/cs.LG
```

The answer should be phrased honestly as "latest indexed RSS items", not "the whole internet".

## Local Energy Cost

Because Ollama runs locally, hosted API cost is `0`. For a more realistic local-cost signal, `/runs/{id}/metrics` estimates energy from recorded Ollama `total_duration_ms` values:

```text
estimated_energy_kwh = inference_power_watts / 1000 * model_duration_seconds / 3600
estimated_energy_cost_usd = estimated_energy_kwh * electricity_cost_per_kwh_usd
```

Set `INFERENCE_POWER_WATTS` to the approximate GPU plus CPU draw during inference and `ELECTRICITY_COST_PER_KWH_USD` to your local electricity rate.

Create future migrations after editing SQLModel models:

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe change"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Do not commit real secrets.

This repository includes `.env.example` as a safe template. The real `.env` file is ignored by Git on purpose because it contains secrets.

## Live Telegram Setup

The Telegram integration uses polling, so it works locally without ngrok or a public webhook.

1. In Telegram, open `@BotFather`.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token into backend `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:replace_with_your_token
TELEGRAM_ALLOWED_USER_IDS=
```

4. Start or restart the backend.
5. Open your bot in Telegram and send:

```text
/whoami
```

6. Copy the returned numeric user ID into backend `.env`:

```env
TELEGRAM_ALLOWED_USER_IDS=123456789
```

7. Restart the backend again.
8. Send a normal message to the bot, for example:

```text
Research why local Ollama agents are useful for private automation and send me a concise report.
```

The bot will start a real LangGraph run, show a typing indicator while local Ollama-backed agents work, and send the final output back to Telegram. The same run appears live in the Streamlit Monitoring page.

Only the Telegram user IDs listed in `TELEGRAM_ALLOWED_USER_IDS` can trigger workflows. Unknown users can only receive an authorization message. If `TELEGRAM_ALLOWED_USER_IDS` is empty, the bot stays in setup mode and refuses workflow execution.

Telegram troubleshooting:

- Chat with the exact bot username shown in Streamlit Settings under Telegram. In this local setup it should match the username returned by `@BotFather`.
- Use a direct one-to-one chat with the bot for the demo. In Telegram groups, privacy mode can prevent bots from seeing normal messages unless group privacy is disabled in `@BotFather`.
- If the bot does not reply, open Streamlit Settings and check `Updates Seen`, `Last Action`, `Last Run`, and `Last Send Error` in Telegram diagnostics.
- If `Updates Seen` stays at `0` after you send `/start`, Telegram is not delivering messages to this local poller. Confirm you are messaging the right bot, the backend is running, and no other process is polling the same token.
- If `Updates Seen` increases but `Last Action` is `blocked_unauthorized_user`, send `/whoami` and add that numeric ID to `TELEGRAM_ALLOWED_USER_IDS`, then restart the backend.

## Telegram Chatbot UX

The Telegram bot is the external conversational channel for AgentFlowViz. It is designed as a workflow concierge, not a generic chatbot.

Supported commands:

```text
/start       Show workflow buttons
/templates   Choose a workflow template
/runs        Show recent runs
/status      Show the latest run status
/help        Show commands
/whoami      Show your Telegram user ID
/cancel      Clear selected workflow
```

Workflow buttons:

```text
Smart Task Router
Image and Document Router
Recent Runs
Help
```

Interaction flow:

```text
User opens /start
Bot shows workflow buttons
User chooses Smart Task Router or Image and Document Router
User sends any normal prompt
Bot starts a persisted LangGraph run
Bot shows typing indicators while the workflow runs
Agents execute with Ollama locally
Bot sends only the final answer back to the chat
Streamlit Monitoring shows the same run live
```

Telegram document and photo messages are supported for the media workflow. Send a PDF, text file, or image with a caption such as "Can you summarize this doc?" and the bot will download the file through Telegram, store it in MinIO, persist extracted attachment context in Postgres, and run `image_document_router`.

For photo analysis, make sure a local vision model is pulled and configured:

```powershell
ollama pull llava:7b
```

```env
OLLAMA_VISION_MODEL=llava:7b
```

Normal Telegram conversations do not expose system logs, route decisions, agent paths, or raw run metadata. Those details stay in Streamlit Monitoring and the persisted backend logs.

Follow-up questions can use prior Telegram context. For example, after telling the bot "My preferred report style is bullet points," a later prompt like "Use my preferred style" can use both the recent-turn context and the rolling memory summary for your Telegram user ID.

Telegram does not provide true token-by-token streaming like a web socket UI. The bot uses `sendChatAction` typing heartbeats while the local workflow runs, then sends the final answer. True token streaming is possible later by streaming the final Ollama call and periodically editing a Telegram message, but this implementation avoids noisy partial responses and Telegram edit-rate limits.

Different prompts work. The chosen template changes the workflow context:

- `smart_task_router` uses an Orchestrator node to choose one branch: Research Specialist, Math Specialist, or Support Specialist. Only the selected branch runs, then Response Synthesizer writes the reply.
- `image_document_router` uses a Media Orchestrator node to choose either Image Analyst or Document Analyst, then Media Response Writer writes the final response. Streamlit uploads are stored in MinIO and the extracted attachment context is persisted in Postgres.

## Adding Workflow Templates

Use the Streamlit Workflows page for the fastest path:

1. Create a workflow or seed the two example templates.
2. Add or create agent nodes in the inspector.
3. Drag nodes on the canvas.
4. Connect nodes and save the canvas.
5. Select an edge and set its condition or feedback-loop flag.
6. Enable the workflow when it should be runnable, or disable it to keep editing safely.
7. Delete templates you no longer need, or use the seed endpoint to restore the examples.
8. Run an active template from Streamlit or Telegram.

You can also create a template through the API:

```http
POST /workflows/templates
```

```json
{
  "name": "custom_workflow",
  "description": "A short description for the run context.",
  "is_active": true,
  "graph": {
    "nodes": ["Intake", "Researcher", "Writer"],
    "edges": [
      {"source": "Intake", "target": "Researcher", "condition": "ready"},
      {"source": "Researcher", "target": "Writer", "condition": "research_ready"},
      {"source": "Writer", "target": "Researcher", "condition": "needs_revision", "feedback_loop": true}
    ]
  }
}
```

The runtime loads the saved graph, follows non-feedback edges once, records feedback loops in the workflow context, uses matching Agent records when names match, and falls back to simple node-specific defaults when a node has no saved Agent yet.

## Adding Messaging Channels

Telegram is implemented as the first external channel. To add Slack or WhatsApp:

1. Create a new adapter module next to `app/telegram_bot.py`.
2. Keep secrets in `.env` and add safe examples to `.env.example`.
3. Reuse the same run creation path: create a `Run`, then call `execute_demo_run`.
4. Persist incoming and outgoing messages through the `Message` table.
5. Expose a small `/channel/status`, `/channel/start`, and `/channel/stop` API if the channel needs runtime control.
6. Add the channel name to agent `channels` in the UI and seed data.

## Walkthrough Recording Checklist

For demos or handoffs, record a short video or GIF that shows:

1. Show backend health and local Ollama models.
2. Open Streamlit Workflows and show the draggable graph plus inspector.
3. Edit an agent or edge condition from the Workflows page.
4. Send a prompt to the Telegram bot from an allowed user.
5. Show the final Telegram reply.
6. Open Streamlit Monitoring and show messages, logs, tool calls, tokens, and run status.

## Implementation Notes

- Use LangGraph for the agent runtime so workflows are executable, inspectable, and easy to explain.
- Use Ollama models only. Do not require hosted LLM API keys for the main demo.
- Use the official `ollama` Python client for model calls.
- Keep existing workflows fast by using `qwen3.5:9b` for intermediate agents, `gpt-oss:20b` for final response agents, and per-agent `max_output_tokens` limits with a completion guard for clipped final replies.
- Capture Ollama usage metrics from responses, including prompt tokens, output tokens, and durations, so the frontend can show live throughput and cost-equivalent telemetry.
- Use Postgres for persistence and Alembic for schema migrations.
- Use pgvector semantic caching with an HNSW cosine index to avoid repeated full workflow execution for similar prompts.
- Keep Telegram conversation memory user-scoped, with recent turns for short-term context and a rolling summary for long-term recall.
- Persist every inter-agent message and tool call so the frontend can show a live audit trail.
- Keep channel adapters behind a small interface so Telegram can later be swapped with Slack or WhatsApp.

## Ollama References

- Ollama API default local base URL: https://docs.ollama.com/api
- Ollama chat endpoint, tools, thinking, and runtime options: https://docs.ollama.com/api/chat
- Ollama usage metrics available in API responses: https://docs.ollama.com/api/usage
- Ollama embeddings guide: https://docs.ollama.com/capabilities/embeddings
