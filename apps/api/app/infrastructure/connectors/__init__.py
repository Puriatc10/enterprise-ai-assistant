"""Data connector adapters (Phase 4).

Planned implementations, each a ``DataConnectorPort``:

* ``didar/``        — only if an authorized, read-only API is confirmed (Phase 1);
* ``postgres/``     — read-only role, named repository functions, row limits;
* ``external_api/`` — fixed endpoint allowlist, no caller-supplied URLs;
* ``mock/``         — synthetic data; the default for development, tests and the
                      public demonstration.

Creating empty sub-packages before there is an implementation would be
speculation; they appear with their first real adapter.
"""
