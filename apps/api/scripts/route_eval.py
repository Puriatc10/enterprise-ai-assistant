"""Measure tool-routing accuracy against the live model.

Four tools could be guessed. Eight across two domains cannot, so this asks a fixed set of
Persian questions, records which tool the model picked, and reports accuracy. It is not in
CI: it needs a key, it costs calls, and a free-tier model is not deterministic. Run it
before a demo and record the number.

Usage:
    cd apps/api && .venv/Scripts/python.exe scripts/route_eval.py
    ... --model openrouter/other:free --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.core.dependencies import build_tool_registry

SYSTEM = (
    "You are a Persian-speaking enterprise assistant. Use the provided tools to answer "
    "questions about the user's own data. If none of the tools can answer, say so."
)


@dataclass(frozen=True, slots=True)
class Case:
    question: str
    expected: frozenset[str]
    note: str

    @property
    def is_negative(self) -> bool:
        return not self.expected


CASES: tuple[Case, ...] = (
    Case("چند بیمه‌نامه دارم؟", frozenset({"get_my_policy_count"}), "count, insurance"),
    Case("بیمه‌نامه‌هایم را نشان بده", frozenset({"get_my_policies"}), "list, insurance"),
    Case(
        "وضعیت بیمه‌نامه POL-1A2B3C-1 چیست؟",
        frozenset({"get_policy_status"}),
        "status by id",
    ),
    Case("چند تیکت باز دارم؟", frozenset({"support_count_my_tickets"}), "count, support"),
    Case("تیکت‌های پشتیبانی من را لیست کن", frozenset({"support_list_my_tickets"}), "list, support"),
    Case("جزئیات تیکت TKT-1001 را بگو", frozenset({"support_get_ticket"}), "detail by id"),
    Case("چه داده‌هایی می‌توانی ببینی؟", frozenset({"list_my_data_sources"}), "capabilities"),
    Case("به چه اطلاعاتی دسترسی داری؟", frozenset({"list_my_data_sources"}), "capabilities"),
    Case("قیمت دلار امروز چند است؟", frozenset(), "not our data: must decline"),
    Case("سلام، حالت چطوره؟", frozenset(), "greeting: no tool"),
    Case(
        "چند بیمه‌نامه دارم و چند تیکت باز دارم؟",
        frozenset({"get_my_policy_count", "support_count_my_tickets"}),
        "cross-domain",
    ),
    Case(
        "خلاصه‌ای از بیمه‌ها و تیکت‌هایم بده",
        frozenset({"get_my_policies", "support_list_my_tickets"}),
        "cross-domain list",
    ),
)


def _specs(registry):
    """Provider payload for the registry's tools, straight from the domain specs."""
    from app.domain.models.auth import AuthContext

    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in registry.specs_for(AuthContext(subject="eval", tenant_id="eval"))
    ]


async def _ask(settings: Settings, tools: list[dict], question: str) -> tuple[set[str], str]:
    import litellm

    litellm.num_retries = 0
    response = await litellm.acompletion(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ],
        tools=tools,
        tool_choice="auto",
        timeout=settings.llm_timeout_seconds,
        num_retries=0,
    )
    message = response.choices[0].message
    calls = {call.function.name for call in (message.tool_calls or [])}
    return calls, (message.content or "")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="Override LLM_MODEL")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case (flakiness check)")
    parser.add_argument("--json", default=None, help="Write raw results to this path")
    args = parser.parse_args()

    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})
    if not settings.llm_configured:
        print("no credential configured for the provider; nothing to measure")
        return 2

    setup = build_tool_registry(settings)
    tools = _specs(setup.registry)
    print(f"model={settings.llm_model} domains={','.join(setup.domains)} tools={len(tools)}\n")

    rows: list[dict] = []
    passed = 0
    total = 0
    for case in CASES:
        for _run in range(args.repeat):
            total += 1
            try:
                calls, text = await _ask(settings, tools, case.question)
            except Exception as exc:  # noqa: BLE001 - a provider failure is a data point
                rows.append({"question": case.question, "error": type(exc).__name__})
                print(f"  ERROR {type(exc).__name__}: {case.question}")
                continue

            ok = case.expected <= calls if case.expected else not calls
            passed += 1 if ok else 0
            rows.append(
                {
                    "question": case.question,
                    "expected": sorted(case.expected),
                    "called": sorted(calls),
                    "ok": ok,
                    "note": case.note,
                    "text": text[:160],
                }
            )
            mark = "ok  " if ok else "MISS"
            print(f"  {mark} {case.question[:44]:<46} called={sorted(calls)}")

    accuracy = (passed / total * 100) if total else 0.0
    print(f"\naccuracy {passed}/{total} = {accuracy:.0f}%")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"raw results -> {args.json}")
    return 0 if accuracy >= 80 else 1


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(f"# run at {datetime.now(UTC).isoformat(timespec='seconds')}")
    raise SystemExit(asyncio.run(main()))
