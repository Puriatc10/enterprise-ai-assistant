# Enterprise AI Assistant

[![CI](https://github.com/Puriatc10/enterprise-ai-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Puriatc10/enterprise-ai-assistant/actions/workflows/ci.yml)

A web-based enterprise assistant that answers natural-language questions about data
spread across multiple organizational systems — by calling narrowly scoped, read-only
tools and composing the answer from what those tools actually returned.

> **Status: Phases 7–9 complete — multi-source, streaming, hardened.** Three data domains
> (insurance, support, CRM) behind one orchestrator, a Persian RTL interface with
> token-based light/dark themes and SSE streaming, per-user rate limiting, and measured
> tool routing (92 %). Deployment is prepared in `render.yaml` but **not applied**, and the
> live Supabase row-level-security path is unverified without a confirmed user.

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
| Web chat interface (React + TypeScript) | **implemented** — RTL Persian chat, SSE streaming, markdown, model-written titles, rename/delete, tool provenance, dark mode |
| Backend API (FastAPI) | health + conversations + send turn |
| Provider-independent LLM abstraction | port + LiteLLM adapter, OpenRouter verified |
| Function / tool calling with validation | **implemented** — registry + guarded executor |
| Multiple read-only data connectors | **3 domains / 8 tools** — insurance (mock), support (SQLite), CRM (PostgreSQL) |
| Supabase auth + persistence | JWT verification + RLS-scoped repository |
| Docker-based dev environment | working |
| Python packaging & tests | 265 backend tests (10 PostgreSQL integration in CI) + 21 client tests |

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

### The short version — one command

```bash
bash scripts/dev.sh        # API on :8000 + web on :5173; Ctrl-C stops both
```

Open <http://localhost:5173>. On a fresh clone, install first:

```bash
cd apps/api && uv sync --python 3.12 --extra llm && cd ../..
cd apps/web && npm install && cd ../..
cp .env.example .env.local     # then set VITE_API_BASE_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
```

**Signing in:** this Supabase project has email confirmation enabled, so a new account gets
no session until the link in the confirmation mail is opened. The app says exactly that;
see `docs/development/DEVELOPMENT.md`.

### Backend, on its own

```bash
cd apps/api
uv sync --python 3.12 --extra llm    # --extra llm is what lets it call a model
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000/health> → `{"status":"ok", ...}`.
Interactive API docs: <http://localhost:8000/docs>.

The venv binary is used rather than `uv run` because on this host `uv run <tool>` has printed
empty summaries ("no tests collected", exit 0); on Linux and in CI `uv run` is fine.

### Frontend

```bash
cd apps/web
npm install
npm run dev            # http://localhost:5173
```

The client needs three build-time variables. **Put them in the repository-root `.env`
(or `.env.local`)**, not in `apps/web/` — `vite.config.ts` sets `envDir: "../.."` so one
file serves the whole repo, and a `VITE_*` file inside `apps/web/` is silently ignored:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_SUPABASE_URL=https://<project>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon public key>
```

Without the two Supabase values the app renders a notice naming what is missing instead of
crashing — the same fail-visibly rule the backend follows when it returns 501.

Sign-in, the token and the chat thread all live in the browser; the anon key is public by
design and row-level security is the actual control. **Never** put the `service_role` key
in any `VITE_*` variable: everything with that prefix is inlined into the bundle.

The demo login path needs a confirmed user. In a fresh Supabase project, create one under
**Authentication → Users**, or turn off **Confirm email** for the demo project; otherwise
signup returns no session and the UI will say a confirmation email was sent.

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
| **Everything CI runs** | `bash scripts/check_all.sh` |
| Seed the support database | `cd apps/api && .venv/Scripts/python.exe scripts/seed_support_sqlite.py` |
| Seed the CRM demo account | `cd apps/api && .venv/Scripts/python.exe scripts/seed_crm.py --dsn <url> --subject <uuid>` |
| Measure tool routing | `cd apps/api && .venv/Scripts/python.exe scripts/route_eval.py` |
| Run API (reload) | `cd apps/api && uv run uvicorn app.main:app --reload` |
| Backend tests | `cd apps/api && uv run pytest` |
| Lint | `cd apps/api && uv run ruff check .` |
| Format | `cd apps/api && uv run ruff format .` |
| Frontend dev | `cd apps/web && npm run dev` |
| Frontend typecheck | `cd apps/web && npm run typecheck` |
| Frontend unit tests | `cd apps/web && npm test` |
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
| [`docs/decisions/ADR-0006-browser-auth.md`](docs/decisions/ADR-0006-browser-auth.md) | browser auth, the error envelope, and why the turn reports `tools_used` |
| [`docs/decisions/ADR-0007-deployment.md`](docs/decisions/ADR-0007-deployment.md) | Render topology, the free tier accepted on purpose, CORS as an exact origin |
| [`docs/decisions/ADR-0008-multi-source.md`](docs/decisions/ADR-0008-multi-source.md) | domains as registries; read-only enforced per engine; scope in SQL because a direct connection bypasses RLS |
| [`docs/decisions/ADR-0009-interface-streaming.md`](docs/decisions/ADR-0009-interface-streaming.md) | tokens over a framework, streaming rules, sanitised markdown, lazy chat chunk |
| [`docs/analysis/PHASE7-ROUTING.md`](docs/analysis/PHASE7-ROUTING.md) | measured routing accuracy with eight tools, including the one miss |
| [`docs/development/DEPLOYMENT.md`](docs/development/DEPLOYMENT.md) | how to deploy, and what the free tier costs in behaviour |
| [`docs/DEMO.md`](docs/DEMO.md) | the five-minute walkthrough, with its failure modes |
| [`docs/PLAN-PHASE-7-9.md`](docs/PLAN-PHASE-7-9.md) | the post-MVP review and the plan for multi-source, the new interface, and hardening |
| [`docs/PROJECT-REPORT.md`](docs/PROJECT-REPORT.md) | **the full report: phases 0–9, measurements, and the honest gap list** |
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
