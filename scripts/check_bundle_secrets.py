#!/usr/bin/env python3
"""Fail the build if a credential reaches the client bundle.

Scans for credential *shapes*, not words. The phrase "service_role" appearing in UI copy
is advice; a working key is a leak — so the JWT case is decided by decoding the payload
and looking at the `role` claim, and anon keys (public by design) pass.

Exit codes: 0 clean, 1 leak found, 2 nothing to scan.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

DIST = Path(sys.argv[1] if len(sys.argv) > 1 else "apps/web/dist")

#: (pattern, human label). "JWT" is special-cased: only role=service_role is a leak.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "OpenAI-style API key"),
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "Google API key"),
    (re.compile(r"sb_secret_[A-Za-z0-9_-]{10,}"), "Supabase secret key"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "JWT",
    ),
]


def jwt_role(token: str) -> str | None:
    """The `role` claim, or None if this is not a decodable JWT."""
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, IndexError, TypeError):
        return None
    return claims.get("role") if isinstance(claims, dict) else None


def main() -> int:
    if not DIST.is_dir():
        print(f"error: {DIST} does not exist — build the client first", file=sys.stderr)
        return 2

    files = [path for path in DIST.rglob("*") if path.is_file()]
    if not files:
        print(f"error: no files under {DIST}", file=sys.stderr)
        return 2

    findings: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, label in PATTERNS:
            for match in pattern.finditer(text):
                token = match.group(0)
                if label == "JWT":
                    # Anon/publishable keys are expected in the bundle; a service_role
                    # token would bypass row-level security and must never ship.
                    if jwt_role(token) != "service_role":
                        continue
                    label = "JWT with role=service_role"
                findings.add(f"{path.relative_to(DIST)}: {label}")

    for finding in sorted(findings):
        print(f"::error::{finding}")
    if findings:
        return 1

    print(f"bundle clean: {len(files)} file(s) scanned, no credential shapes found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
