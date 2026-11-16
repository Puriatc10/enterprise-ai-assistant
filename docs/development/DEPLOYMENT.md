# Deployment

Two Render resources, defined in `render.yaml` at the repository root:

```text
Browser ──► Static site (apps/web dist)  ──► FastAPI container (apps/api Dockerfile)
                    │                                   │
                    └──── Supabase Auth (JWKS) ─────────┴──► Supabase PostgREST (RLS)
                                                            └──► mock policy connector
```

The web tier is static because the client is a single-page app: there is no server-side
rendering, and the only build-time inputs are public values. The API is a container
because `apps/api/Dockerfile` already builds and boots in CI, so the thing that is
verified is the thing that is deployed.

## First deployment

1. Push the repository (the blueprint is read from the default branch).
2. Render → **New → Blueprint** → select the repo. It creates both services and asks for
   every `sync: false` value.
3. Fill the API service:
   - `OPENROUTER_API_KEY` — the provider key.
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY` — the same project the tokens come from.
   - `API_CORS_ORIGINS` — the static site's exact origin, e.g.
     `https://enterprise-ai-assistant.onrender.com`. Scheme and host, no trailing slash;
     a mismatch produces a browser CORS failure that looks like a dead backend.
4. Fill the static site: `VITE_API_BASE_URL` (the API's public URL, no trailing slash),
   `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. Changing any of these needs a rebuild,
   not a restart — they are inlined into the bundle.
5. Verify: `GET {api}/health` returns `{"status":"ok", ..., "llm":{"configured":true}}`,
   and the static site shows the login screen.

`SUPABASE_SERVICE_ROLE_KEY` is never set anywhere. The backend queries PostgREST with the
caller's own token so row-level security stays the backstop (SECURITY.md §2), and the
client bundle is scanned for a `service_role` JWT on every build.

## Free-tier limits, and what they mean here

| Limit | Consequence for this project |
|---|---|
| Services spin down after 15 min without traffic | The first request after idle waits for a cold start (tens of seconds). The UI's 60s turn timeout is deliberately longer than the 45s model timeout, so the turn survives; the *first* page load may not. |
| 512 MB RAM / 0.1 CPU | Fine for this workload: the container is a thin async API. It is not fine for local embedding models — another reason RAG is a later phase. |
| Ephemeral filesystem | Nothing is stored on disk. Conversation state lives in Supabase; the API holds no local database (Phase 0 decision). A container restart loses only the JWKS cache, which refetches. |
| 750 instance-hours/month | One always-on service would exceed a month (~730h) only if it never idled; with spin-down it stays under. |
| 100 GB/month bandwidth | Not a constraint at demo traffic. |
| 500 build minutes/month | Each Docker build is a few minutes; a handful of deploys per week is well inside it. |

**Before any demo, warm the API** (`curl {api}/health`) and wait for a 200. A cold start
in front of an audience reads as "the product is broken", and no amount of correct code
fixes that impression.

## Operating notes

- **Logs**: Render captures stdout. Every response carries `X-Request-ID`, and the same id
  is in the error envelope, so a user's screenshot maps to a log line.
- **Rollback**: Render keeps previous deploys; use "Deploy previous version". Nothing in
  this app keeps migrations in lockstep with code, because the schema changes are applied
  to Supabase deliberately (§ migrations below).
- **Migrations**: `supabase/migrations/*.sql` are applied by hand in the Supabase SQL
  editor. This is a conscious choice for a single-environment MVP: a migration runner
  without a staging database would be ceremony, not safety.
- **Rate limits**: the free model returns 429 under load; the API maps it to `429` with a
  Persian message and the UI says the service is busy. That is the designed behaviour, not
  an incident.

## Live deployment (2026-11-16)

| | URL |
|---|---|
| App | https://eaa-web-8bvb.onrender.com |
| API | https://enterprise-ai-assistant-api-jmdj.onrender.com (`/health`, `/docs`) |
| Render services | `enterprise-ai-assistant-api` (Docker, frankfurt, free) · `eaa-web` (static, free) |

The `-jmdj` / `-8bvb` suffixes are Render's: the plain names were already taken, so it
appended a unique part. Both are the canonical hostnames `API_CORS_ORIGINS` and
`VITE_API_BASE_URL` point at.

### Things only a live deployment showed

1. **The image installed the base deps only.** `/health` reported `llm.configured: true`
   because the key was set, and every real turn failed with `RuntimeError: … needs the
   optional dependency`. The Dockerfile now installs `.[llm]`. CI never runs a turn against
   the image, so it could not catch this.
2. **The SQLite support database was missing.** The domain opens it read-only at startup
   and never creates it, so the image seeds it at build time from the committed SQL.
3. **Changing an environment variable does not restart a Docker service.** The new
   `API_CORS_ORIGINS` sat in the dashboard while the container kept the old value and every
   browser request failed as a "network" error. Render a redeploy after env changes.
4. **The static-site API field is `publishPath`**, not `staticPublishPath` (which is the
   YAML key). Creating the site through the API without it published `public`, an empty
   directory, so the deploy "succeeded" and served nothing.

### Known behaviour on the free plan

- The API sleeps after 15 minutes idle; the next request pays a 30–60 s cold start. The
  static site never sleeps.
- Large JS bundles transfer slowly to some networks (measured 1–3 kB/s from Iran, against
  ~1.2 MB/s from jsDelivr for the same size), so first load can take a while. Once the
  bundle is cached, navigation is instant. This is a network path, not the artifact: the
  same object downloads complete and byte-identical when the stream is allowed to finish.

## Not doing (yet)

Kubernetes, microservices, a queue, or a separate proxy service. Each adds an operational
surface this project does not need, and the modular monolith plus one container is what
the architecture decisions committed to (ADR-0001, ADR-0007).
