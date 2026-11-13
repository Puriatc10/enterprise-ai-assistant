"""Conversation and message services (Phase 2).

Owns the turn lifecycle: load conversation for the authenticated subject, append
the user message, persist the assistant message and the tool-audit records.
Tenant scoping is applied here (via the repository port), never in a router.
"""
