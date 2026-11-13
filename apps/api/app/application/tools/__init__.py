"""Tool registry and executor (Phase 3) — the security choke point.

Each tool declares: name, description, a Pydantic argument model, an output model,
the scopes it requires, and a timeout. The executor enforces, in order:
registry allowlist -> schema validation -> scope check -> identity injection from
``AuthContext`` -> bounded execution -> output validation -> audit record.

Forbidden here, permanently: a generic ``execute_sql`` / ``fetch_url`` / shell
tool, and any tool whose arguments can name a user or tenant.
"""
