# Development Guide

Operational details that do not belong in the README. Architecture lives in
`../architecture/`; scope rules live in `../../AGENTS.md`.

---

## Verified environment (bootstrap host, 2026-11-14)

| Tool | Version found | Notes |
|---|---|---|
| Windows | 11 (10.0.26200) | shell is git-bash, not PowerShell |
| Python | 3.14.9 as `python`; **3.12.4** via `py -3.12` | project targets 3.12+ |
| uv | 0.12.17 | preferred package manager |
| Node.js | 24.21.0 | |
| npm | 11.19.0 | |
| Docker | 25.0.3 | daemon reachable |
| Docker Compose | v2.24.6-desktop.1 | |

**Why 3.12 and not the default 3.14**: the machine's default `python` is 3.14.9, which is
ahead of what parts of the LLM/data ecosystem (LiteLLM, tokenizer/compiled dependencies)
fully support. `uv sync --python 3.12` pins the project interpreter regardless of the
machine default, which also makes CI reproducible.

## Backend

```bash
cd apps/api
uv sync --python 3.12          # base deps (no LLM stack)
uv run uvicorn app.main:app --reload --port 8000
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Adding the LLM stack when Phase 1 starts:

```bash
cd apps/api
uv sync --python 3.12 --extra llm
```

`litellm` is an optional extra on purpose: the Phase 0 skeleton must install, boot,
and pass tests without it and without any API key.

### Phase 1 tool-calling spike

```bash
cd apps/api
uv sync --extra llm
uv run --extra llm python scripts/phase1_spike.py            # full Persian case set
uv run --extra llm python scripts/phase1_spike.py --case 1    # one case
```

It sends four synthetic-data questions to `LLM_MODEL` (default
`openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`) and reports, per case: the tool the
model asked for, the arguments it extracted, whether the tool round-trip completed,
latency, and token usage. Exit code is `2` if a tool expectation fails. `--model` switches
provider without editing code.

Without a credential for the configured provider (`OPENROUTER_API_KEY` for the default
model) it prints a skip and exits `0` — the spike is safe to run anywhere and is never part
of CI. The live run happens through the manual `llm-spike` workflow, which reads the
`GEMINI_API_KEY` repository secret and does nothing when it is absent.

## Frontend

```bash
cd apps/web
npm install
npm run dev          # http://localhost:5173
npm run typecheck
npm run build
```

Environment: `VITE_API_BASE_URL` (see `.env.example`). Vite only exposes `VITE_`-prefixed
variables, and everything it exposes is **public** — never put a secret there.

## Ports

| Service | Port |
|---|---|
| API (local) | 8000 |
| Web dev server | 5173 |
| API (compose) | 8000 |

## Docker

```bash
docker compose config        # validate only, no containers
docker compose up --build    # api + web
docker compose down
```

No database container: persistence is Supabase (managed). If you need a throwaway
Postgres for *connector* work in Phase 4, add it there — never as the application store.

`docker compose config` validates the compose file; only a real `docker compose build`
validates the Dockerfiles. Both images are built and smoke-tested in CI (`.github/
workflows/ci.yml`), which is the authoritative check when a local registry is blocked.

## Configuration rules

- Settings are defined once, in `app/core/config.py` (Pydantic Settings).
- Every new setting gets a line in `.env.example` with a comment, and a default that
  keeps `GET /health` working without it.
- No literal URLs, model ids, or credentials outside `.env` / settings.

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: app` | running uvicorn from the repo root | `cd apps/api` first |
| `GET /health` returns 500 on startup | an import of a missing optional dependency | check the traceback; LLM imports belong behind the `llm` extra and inside `infrastructure/llm/` |
| CORS error in the browser | origin not in `API_CORS_ORIGINS` | add it, restart the API |
| `uv sync` fails resolving a package | network/resolver | retry; if the host blocks a registry, configure a mirror rather than pinning odd versions |
| Compose build cannot find files | build context is the repo root | Dockerfiles copy from repo-root paths; run compose from the root |
| `docker compose build` fails or hangs at `registry-1.docker.io` | Docker Hub is unreachable from this network (observed: HTTP 403, then a pull that never completes) | configure a registry mirror in Docker Desktop (Settings → Docker Engine → `registry-mirrors`) or build where Docker Hub is reachable. Until then the Dockerfiles are **unvalidated by execution**; `docker compose config` still validates the compose file itself. |

## Conventions worth repeating

- Commits: Conventional Commits. No secrets, no PII, no generated lockfile churn mixed
  into feature commits.
- Tests must not require network or real keys. Fake the adapters.
- If a change touches `app/security/`, `app/application/tools/`, or a connector, update
  `docs/architecture/SECURITY.md` in the same change.
