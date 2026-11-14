"""Mock policy connector — the MVP's primary data source (ADR-0002).

Didar24 has no authorized API, so the demonstration runs on synthetic data. What
makes this adapter more than a stub is that it keeps the *contract* honest:

* It implements the real ``DataConnectorPort`` — same operations, same status
  taxonomy, same "never raise, return a typed result" rule — so a future real adapter
  is a swap, not a rewrite.
* Each caller gets a **deterministic portfolio derived from their verified subject**.
  Two consequences that matter: every demo user sees plausible data, and an id
  belonging to someone else genuinely does not resolve, so ownership bugs surface here
  instead of in production.
* It is not a security control, and says so. Scoping is enforced by the repository and
  RLS; this adapter is data, not authority.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.domain.interfaces.connector import (
    ConnectorHealth,
    ConnectorResult,
    ConnectorStatus,
    FetchSpec,
)
from app.domain.models.auth import AuthContext

logger = logging.getLogger(__name__)

#: Operations this connector answers. Anything else is refused rather than guessed.
ALLOWED_OPERATIONS = frozenset({"list_policies", "count_policies", "policy_by_id"})

_POLICY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "type": "درمان تکمیلی",
        "status": "فعال",
        "insurer": "بیمه دی",
        "start_date": "1404/03/01",
        "end_date": "1405/03/01",
        "premium_toman": 4_800_000,
    },
    {
        "type": "شخص ثالث",
        "status": "فعال",
        "insurer": "بیمه دی",
        "start_date": "1404/07/15",
        "end_date": "1405/07/15",
        "premium_toman": 12_500_000,
    },
    {
        "type": "آتش‌سوزی",
        "status": "منقضی",
        "insurer": "بیمه دی",
        "start_date": "1403/02/10",
        "end_date": "1404/02/10",
        "premium_toman": 2_100_000,
    },
)

#: Fields safe to hand to a model: no national id, no address, no contact details.
_PUBLIC_FIELDS = (
    "policy_id",
    "type",
    "status",
    "insurer",
    "start_date",
    "end_date",
    "premium_toman",
)


def _subject_token(subject: str) -> str:
    """Short, stable, non-identifying token derived from the subject."""
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()[:6].upper()


def portfolio_for(subject: str) -> list[dict[str, Any]]:
    """The synthetic portfolio for one subject.

    Deterministic: the same user always sees the same policies, so a demo can be
    repeated and a test can assert exact ids.
    """
    token = _subject_token(subject)
    return [
        {"policy_id": f"POL-{token}-{index}", **template}
        for index, template in enumerate(_POLICY_TEMPLATES, start=1)
    ]


def _project(policy: dict[str, Any]) -> dict[str, Any]:
    return {field: policy[field] for field in _PUBLIC_FIELDS if field in policy}


class MockPolicyConnector:
    """Synthetic, per-subject policy data behind ``DataConnectorPort``."""

    name = "mock-policies"

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(reachable=True, detail="synthetic data source")

    async def fetch(self, spec: FetchSpec, context: AuthContext) -> ConnectorResult:
        if spec.operation not in ALLOWED_OPERATIONS:
            logger.warning("mock connector refused unknown operation=%r", spec.operation)
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE,
                message="Unsupported operation.",
            )

        policies = portfolio_for(context.subject)

        if spec.operation == "list_policies":
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={"policies": [_project(policy) for policy in policies]},
            )

        if spec.operation == "count_policies":
            return ConnectorResult(
                status=ConnectorStatus.OK,
                data={
                    "count": len(policies),
                    "active_count": sum(1 for policy in policies if policy["status"] == "فعال"),
                },
            )

        # policy_by_id: an id the caller does not own simply does not exist for them.
        policy_id = spec.params.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id.strip():
            return ConnectorResult(
                status=ConnectorStatus.INVALID_RESPONSE,
                message="policy_id is required.",
            )

        for policy in policies:
            if policy["policy_id"] == policy_id.strip():
                return ConnectorResult(status=ConnectorStatus.OK, data=_project(policy))

        return ConnectorResult(
            status=ConnectorStatus.NOT_FOUND,
            message="No such policy for this user.",
        )
