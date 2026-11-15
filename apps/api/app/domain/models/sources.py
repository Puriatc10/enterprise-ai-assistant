"""What the assistant can reach, as data rather than prose.

The model needs to be able to answer "what can you do?" without inventing domains, and
the UI may later show the same list. Keeping it a domain object means one source of truth
for both.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DomainDescriptor:
    """One enabled data domain.

    ``tools`` is filled from the domain's own registry at composition time, so a domain
    that gains a tool does not need to update a hand-written list here — which is how
    capability lists rot.
    """

    id: str
    label: str
    description: str
    tools: tuple[str, ...]
