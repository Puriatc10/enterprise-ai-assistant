"""Persistence adapters (Phase 2): conversations, messages, tool audit records.

Supabase/PostgreSQL is the application store and is logically separate from every
connector data source: different credentials, no cross-boundary joins. Every query
is filtered by tenant from ``AuthContext`` at this layer.
"""
