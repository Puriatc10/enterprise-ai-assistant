"""Enterprise AI Assistant — backend application package.

Layering (see docs/architecture/ARCHITECTURE.md):

    api/             HTTP edge: routing, request/response mapping, error envelope
    core/            settings and the composition root (dependency wiring)
    domain/          models and ports; imports no framework and performs no I/O
    application/     orchestration, conversation services, tool execution
    infrastructure/  adapters: LLM provider, data connectors, persistence
    security/        authentication context and authorization enforcement

Dependencies point inward: api -> application -> domain, with infrastructure
implementing domain ports and being injected at the edge.
"""

__version__ = "0.1.0"
