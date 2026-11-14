"""Tool registry: the allowlist of things the model may ask for.

A tool is a *declaration*, not a function pointer floating in prompt text. Each one
carries its own argument schema, the scopes it needs, and its timeout, so the executor
never has to guess and the model never sees a tool the caller could not use.

Two rules encoded here:

* **Names are unique and validated at startup.** A duplicate silently shadows the
  first registration, and the failure would look like a confused model.
* **``specs_for(context)`` filters by scope.** A tool the caller lacks the scope for is
  not offered at all, rather than offered and then refused — fewer wasted rounds, and
  the model cannot be talked into asking for it.

Tool arguments are Pydantic models with ``extra="forbid"``. That is a security
property, not tidiness: it is what makes ``get_my_policy_count(user_id="someone")`` a
validation error instead of an accepted extra field.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.domain.models.auth import AuthContext
from app.domain.models.tools import ToolSpec

logger = logging.getLogger(__name__)

#: Signature of a tool implementation: verified context + validated arguments.
ToolHandler = Callable[[AuthContext, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """One registered tool."""

    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler
    #: Optional output schema. When set, a handler result that does not match is a
    #: defect worth surfacing rather than forwarding to the model.
    result_model: type[BaseModel] | None = None
    #: Scopes the caller must hold. Empty today: every authenticated user may read
    #: their own data. Roles that read *other* people's data will set this.
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    timeout_seconds: float = 10.0

    def spec(self) -> ToolSpec:
        """Project into the provider-facing declaration."""
        schema = self.arguments_model.model_json_schema()
        # The provider expects a bare object schema; `title` is Pydantic noise here.
        schema.pop("title", None)
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=schema,
            required_scopes=self.required_scopes,
            timeout_seconds=self.timeout_seconds,
        )


class ToolRegistry:
    """Immutable allowlist of tools."""

    def __init__(self, tools: Iterable[ToolDefinition]) -> None:
        registered: dict[str, ToolDefinition] = {}
        for tool in tools:
            if not tool.name or not tool.name.strip():
                raise ValueError("A tool must have a name.")
            if not tool.description.strip():
                # An undescribed tool is one the model will misuse.
                raise ValueError(f"Tool {tool.name!r} must have a description.")
            if tool.name in registered:
                raise ValueError(f"Duplicate tool name: {tool.name!r}")
            if tool.timeout_seconds <= 0:
                raise ValueError(f"Tool {tool.name!r} must have a positive timeout.")
            registered[tool.name] = tool

        self._tools = registered
        logger.info(
            "tool registry initialised with %d tool(s): %s",
            len(registered),
            ", ".join(sorted(registered)) or "none",
        )

    def get(self, name: str) -> ToolDefinition | None:
        """Look up by name. ``None`` means "not allowed", never "try anything"."""
        return self._tools.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs_for(self, context: AuthContext) -> tuple[ToolSpec, ...]:
        """Only the tools this caller is allowed to use."""
        return tuple(
            tool.spec() for tool in self._tools.values() if context.has_scopes(tool.required_scopes)
        )

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools
