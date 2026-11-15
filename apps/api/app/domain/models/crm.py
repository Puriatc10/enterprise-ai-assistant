"""The application's own CRM vocabulary: accounts, deals, stages, projection.

Connectors speak in this shape, not in theirs — a Postgres row, a future REST payload and
the demo seed must all arrive at the tool layer as the same fields. ``Projection`` lives in
the domain layer because it is a *contract*: the allowlist is what keeps an internal id, an
``auth_subject`` or a future ``notes`` column out of a model prompt.

Two rules, both of which the tests pin:

* **Public fields only.** ``PUBLIC_DEAL_FIELDS`` / ``PUBLIC_ACCOUNT_FIELDS`` are the whole
  vocabulary. A connector that returns more loses the extra here rather than upstream.
* **"Open" is the complement of a closed list.** ``won`` and ``lost`` are closed; anything
  else — including a stage added later — counts as open pipeline. Writing it the other way
  round would silently drop new stages out of the pipeline total.

Timestamps are rendered as ISO strings on the way out. A ``datetime`` would reach the tool
result models as an object and then the audit record as a non-JSON value; the boundary is
here, where the domain decides what an account or deal looks like.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

#: Fields a customer may see about a deal, and therefore the only ones a model may see.
PUBLIC_DEAL_FIELDS: tuple[str, ...] = (
    "deal_id",
    "title",
    "stage",
    "amount_toman",
    "currency",
    "expected_close",
    "updated_at",
)

#: Fields a customer may see about their own account.
PUBLIC_ACCOUNT_FIELDS: tuple[str, ...] = (
    "name",
    "industry",
    "owner_name",
    "created_at",
)

#: Every stage the CRM has. Kept beside ``CLOSED_STAGES`` so the constraint in the
#: migration, the connector's filter and this vocabulary cannot drift apart silently.
DEAL_STAGES: tuple[str, ...] = ("lead", "qualified", "proposal", "won", "lost")

#: Stages that are no longer being worked. Everything else is open pipeline.
CLOSED_STAGES: tuple[str, ...] = ("won", "lost")

#: The only currency the demo dataset uses (Iranian toman).
DEFAULT_CURRENCY = "IRT"


def _text(value: Any) -> Any:
    """Render a date/timestamp as an ISO string; pass anything else through."""
    return value.isoformat() if isinstance(value, date) else value


def _toman(value: Any) -> int:
    """Coerce a toman amount to ``int``.

    ``bigint`` arrives as ``int``, but ``sum(bigint)`` and ``avg`` arrive as ``Decimal``.
    The tool schemas say integer, so the conversion happens once, here.
    """
    return int(value)


def project_deal(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a connector row to the public deal fields.

    Missing and ``null`` fields are omitted rather than sent as ``None``: a model reading
    ``"expected_close": null`` tends to invent an explanation, while a missing key makes it
    say it does not have that detail (the same rule as the policy projection).
    """
    projected = {
        field: _text(row[field])
        for field in PUBLIC_DEAL_FIELDS
        if field in row and row[field] is not None
    }
    if "amount_toman" in projected:
        projected["amount_toman"] = _toman(projected["amount_toman"])
    return projected


def project_deals(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [project_deal(row) for row in rows]


def project_account(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce a connector row to the public account fields."""
    return {
        field: _text(row[field])
        for field in PUBLIC_ACCOUNT_FIELDS
        if field in row and row[field] is not None
    }
