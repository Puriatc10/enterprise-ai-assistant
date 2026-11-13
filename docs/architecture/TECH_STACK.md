# Technology Stack

Every technology below is here for a stated responsibility. For each: what it does,
why it was chosen, what it replaced, what it costs us, and whether it is MVP-required.

Legend for **Status**: `MVP-required` · `MVP-optional` · `Future`.

Verification date for model/provider facts: **2026-11-14**. Provider-facing numbers
(pricing, quotas, model ids) change often — re-verify in Phase 1 before relying on them.

---

## Frontend

### React 19

- **Responsibility**: chat UI, conversation state, rendering streaming answers.
- **Why**: largest component ecosystem; well-understood by any future contributor; pairs
  naturally with Vite and TypeScript.
- **Alternatives**: Vue (smaller ecosystem for the AI/chat component space), Svelte
  (leaner but fewer ready-made building blocks), server-rendered HTML + htmx (would
  remove the SPA entirely — viable for a chat UI, but poor for future rich rendering of
  tool results and Persian RTL polish).
- **Limitations**: no opinion on data fetching or state; bundle size grows without
  discipline; requires care for RTL layouts (see frontend conventions).
- **Status**: MVP-required. Docs: <https://react.dev/>

### TypeScript

- **Responsibility**: type safety across API contracts and components.
- **Why**: the backend contract is typed (Pydantic → OpenAPI); typed client code keeps
  them aligned and catches integration mistakes at compile time.
- **Alternatives**: plain JavaScript (faster to start, drifts from the API contract).
- **Limitations**: build/typing friction; `any` can silently undo the benefit — banned in
  new code.
- **Status**: MVP-required. Docs: <https://www.typescriptlang.org/docs/>

### Vite

- **Responsibility**: dev server and production bundler.
- **Why**: fast HMR, near-zero config, first-class TS/JSX, static output that any static
  host (Render Static Sites) can serve.
- **Alternatives**: Next.js (SSR not needed for an authenticated chat app and adds a
  Node runtime to host), CRA (deprecated), Parcel (smaller ecosystem).
- **Limitations**: build-time environment injection (`VITE_*` vars are public), so never
  put secrets there; SSR is out of scope.
- **Status**: MVP-required. Docs: <https://vite.dev/guide/>

---

## Backend

### Python 3.12+

- **Responsibility**: implementation language.
- **Why**: strongest ecosystem for LLM tooling and data access; readable by a small,
  educational team.
- **Alternatives**: Node/TypeScript end-to-end (one language, but weaker data/LLM
  libraries), Go (faster runtime, slower iteration for this domain).
- **Limitations**: the free hosting tier is slower to cold-start than Go; dependency
  weight must be managed deliberately.
- **Status**: MVP-required. Docs: <https://docs.python.org/3.12/>

### FastAPI

- **Responsibility**: HTTP API, request validation, dependency injection, OpenAPI.
- **Why**: Pydantic-native validation, async-first, DI that fits Ports & Adapters
  composition, free OpenAPI for frontend typing.
- **Alternatives**: Flask (no async/validation story without add-ons), Django REST
  Framework (heavier, ORM-centric for a system whose data lives elsewhere), Litestar
  (capable but smaller ecosystem).
- **Limitations**: DI is convention-based rather than a container — wiring discipline is
  ours to keep; sync endpoints must be avoided in async paths.
- **Status**: MVP-required. Docs: <https://fastapi.tiangolo.com/>

### Pydantic v2 + pydantic-settings

- **Responsibility**: validated request/response models, tool argument schemas, typed
  configuration.
- **Why**: one schema language for HTTP boundaries, LLM tool parameters, and settings.
  The same model that validates a tool call can generate its JSON schema for the model.
- **Alternatives**: dataclasses + manual checks (more code, weaker errors), attrs,
  marshmallow (older style).
- **Limitations**: v1→v2 API differences are a common source of stale examples; settings
  must not silently accept unknown keys in security-relevant places.
- **Status**: MVP-required. Docs: <https://docs.pydantic.dev/latest/>

### Uvicorn

- **Responsibility**: ASGI server.
- **Why**: reference server for FastAPI; simple process model that fits one-container
  deployment on Render.
- **Alternatives**: Hypercorn (HTTP/2, unnecessary), Gunicorn+Uvicorn workers (only worth
  it beyond a single small instance).
- **Limitations**: process-level scaling only; a free-tier instance is a single worker.
- **Status**: MVP-required. Docs: <https://www.uvicorn.org/>

### HTTPX

- **Responsibility**: async HTTP client for LLM providers and REST connectors.
- **Why**: async, typed, one client for both internal calls and connector adapters,
  with explicit timeouts (required by the tool-execution policy).
- **Alternatives**: `requests` (sync; blocks the event loop), `aiohttp` (fine, but two
  client styles in one codebase).
- **Limitations**: connection pooling must be managed per app lifespan; timeouts are not
  opinionated — set them explicitly everywhere.
- **Status**: MVP-required. Docs: <https://www.python-httpx.org/>

### uv

- **Responsibility**: dependency management, virtualenvs, locking, running commands.
- **Why**: fast, produces a lockfile for reproducible installs, installs a pinned Python
  interpreter when needed, and avoids the global-interpreter drift this host has
  (Python 3.14.9 as `python`, 3.12.4 via the launcher).
- **Alternatives**: pip + venv (works, no lockfile), Poetry (heavier, slower), conda
  (overkill here).
- **Limitations**: not universally installed; CI/Render must install it or use the
  generated lockfile.
- **Status**: MVP-required. Docs: <https://docs.astral.sh/uv/>

---

## LLM integration

### Google Gemini (initial provider)

- **Responsibility**: language understanding, tool-call proposal, answer generation.
- **Why**: usable free tier for an educational MVP, strong multilingual/Persian
  performance, function calling, long context.
- **Alternatives**: OpenAI (best tooling, no meaningful free tier), Groq (fast, limited
  model choice), OpenRouter (one key for many models, adds a hop), local Ollama/vLLM
  (no data leaves the machine, requires hardware and ops).
- **Verified at bootstrap (2026-11-14)**
  - `gemini-3.5-flash-lite` is a real, currently-offered model id (previously-generation
    release, still available for production use; it supersedes the role of
    `gemini-3.1-flash-lite`).
  - LiteLLM model string: **`gemini/gemini-3.5-flash-lite`** (Google AI Studio), also
    available via Vertex AI and third-party gateways.
  - Published paid pricing: **$0.30 / $2.50 per 1M input/output tokens**; 1M-token
    context; function calling supported.
  - Free tier: included ("free of charge") — per-model RPM/TPM/RPD are not published as
    a static table and are read from Google AI Studio for the project's own quota.
    Community reports put high-throughput Flash-Lite models at ~500 RPD on the free
    tier; treat that as an estimate, not a contract.
- **Limitations**: free-tier content may be used to improve Google products → **no real
  PII on the free tier** (see `SECURITY.md`); quota is per-project and per-model; 429s
  must be handled; latency varies by region (relevant for Iran-based usage — verify
  reachability in Phase 1).
- **Status**: MVP-required. Docs: <https://ai.google.dev/gemini-api/docs> ·
  pricing <https://ai.google.dev/gemini-api/docs/pricing> ·
  rate limits <https://ai.google.dev/gemini-api/docs/rate-limits>

### LiteLLM Python SDK (in-process, not the Proxy)

- **Responsibility**: provider-agnostic completion + function calling behind our own
  `LLMPort`.
- **Why**: one call shape for Gemini/OpenAI/Groq/OpenRouter/Ollama; retries, timeouts,
  cost tracking, and normalized tool-call objects; migrating providers becomes config.
- **Alternatives**: `google-genai` SDK directly (vendor lock-in at the call site),
  LangChain (much larger abstraction surface than needed and hard to keep out of the
  domain), a self-hosted LiteLLM Proxy (an extra service to run, secure, and pay for —
  rejected for the MVP).
- **Limitations**: it is a large dependency with its own release cadence; its response
  objects must not leak past our adapter; vendor-specific features lag on day-0 support.
- **Status**: MVP-required for LLM work; declared as an **optional extra** (`uv sync
  --extra llm`) so the Phase 0 skeleton installs and boots without it.
- Docs: <https://docs.litellm.ai/> · model map <https://models.litellm.ai/>

---

## Data & persistence

### Supabase (PostgreSQL + Auth)

- **Responsibility**: application database (users, conversations, messages, tool audit)
  and authentication (password/OAuth, JWT issuance).
- **Why**: managed Postgres without running a database or an auth server; JWT-based auth
  that a FastAPI backend can verify without a shared session store; row-level security
  available as defense in depth.
- **Alternatives**: own Postgres + Auth.js/Keycloak (full control, real ops burden),
  Firebase (no SQL — wrong for conversations and joins), Clerk/Auth0 + Neon (good, two
  vendors and one more integration surface).
- **Limitations**: the SDK is JS-first (Python access is thin — plan on `httpx`/`asyncpg`
  against the REST/Postgres endpoint, not the client library); service-role key bypasses
  RLS and must never leave the backend; free project limits include pausing on
  inactivity.
- **Status**: MVP-required (Phase 2). Docs: <https://supabase.com/docs>

### PostgreSQL (connector target)

- **Responsibility**: reading structured organizational data through read-only,
  explicitly defined queries.
- **Why**: the expected shape of enterprise customer data; strong typing supports
  trustworthy answers.
- **Alternatives**: none at this layer — the adapter is generic over SQL sources within
  reason.
- **Limitations**: schema varies per deployment; requires a dedicated read-only role;
  never expose arbitrary query execution to the model.
- **Status**: MVP-required (Phase 4). Docs: <https://www.postgresql.org/docs/>

---

## Containerization & hosting

### Docker + Docker Compose

- **Responsibility**: reproducible local environment; the same artifact deployed later.
- **Why**: removes "works on my machine"; one command brings up api + web; Compose config
  is validated in CI.
- **Alternatives**: bare local tooling only (simplest, but no parity with hosting),
  Podman (compatible, less ubiquitous).
- **Limitations**: Docker Desktop on Windows adds VM overhead; no database service is
  used on purpose (Supabase is managed).
- **Status**: MVP-required. Docs: <https://docs.docker.com/compose/>

### Render (Free tier)

- **Responsibility**: first public hosting — Web Service (Docker) for the API, Static
  Site for the frontend.
- **Why**: free, minimal ops, Docker support, automatic TLS, deploy from a repo.
- **Alternatives**: Fly.io (better cold starts, requires a card), Railway (trial credit),
  Vercel/Netlify for the frontend only, a VPS (full control, full ops burden).
- **Limitations**: free web services spin down after inactivity (**30–60 s cold start**),
  ephemeral filesystem (nothing durable on local disk), limited CPU/RAM, monthly hours
  cap, no persistent disk. Acceptable for a demo; not for a product.
- **Status**: MVP-optional (Phase 6). Docs: <https://render.com/docs/free>

---

## Testing & quality

| Tool | Responsibility | Why / alternatives | Status |
|---|---|---|---|
| `pytest` | backend test runner | standard, fixture model fits DI; alternative `unittest` is verbose | MVP-required |
| `httpx` + `ASGITransport` | API tests without a live server | no port binding, fast, same client library | MVP-required |
| `ruff` | lint + format | one fast tool replacing flake8+isort+black; alternative `black`+`flake8` is two tools | MVP-required |
| `mypy` | static typing (adopt incrementally) | catches port/adapter drift; alternative `pyright` | MVP-optional |
| `tsc --noEmit` | frontend typecheck | already present via Vite's TS setup | MVP-required |

Docs: <https://docs.pytest.org/> · <https://docs.astral.sh/ruff/>

---

## Not in this stack, deliberately

| Excluded | Why not now |
|---|---|
| LiteLLM Proxy as a service | extra deployable, extra auth surface, no MVP benefit |
| Vector database (pgvector/Pinecone/Qdrant) | no documents to search yet; RAG is a future phase |
| LangChain / LlamaIndex / agent frameworks | would put provider- and framework-specific types in the domain |
| Celery / queues | no background jobs yet; tool calls are per request |
| Redis | no cache or session store needed while auth is JWT-based |
| Kubernetes / Terraform | one container, one static site |
| OpenTelemetry stack | structured logs first; tracing when there is something to trace |
