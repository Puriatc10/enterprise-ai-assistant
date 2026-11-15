# Enterprise AI Assistant

[![CI](https://github.com/Puriatc10/enterprise-ai-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Puriatc10/enterprise-ai-assistant/actions/workflows/ci.yml)

A web-based enterprise assistant that answers natural-language questions about data
spread across multiple organizational systems — by calling narrowly scoped, read-only
tools and composing the answer from what those tools actually returned.

> **Status: Phase 3 — tool execution.** The backend now runs the full loop: authenticated
> request → LLM proposes a tool → the backend validates, authorizes and executes it →
> the answer is composed from what the tool returned. Authentication and conversation
> persistence are wired to Supabase; the data behind the tools is synthetic (ADR-0002).
> The frontend is still a skeleton, and this is **not** production-ready.

---

## Problem

Organizations keep their operational truth in many places: a CRM, an insurance
platform, one or more PostgreSQL databases, and various internal REST APIs. A person
with a question such as *"How many insurance policies do I have?"* has to know which
system holds the answer, open it, and interpret a raw UI. None of those systems know
about each other, and none of them can answer a question in plain language.

Wrapping a generic LLM around those systems is not a solution either: a model that
summarizes from memory invents data, and a model trusted with access control leaks
data. The interesting work is the layer in between — orchestration plus **authorized,
typed, read-only tool access**.

## Product vision

An authenticated user asks a question in natural language. The assistant:

1. understands what information is required;
2. selects a suitable, pre-registered tool;
3. retrieves data through a connector that runs under the user's own authorization;
4. computes with structured results in application code (not in the model);
5. answers in natural language, grounded exclusively in retrieved data.

Persian-language input is the expected primary interface for the first demonstration.

**The LLM never invents data and never decides permissions.** Identity and tenant scope
are taken from a verified authentication token by the backend, and tool execution is
authorized server-side before any connector runs.

## Main capabilities

| Capability | State |
|---|---|
| Web chat interface (React + TypeScript) | skeleton, no chat (Phase 5) |
| Backend API (FastAPI) | health + conversations + send turn |
| Provider-independent LLM abstraction | port + LiteLLM adapter, OpenRouter verified |
| Function / tool calling with validation | **implemented** — registry + guarded executor |
| Multiple read-only data connectors | mock (demo), PostgreSQL (CI-verified), REST (allowlisted) |
| Supabase auth + persistence | JWT verification + RLS-scoped repository |
| Docker-based dev environment | working |
| Python packaging & tests | working (1 health test) |

Deliberately **out of scope for the MVP**: write operations against external systems,
CRUD on customer data, connector management UI, dynamic connector configuration,
multi-agent orchestration, RAG/vector infrastructure, fine-tuning, Kubernetes,
microservices.

## Technology stack

React · TypeScript · Vite — FastAPI · Pydantic · Uvicorn · HTTPX — LiteLLM SDK in-process
with OpenRouter's free tool-capable models (Gemini kept as a later, blocked alternative)
— Supabase (PostgreSQL + Auth) — Docker /
Compose — Render for first hosting.

Rationale, alternatives, and limitations for each choice: [`docs/architecture/TECH_STACK.md`](docs/architecture/TECH_STACK.md).

## High-level architecture

```text
                      USER
                       |
                       v
                React Chat UI
                       |
                       v
                  FastAPI API
                       |
            Authentication / Session
                       |
                       v
               Conversation Layer
                       |
                       v
                AI Orchestrator
                       |
            +----------+----------+
            |                     |
            v                     v
      LLM Abstraction        Tool Registry
            |                     |
            v                     v
         LiteLLM              Tool Executor
            |                     |
            v                     v
      Gemini / Other         Data Connectors
      LLM Providers                |
                            +------+------+
                            |      |      |
                            v      v      v
                          Didar  SQL   External
                           API    DB     APIs

                    Application Storage
                           |
                         Supabase
```

A modular monolith, internally separated by Ports & Adapters. Full design, data flow,
and tool-calling sequence: [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md).

## Repository structure

```text
enterprise-ai-assistant/
├── apps/
│   ├── api/                      FastAPI backend (single deployable unit)
│   │   ├── app/
│   │   │   ├── main.py           app factory, lifespan, CORS, router mounting
│   │   │   ├── core/             settings, dependency wiring
│   │   │   ├── api/              HTTP layer (routers only, no business logic)
│   │   │   ├── domain/           models + ports (protocols); no framework imports
│   │   │   ├── application/      orchestration, services, tool registry/executor
│   │   │   ├── infrastructure/   adapters: LLM, connectors, persistence
│   │   │   └── security/         auth context, token verification, authorization
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   └── web/                      React + TypeScript + Vite frontend
│       ├── src/
│       ├── package.json
│       └── Dockerfile
├── docs/
│   ├── architecture/             ARCHITECTURE.md, TECH_STACK.md, SECURITY.md
│   ├── decisions/                ADR-0001-initial-architecture.md
│   ├── development/              DEVELOPMENT.md
│   └── ROADMAP.md
├── docker-compose.yml            local development stack
├── AGENTS.md                     engineering rules for coding agents
├── .env.example
└── README.md
```

Empty packages are not created ahead of need. Directories appear when a phase gives
them a real occupant.

## Local setup

Prerequisites: **Python 3.12+**, **uv**, **Node.js 20+**, **npm**, and optionally
**Docker** with Compose v2.

```bash
git clone <this-repo> enterprise-ai-assistant
cd enterprise-ai-assistant
cp .env.example .env          # optional at Phase 0 — nothing is required to boot
```

### Backend

```bash
cd apps/api
uv sync --python 3.12                       # base deps only
uv run uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/health> → `{"status":"ok", ...}`.
Interactive API docs: <http://localhost:8000/docs>.

Adding the LLM stack (Phase 1 onward, needs a key to actually call a model):

```bash
uv sync --extra llm
```

### Frontend

```bash
cd apps/web
npm install
npm run dev            # http://localhost:5173
```

The API base URL comes from `VITE_API_BASE_URL` (see `.env.example`); nothing is
hardcoded in the client.

### Docker Compose

```bash
docker compose up --build            # api :8000, web :5173
docker compose config               # validate configuration only
```

No database container is provided on purpose: persistence is Supabase (managed), and
the application database is logically separate from connector data sources.

## Development commands

| Task | Command |
|---|---|
| Run API (reload) | `cd apps/api && uv run uvicorn app.main:app --reload` |
| Backend tests | `cd apps/api && uv run pytest` |
| Lint | `cd apps/api && uv run ruff check .` |
| Format | `cd apps/api && uv run ruff format .` |
| Frontend dev | `cd apps/web && npm run dev` |
| Frontend typecheck | `cd apps/web && npm run typecheck` |
| Frontend build | `cd apps/web && npm run build` |
| Whole stack | `docker compose up --build` |
| Tool-calling spike (needs `OPENROUTER_API_KEY`) | `cd apps/api && uv sync --extra llm && uv run --extra llm python scripts/phase1_spike.py` |

### Continuous integration

Every push to `main` and every pull request runs `.github/workflows/ci.yml` on GitHub
Actions:

| Job | What it proves |
|---|---|
| `api` | locked install on Python 3.12, ruff lint + format, pytest, and that the project still installs with plain `pip` (the path the container image uses) |
| `web` | locked `npm ci`, TypeScript typecheck, production build, and no credential-shaped string in `dist/` |
| `docker` | both images build, the compose file validates, the API container answers `/health` with **no** credentials, the web container serves the built page |

The Docker job exists because image builds could not be run on the original development
host (Docker Hub unreachable there); CI is where the container path is verified.

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) | modules, data flow, tool-calling sequence, RAG positioning |
| [`docs/architecture/TECH_STACK.md`](docs/architecture/TECH_STACK.md) | every technology: role, why, alternatives, limits |
| [`docs/architecture/SECURITY.md`](docs/architecture/SECURITY.md) | authn/authz model, tenant isolation, LLM data privacy |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phases 0–6 plus future knowledge retrieval |
| [`docs/decisions/ADR-0001-initial-architecture.md`](docs/decisions/ADR-0001-initial-architecture.md) | what was decided, and what is still provisional |
| [`docs/decisions/ADR-0002-didar24-access-path.md`](docs/decisions/ADR-0002-didar24-access-path.md) | why the mock connector is the primary data path |
| [`docs/decisions/ADR-0003-provider-selection.md`](docs/decisions/ADR-0003-provider-selection.md) | why the MVP runs on OpenRouter rather than Gemini |
| [`docs/decisions/ADR-0004-tool-execution.md`](docs/decisions/ADR-0004-tool-execution.md) | the tool execution model: allowlist, refusals as data, identity from context |
| [`docs/decisions/ADR-0005-connector-model.md`](docs/decisions/ADR-0005-connector-model.md) | one connector contract; read-only enforced twice; projection owned by the domain |
| [`docs/analysis/PHASE1-DIDAR24.md`](docs/analysis/PHASE1-DIDAR24.md) | Phase 1 evidence: what Didar24 exposes, and what it does not |
| [`docs/analysis/PHASE1-LLM.md`](docs/analysis/PHASE1-LLM.md) | Phase 1 evidence: measured tool-calling results, and the adapter rules they imply |
| [`docs/development/DEVELOPMENT.md`](docs/development/DEVELOPMENT.md) | environment details, ports, troubleshooting |
| [`AGENTS.md`](AGENTS.md) | rules for AI coding agents working in this repo |

## Known limitations at this stage

- No chat, no LLM call, no connector: by design (Phase 0 scope).
- Authentication is not enforced yet; the enforcement point exists and returns
  `501 Not Implemented` rather than pretending to pass.
- Didar24 integration feasibility is unverified and may reduce to a mock connector.
- Free-tier LLM usage is not acceptable for real customer data.
- Render's free tier spins down when idle and has ephemeral storage — fine for a
  demonstration, not a service level.
