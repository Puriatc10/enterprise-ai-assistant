"""Contracts the Phase 1 spike relies on.

The spike's local tool executor is a stand-in for the Phase 3 executor, so its
rejection behaviour is worth pinning now: if it silently accepted an unknown tool
or a malformed argument, the spike would "pass" for the wrong reason and Phase 3
would inherit a hole.

Note this file imports no `litellm`: the LLM import lives inside `main()`, which is
what keeps the suite hermetic.
"""

from __future__ import annotations

from scripts.phase1_spike import CASES, SYNTHETIC_POLICIES, execute_tool


def test_count_tool_returns_synthetic_count() -> None:
    result = execute_tool("get_my_policy_count", {})

    assert result["status"] == "ok"
    assert result["data"]["count"] == len(SYNTHETIC_POLICIES)
    assert result["data"]["active_count"] == 2


def test_count_tool_rejects_unexpected_arguments() -> None:
    """The model must not be able to pass a user id into a user-scoped tool."""
    result = execute_tool("get_my_policy_count", {"user_id": "someone-else"})

    assert result["status"] == "invalid_request"


def test_policy_status_returns_the_synthetic_policy() -> None:
    result = execute_tool("get_policy_status", {"policy_id": "PL-100232"})

    assert result["status"] == "ok"
    assert result["data"]["type"] == "شخص ثالث"


def test_policy_status_hides_unknown_ids() -> None:
    """An id the caller does not own is 'not found', never 'forbidden'."""
    result = execute_tool("get_policy_status", {"policy_id": "PL-999999"})

    assert result["status"] == "not_found"
    assert "PL-999999" not in str(result.get("data"))


def test_policy_status_requires_an_id() -> None:
    assert execute_tool("get_policy_status", {})["status"] == "invalid_request"
    assert execute_tool("get_policy_status", {"policy_id": "  "})["status"] == "invalid_request"


def test_unknown_tool_is_rejected() -> None:
    result = execute_tool("execute_sql", {"query": "select * from policies"})

    assert result["status"] == "invalid_request"
    assert "execute_sql" in result["message"]


def test_every_case_expects_a_known_tool_or_none() -> None:
    known = {"get_my_policy_count", "get_policy_status", None}
    assert all(case.expect_tool in known for case in CASES)
    assert any(case.expect_tool is None for case in CASES), "a no-tool control case is required"
