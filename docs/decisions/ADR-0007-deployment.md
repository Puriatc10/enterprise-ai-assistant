# ADR-0007 — Deployment topology and the free tier

- **Status:** accepted
- **Date:** 2026-11-15
- **Context phase:** Phase 6 (testing, deployment, demonstration)

## Context

The MVP has to be demonstrable from a public URL, for free, without weakening anything the
earlier ADRs claimed. Constraints that shaped the choice:

- The API is a long-lived async process with an in-process LLM SDK (ADR-0003) — not a
  stateless function.
- Persistence and auth are managed (Supabase), so there is no database to operate.
- The free provider is slow and occasionally rate-limited; the platform must not make that
  worse.
- Nothing may store customer data on a disk we control (the demo data is synthetic anyway).

## Decisions

### 1. API as a Docker web service, web as a static site

The API deploys the same `apps/api/Dockerfile` that CI builds and boots with no
credentials. The frontend deploys as a static bundle.

**Why:** the verified artifact is the deployed artifact. A platform-specific Python
runtime would mean the container is decorative; a server-rendered frontend would mean a
second process to keep alive for no benefit — the client is a SPA whose only build inputs
are public.

**Alternatives:** Vercel (needs an ASGI→serverless adapter and changes the execution model
the LLM client assumes), Fly.io (fine technically, more moving parts for a demo), a single
container serving both (mixes two lifecycles and makes the static tier's CDN pointless).

### 2. Render, free plan, accepting the spin-down

Free instances sleep after 15 minutes. That is documented rather than engineered around.

**Why:** the honest constraint is better than a paid instance hiding it: cold starts are a
real property of free hosting, and the project's own documentation should admit it. The
mitigation is procedural — warm the API before a demo.

**Cost:** the first request after idle is slow; a visitor arriving cold may think the site
is down. A keep-alive cron would defeat the free tier's purpose and is not added.

### 3. Two services, one blueprint

`render.yaml` declares both services, so the topology is reviewable in a pull request
instead of living in a dashboard.

**Why:** infrastructure that cannot be diffed drifts. Secrets are `sync: false`, so the
blueprint is complete and contains no credentials.

### 4. Configuration stays environment-shaped

Every environment-specific value — model, timeouts, CORS origin, Supabase project,
connector source — is an env var, and the frontend's three `VITE_` values are build-time.
There is no per-environment code path.

**Why:** it is the same rule the application already follows (ADR-0001), and it means a
staging environment is a second blueprint apply rather than a branch.

### 5. CORS is an exact origin, never a wildcard

`API_CORS_ORIGINS` must name the static site's origin precisely.

**Why:** an access token in a browser is a bearer credential; `*` would let any page use a
session the user already has. The failure mode of getting it wrong (a browser CORS error
that looks like a dead API) is annoying but loud, which is the right way to be wrong.

### 6. Migrations are applied by hand to Supabase

No migration runner in the deploy pipeline.

**Why:** with one environment and a managed Postgres, a runner adds a failure mode
(deploy succeeds, schema does not) without adding safety. Revisit the moment there is a
second environment or a second contributor applying schema changes.

## Consequences

- A public demo costs nothing and needs four secret values, not a cluster.
- Cold start is a known, stated property; the runbook says warm it.
- Rolling back is "deploy the previous build" — safe because the schema is applied
  deliberately and forward-only.
- No local persistence: an instance restart costs a JWKS refetch and nothing else.

## Provisional

- Region choice (Frankfurt) is a guess about the audience, not a measurement.
- If the demo outgrows the free tier's cold start, the first lever is a paid instance, not
  a rewrite — the blueprint changes one field.
- Custom domains and TLS are Render's defaults; neither is configured yet.
