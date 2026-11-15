"""Tool registry, executor and the mock connector.

The executor is the security boundary of the whole product, so its refusals are tested
before its successes: an unknown tool, a smuggled identity field, a missing scope, a
timeout, and a handler that raises. If any of those reach a data source, the design
claim ("the model proposes, the backend authorizes") is false.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict

from app.application.tools.executor import ToolExecutor, ToolRefusal, hash_arguments
from app.application.tools.policies import PolicyCountResult, build_policy_registry
from app.application.tools.registry import ToolDefinition, ToolRegistry
from app.domain.models.auth import AuthContext
from app.domain.models.tools import ToolCall, ToolStatus
from app.infrastructure.connectors.mock.policies import MockPolicyConnector, portfolio_for

ALICE = AuthContext(subject="alice-id", tenant_id="tenant-a")
BOB = AuthContext(subject="bob-id", tenant_id="tenant-a")
ALICE_SCOPED = AuthContext(subject="alice-id", tenant_id="tenant-a", scopes=frozenset({"admin"}))


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _tool(name: str = "t", *, scopes: frozenset[str] = frozenset(), **kwargs) -> ToolDefinition:
    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        return {"ok": 1}

    return ToolDefinition(
        name=name,
        description=kwargs.pop("description", "does a thing"),
        arguments_model=kwargs.pop("arguments_model", NoArgs),
        handler=kwargs.pop("handler", handler),
        required_scopes=scopes,
        **kwargs,
    )


def test_duplicate_tool_names_are_rejected_at_startup() -> None:
    """A silent shadow would look like a confused model later."""
    with pytest.raises(ValueError, match="Duplicate"):
        ToolRegistry([_tool("same"), _tool("same")])


def test_tools_must_be_described_and_have_a_timeout() -> None:
    with pytest.raises(ValueError, match="description"):
        ToolRegistry([_tool("x", description="  ")])
    with pytest.raises(ValueError, match="positive timeout"):
        ToolRegistry([_tool("y", timeout_seconds=0)])


def test_spec_projects_a_clean_json_schema() -> None:
    spec = _tool("count").spec()

    assert spec.name == "count"
    assert spec.parameters["type"] == "object"
    assert "title" not in spec.parameters


def test_specs_for_hides_tools_the_caller_cannot_use() -> None:
    registry = ToolRegistry([_tool("open"), _tool("privileged", scopes=frozenset({"admin"}))])

    assert [spec.name for spec in registry.specs_for(ALICE)] == ["open"]
    assert {spec.name for spec in registry.specs_for(ALICE_SCOPED)} == {"open", "privileged"}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _executor(registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(registry=registry)


async def test_unknown_tool_is_refused_without_calling_anything() -> None:
    registry = ToolRegistry([_tool("count")])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="execute_sql", arguments={"query": "select 1"}),
        context=ALICE,
    )

    assert record.result.status is ToolStatus.INVALID_REQUEST
    assert "execute_sql" not in (record.result.message or "")


async def test_identity_shaped_argument_is_a_validation_error() -> None:
    """`extra="forbid"` is what stops the model from naming another user."""
    called: list[NoArgs] = []

    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        called.append(arguments)
        return {"ok": 1}

    registry = ToolRegistry([_tool("count", handler=handler)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="count", arguments={"user_id": "someone-else"}),
        context=ALICE,
    )

    assert record.result.status is ToolStatus.INVALID_REQUEST
    assert "user_id" in (record.result.message or "")
    assert called == [], "the handler must not run with smuggled arguments"


async def test_missing_required_argument_is_refused() -> None:
    registry = ToolRegistry([_tool("status", arguments_model=IdArgs)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="status", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.INVALID_REQUEST


async def test_missing_scope_is_forbidden() -> None:
    called: list[str] = []

    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        called.append(context.subject)
        return {"ok": 1}

    registry = ToolRegistry([_tool("admin-tool", scopes=frozenset({"admin"}), handler=handler)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="admin-tool", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.FORBIDDEN
    assert called == []


async def test_handler_refusal_maps_to_its_own_status() -> None:
    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        raise ToolRefusal(ToolStatus.NOT_FOUND, "No such policy for this user.")

    registry = ToolRegistry([_tool("lookup", handler=handler)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="lookup", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.NOT_FOUND
    assert "No such policy" in (record.result.message or "")


async def test_handler_crash_becomes_an_upstream_error_not_a_500() -> None:
    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        raise RuntimeError("connection reset by peer at 10.0.0.7")

    registry = ToolRegistry([_tool("lookup", handler=handler)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="lookup", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.UPSTREAM_ERROR
    # Internal detail must not reach the model, which would repeat it to the user.
    assert "10.0.0.7" not in (record.result.message or "")


async def test_slow_handler_times_out() -> None:
    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, int]:
        await asyncio.sleep(0.2)
        return {"ok": 1}

    registry = ToolRegistry([_tool("slow", handler=handler, timeout_seconds=0.01)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="slow", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.TIMEOUT


async def test_payload_that_breaks_the_result_model_is_rejected() -> None:
    async def handler(context: AuthContext, arguments: NoArgs) -> dict[str, str]:
        return {"count": "not a number"}

    registry = ToolRegistry([_tool("count", handler=handler, result_model=PolicyCountResult)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="count", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.INVALID_RESPONSE


async def test_successful_call_records_latency_and_an_argument_hash() -> None:
    registry = ToolRegistry([_tool("count", arguments_model=IdArgs)])

    record = await _executor(registry).execute(
        call=ToolCall(id="c1", name="count", arguments={"policy_id": "POL-1"}),
        context=ALICE,
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {"ok": 1}
    assert record.latency_ms >= 0
    assert len(record.arguments_hash) == 64


def test_argument_hash_is_stable_and_does_not_contain_the_arguments() -> None:
    first = hash_arguments({"policy_id": "POL-SECRET-1"})
    second = hash_arguments({"policy_id": "POL-SECRET-1"})

    assert first == second
    assert "POL-SECRET-1" not in first


# ---------------------------------------------------------------------------
# Composition across domains
# ---------------------------------------------------------------------------


def test_compose_merges_domains_and_still_rejects_duplicates() -> None:
    """Adding a domain must not be a chance to shadow an existing tool."""
    policies = ToolRegistry([_tool("get_my_policy_count")])
    support = ToolRegistry([_tool("support_count_my_tickets"), _tool("support_list_my_tickets")])

    merged = policies.compose(support)

    assert merged.names == (
        "get_my_policy_count",
        "support_count_my_tickets",
        "support_list_my_tickets",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        policies.compose(ToolRegistry([_tool("get_my_policy_count")]))


# ---------------------------------------------------------------------------
# Mock connector, through the real tool registry
# ---------------------------------------------------------------------------


def _policy_registry() -> ToolRegistry:
    return build_policy_registry(MockPolicyConnector())


async def test_policy_count_tool_answers_for_the_caller() -> None:
    record = await ToolExecutor(registry=_policy_registry()).execute(
        call=ToolCall(id="c1", name="get_my_policy_count", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {"count": 3, "active_count": 2}


async def test_policy_list_tool_exposes_no_owner_fields() -> None:
    record = await ToolExecutor(registry=_policy_registry()).execute(
        call=ToolCall(id="c1", name="get_my_policies", arguments={}), context=ALICE
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data is not None
    policies = record.result.data["policies"]
    assert len(policies) == 3
    assert all("user_id" not in policy for policy in policies)
    assert all(policy["insurer"] == "بیمه دی" for policy in policies)


async def test_policy_status_tool_finds_an_owned_policy() -> None:
    own_id = portfolio_for(ALICE.subject)[0]["policy_id"]

    record = await ToolExecutor(registry=_policy_registry()).execute(
        call=ToolCall(id="c1", name="get_policy_status", arguments={"policy_id": own_id}),
        context=ALICE,
    )

    assert record.result.status is ToolStatus.OK
    assert record.result.data == {"policy_id": own_id, "status": "فعال"}


async def test_another_users_policy_id_is_not_found() -> None:
    """Ids are derived from the subject, so a foreign id genuinely does not resolve."""
    bob_id = portfolio_for(BOB.subject)[0]["policy_id"]

    record = await ToolExecutor(registry=_policy_registry()).execute(
        call=ToolCall(id="c1", name="get_policy_details", arguments={"policy_id": bob_id}),
        context=ALICE,
    )

    assert record.result.status is ToolStatus.NOT_FOUND


async def test_portfolio_is_deterministic_per_subject() -> None:
    assert portfolio_for("alice-id") == portfolio_for("alice-id")
    assert portfolio_for("alice-id")[0]["policy_id"] != portfolio_for("bob-id")[0]["policy_id"]
