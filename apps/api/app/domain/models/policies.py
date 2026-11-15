"""The application's own policy vocabulary.

Connectors speak in this shape, not in theirs. `Projection` lives in the domain layer
because it is a *contract*, not an implementation detail: every connector — mock,
Postgres, REST — must produce exactly these fields, and the tool layer above does not
care which one answered.

The allowlist is the security-relevant part. A connector that returns a customer's
national id, address or phone number would push that data straight into a model prompt,
so the projection drops anything not named here rather than trusting each adapter to
remember.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Fields a customer may see, and therefore the only ones a model may see.
PUBLIC_POLICY_FIELDS: tuple[str, ...] = (
    "policy_id",
    "type",
    "status",
    "insurer",
    "start_date",
    "end_date",
    "premium_toman",
)

#: Persian status/type strings used by the synthetic dataset.
STATUS_ACTIVE = "فعال"
STATUS_EXPIRED = "منقضی"


def project_policy(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a connector row to the public field set.

    Missing fields are omitted rather than filled with ``None``: a model reading
    ``"status": null`` tends to invent an explanation, while a missing key makes it
    say it does not have that detail.
    """
    return {field: row[field] for field in PUBLIC_POLICY_FIELDS if field in row}


def project_policies(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [project_policy(row) for row in rows]
