"""Phase 1 spike: does Gemini 3.5 Flash-Lite call tools correctly for Persian questions?

This is a *validation script*, not product code. It answers four questions that
Phase 2 depends on, with evidence:

1. Does the model propose a well-formed tool call for a Persian question?
2. Does it survive a second round once the tool result is supplied?
3. Does it correctly *decline* to call a tool for chit-chat?
4. Does it extract a typed argument (a policy id) from Persian text?

Design rules:

* Runs with no API key (prints a skip and exits 0) so CI stays hermetic and tests
  never require network — see the `llm-spike` workflow for the live, manual run.
* Never prints the API key. Never sends anything but synthetic data.
* Prints a JSON trace per case so the numbers in the report are copied, not recalled.

Usage:
    cd apps/api
    uv sync --extra llm
    uv run --extra llm python scripts/phase1_spike.py                 # all cases
    uv run --extra llm python scripts/phase1_spike.py --case 1         # one case
    uv run --extra llm python scripts/phase1_spike.py \
        --model openrouter/google/gemma-4-31b-it:free                  # another provider
    uv run --extra llm python scripts/phase1_spike.py --question "چند بیمه‌نامه دارم؟"

The model is `--model`, then `LLM_MODEL`, then the built-in default — so switching
provider is a flag, never an edit. The credential is looked up by provider prefix
(`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MAX_TOOL_ROUNDS = 3
TIMEOUT_SECONDS = 45.0

#: Built-in default when neither `--model` nor `LLM_MODEL` is set.
#: Matches Settings.llm_model: free, tool-capable, verified in Phase 1.
BUILTIN_DEFAULT_MODEL = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"

#: Which environment variable carries the credential for each LiteLLM prefix.
#: Keeping this table here is the spike-sized version of the adapter's mapping.
KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "gemini/": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter/": ("OPENROUTER_API_KEY",),
    "groq/": ("GROQ_API_KEY",),
    "openai/": ("OPENAI_API_KEY",),
}

# ---------------------------------------------------------------------------
# Synthetic data only. These are fabricated policies; no real customer data may
# pass through a free-tier model (docs/architecture/SECURITY.md §6).
# ---------------------------------------------------------------------------
SYNTHETIC_POLICIES: list[dict[str, Any]] = [
    {
        "policy_id": "PL-100231",
        "type": "درمان تکمیلی",
        "status": "فعال",
        "insurer": "بیمه دی",
        "start_date": "1404/03/01",
        "end_date": "1405/03/01",
    },
    {
        "policy_id": "PL-100232",
        "type": "شخص ثالث",
        "status": "فعال",
        "insurer": "بیمه دی",
        "start_date": "1404/07/15",
        "end_date": "1405/07/15",
    },
    {
        "policy_id": "PL-100233",
        "type": "آتش‌سوزی",
        "status": "منقضی",
        "insurer": "بیمه دی",
        "start_date": "1403/02/10",
        "end_date": "1404/02/10",
    },
]

# Tool declarations exactly as the orchestrator would register them: narrow,
# user-scoped, no identity parameters (identity comes from AuthContext).
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_my_policy_count",
            "description": (
                "Return how many insurance policies the authenticated user currently has. "
                "Use for any question about the number or count of policies. "
                "Takes no arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_policy_status",
            "description": (
                "Return the status of one of the authenticated user's policies, by its id. "
                "Use when the user asks about a specific policy."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_id": {
                        "type": "string",
                        "description": "Policy identifier, e.g. PL-100231.",
                    }
                },
                "required": ["policy_id"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class Case:
    """One validation scenario."""

    name: str
    question: str
    expect_tool: str | None  # None => the model must not call a tool
    expect_args: dict[str, Any] | None = None


CASES: list[Case] = [
    Case(
        name="count-persian",
        question="چند بیمه‌نامه دارم؟",
        expect_tool="get_my_policy_count",
    ),
    Case(
        name="specific-policy-persian",
        question="وضعیت بیمه‌نامه با شماره PL-100232 چیه؟",
        expect_tool="get_policy_status",
        expect_args={"policy_id": "PL-100232"},
    ),
    Case(
        name="chit-chat-no-tool",
        question="سلام! امروز حالت چطوره؟",
        expect_tool=None,
    ),
    Case(
        name="count-informal-persian",
        question="من کلا چند تا بیمه نامه فعال دارم؟",
        expect_tool="get_my_policy_count",
    ),
]

SYSTEM_PROMPT = (
    "You are an insurance assistant for Persian-speaking users. "
    "Answer only from tool results. Never invent policy data, counts, dates or amounts. "
    "If a tool returns no data, say so plainly. "
    "You never decide permissions and never accept a user id: the backend supplies identity. "
    "Reply in Persian, concisely."
)


@dataclass
class CaseResult:
    """Observed behaviour for one case."""

    case: str
    question: str
    expected_tool: str | None
    called_tools: list[str] = field(default_factory=list)
    arguments: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    rounds: int = 0
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_passed: bool = False
    args_passed: bool = True
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.tool_passed and self.args_passed and self.error is None


def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Stand-in for the Phase 3 tool executor, with the same contract.

    Unknown tools and malformed arguments are rejected rather than "best
    effort", and the result is data the model may read — never an instruction.
    """
    if name == "get_my_policy_count":
        if arguments:
            return {"status": "invalid_request", "message": "This tool takes no arguments."}
        return {
            "status": "ok",
            "data": {
                "count": len(SYNTHETIC_POLICIES),
                "active_count": sum(1 for p in SYNTHETIC_POLICIES if p["status"] == "فعال"),
            },
        }

    if name == "get_policy_status":
        policy_id = arguments.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id.strip():
            return {"status": "invalid_request", "message": "policy_id is required."}
        for policy in SYNTHETIC_POLICIES:
            if policy["policy_id"] == policy_id.strip():
                return {"status": "ok", "data": policy}
        # Never reveal whether an id exists for another user: not found, full stop.
        return {"status": "not_found", "message": "No such policy for this user."}

    return {"status": "invalid_request", "message": f"Unknown tool: {name}"}


def load_env_files() -> None:
    """Read `.env` files the way the app does, so the documented workflow works.

    The script is documented as "put GEMINI_API_KEY in apps/api/.env", so it must
    actually read that file: developers should not have to `export` a key by hand.
    Repository-root `.env` is checked too, matching `Settings.env_file`.
    Existing environment variables always win, so CI/runner values are never
    overwritten by a local file.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv ships with pydantic-settings; be defensive.
        return

    api_dir = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[3]
    for candidate in (api_dir / ".env", repo_root / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def resolve_model(explicit: str | None) -> str:
    """`--model` beats `LLM_MODEL`, which beats the built-in default."""
    return explicit or os.getenv("LLM_MODEL") or BUILTIN_DEFAULT_MODEL


def required_key_names(model: str) -> tuple[str, ...]:
    """Environment variables that could hold this model's credential."""
    for prefix, names in KEY_ENV_VARS.items():
        if model.startswith(prefix):
            return names
    return tuple(name for names in KEY_ENV_VARS.values() for name in names)


def require_credentials(model: str) -> str | None:
    """Return the env var name holding a key if one is set, else None (and say why)."""
    for name in required_key_names(model):
        if os.getenv(name):
            return name
    expected = " or ".join(required_key_names(model))
    print(
        f"SKIP: no credential found for {model}.\n"
        f"      Set {expected} in apps/api/.env (or the environment) and re-run.\n"
        "      Nothing was sent. Dispatch the `llm-spike` workflow to run it on the runner.",
        file=sys.stderr,
    )
    return None


def run_case(completion: Any, case: Case, model: str) -> CaseResult:
    """Drive one question through the tool loop and record what happened."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": case.question},
    ]
    result = CaseResult(
        case=case.name,
        question=case.question,
        expected_tool=case.expect_tool,
    )
    started = time.perf_counter()

    try:
        for round_index in range(1, MAX_TOOL_ROUNDS + 1):
            # No `temperature`: Gemini 3.x rejects sampling params outright
            # (litellm raises UnsupportedParamsError before the request is sent).
            # Phase 2's adapter must treat temperature as an optional, provider-
            # mapped parameter rather than a universally supported one.
            response = completion(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                timeout=TIMEOUT_SECONDS,
            )
            result.rounds = round_index
            usage = getattr(response, "usage", None)
            if usage is not None:
                result.prompt_tokens = getattr(usage, "prompt_tokens", None)
                result.completion_tokens = getattr(usage, "completion_tokens", None)

            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                result.answer = message.content or ""
                break

            # Record and answer every requested call, then loop for the final text.
            messages.append(message.model_dump())
            for call in tool_calls:
                name = call.function.name
                raw_args = call.function.arguments or "{}"
                try:
                    arguments = (
                        json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    )
                except json.JSONDecodeError:
                    arguments = {"__unparsable__": raw_args}
                result.called_tools.append(name)
                result.arguments.append(arguments)

                tool_output = execute_tool(name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": name,
                        "content": json.dumps(tool_output, ensure_ascii=False),
                    }
                )
        else:
            result.answer = "(tool round limit reached without a final answer)"

        result.latency_ms = int((time.perf_counter() - started) * 1000)

        first_called = result.called_tools[0] if result.called_tools else None
        result.tool_passed = first_called == case.expect_tool
        if case.expect_args and result.arguments:
            result.args_passed = all(
                result.arguments[0].get(key) == value for key, value in case.expect_args.items()
            )

    except Exception as exc:  # noqa: BLE001 - a spike reports, it does not crash
        message = f"{type(exc).__name__}: {exc}"
        lowered = message.lower()
        # Turn the two setup failures everyone hits into an instruction, not a wall
        # of provider JSON.
        if "free tier is not available" in lowered or "billing" in lowered:
            message = (
                "PROVIDER SETUP: this Google project has no Gemini free tier. Enable "
                "billing at https://aistudio.google.com/billing for the paid tier, or "
                "point LLM_MODEL at another provider. Provider said: " + message[:220]
            )
        elif "location is not supported" in lowered:
            message = (
                "PROVIDER SETUP: the Gemini API refuses this client's region. Run the "
                "llm-spike workflow on GitHub Actions instead. Provider said: " + message[:220]
            )
        result.error = message
        result.latency_ms = int((time.perf_counter() - started) * 1000)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 tool-calling spike (synthetic data only)."
    )
    parser.add_argument("--case", type=int, help="Run a single case by 1-based index.")
    parser.add_argument("--question", help="Ad-hoc question; runs one case with no expectation.")
    parser.add_argument("--json", action="store_true", help="Emit only the JSON trace.")
    parser.add_argument(
        "--model",
        help="LiteLLM model id, e.g. openrouter/google/gemma-4-31b-it:free. "
        "Overrides LLM_MODEL from the environment/.env.",
    )
    args = parser.parse_args()

    load_env_files()

    model = resolve_model(args.model)
    if require_credentials(model) is None:
        return 0

    try:
        import litellm  # type: ignore[import-not-found]
        from litellm import completion  # type: ignore[import-not-found]

        # Retries belong to our adapter with an explicit policy, never to the SDK's
        # defaults: an auth or billing error must fail in seconds, not in minutes.
        litellm.num_retries = 0
    except ImportError:
        print(
            "ERROR: litellm is not installed. Run: uv sync --extra llm",
            file=sys.stderr,
        )
        return 1

    selected: list[Case]
    if args.question:
        selected = [Case(name="adhoc", question=args.question, expect_tool=None)]
    elif args.case:
        if not 1 <= args.case <= len(CASES):
            print(f"ERROR: --case must be 1..{len(CASES)}", file=sys.stderr)
            return 1
        selected = [CASES[args.case - 1]]
    else:
        selected = CASES

    results = [run_case(completion, case, model) for case in selected]

    if not args.json:
        print(f"\nmodel: {model}")
        print(f"cases: {len(results)}\n")
        for item in results:
            mark = "PASS" if item.passed else ("SKIP" if item.expected_tool is None else "FAIL")
            print(f"[{mark}] {item.case}: {item.question}")
            print(f"        expected tool: {item.expected_tool or '(none)'}")
            print(f"        called:        {item.called_tools or '(none)'}")
            if item.arguments:
                print(f"        arguments:     {json.dumps(item.arguments, ensure_ascii=False)}")
            print(f"        rounds: {item.rounds}  latency: {item.latency_ms} ms")
            if item.answer:
                print(f"        answer: {item.answer.strip()[:220]}")
            if item.error:
                print(f"        ERROR: {item.error}")
            print()

    print("JSON_TRACE " + json.dumps([asdict(r) for r in results], ensure_ascii=False))

    scored = [r for r in results if r.expected_tool is not None]
    failures = [r for r in scored if not r.passed]
    print(f"\nsummary: {len(scored) - len(failures)}/{len(scored)} tool expectations met")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
