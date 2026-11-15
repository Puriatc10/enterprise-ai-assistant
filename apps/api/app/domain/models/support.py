"""The application's own support-ticket vocabulary.

As with policies, the projection lives in the domain layer because it is a *contract*:
every support connector — SQLite today, a helpdesk API later — must produce exactly
these fields, so the tool layer above does not care which one answered.

`customer_ref` is the security-relevant column. It is the subject→customer mapping the
connector filters on, so returning it would (a) hand a model an internal identifier
and (b) tell a caller *whose* ticket they are reading. It is therefore not in the
allowlist, and `project_ticket` drops anything not named here rather than trusting each
adapter to remember.

A note on the name `subject`: on a ticket it means the ticket's own title (what the
tool result calls `subject`, matching the column), not the authenticated user. The
user's identity is `customer_ref` in the table and `AuthContext.subject` in code, and
the two are never mixed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Fields a customer may see in a list, and therefore the only ones a model may see.
PUBLIC_TICKET_FIELDS: tuple[str, ...] = (
    "ticket_id",
    "subject",
    "status",
    "priority",
    "created_at",
    "updated_at",
)

#: The detail view adds the body. Nothing else is permitted, ever.
PUBLIC_TICKET_DETAIL_FIELDS: tuple[str, ...] = (*PUBLIC_TICKET_FIELDS, "description")

#: Ticket lifecycle values. A closed ticket is history; only `open` counts as work
#: waiting on support, which is why the count tool reports the two numbers separately.
STATUS_OPEN = "open"
STATUS_PENDING = "pending"
STATUS_CLOSED = "closed"
TICKET_STATUSES: frozenset[str] = frozenset({STATUS_OPEN, STATUS_PENDING, STATUS_CLOSED})

PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
TICKET_PRIORITIES: frozenset[str] = frozenset({PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH})


def project_ticket(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a connector row to the public field set.

    Missing fields are omitted rather than filled with ``None``: a model reading
    ``"priority": null`` tends to invent an explanation, while a missing key makes it
    say it does not have that detail.
    """
    return {field: row[field] for field in PUBLIC_TICKET_FIELDS if field in row}


def project_tickets(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [project_ticket(row) for row in rows]


def project_ticket_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """The single-ticket projection: the list fields plus the description."""
    return {field: row[field] for field in PUBLIC_TICKET_DETAIL_FIELDS if field in row}
