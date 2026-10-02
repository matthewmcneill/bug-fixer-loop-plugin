#!/usr/bin/env python3
"""PreToolUse gate for read_url_content — blocks GitHub issue/PR URL fetches.

Redirects agents to use safe_ingest.py instead of fetching GitHub pages
directly (which fails on private repos with 404).
"""
from __future__ import annotations

import json
import re
import sys

GITHUB_ISSUE_PR_URL = re.compile(
    r"https?://github\.com/[^/]+/[^/]+/(?:issues|pull)/\d+"
)

REASON = (
    "Use safe_ingest.py instead of fetching GitHub URLs: Direct HTTP fetches of "
    "GitHub issue/PR pages are prohibited (private repos return 404). Extract the "
    "issue/PR number from the URL and run: "
    "`python3 scripts/safe_ingest.py issue-view <number>` (or `pr-view <number>` "
    "for PRs). Run from the project root."
)


def main() -> int:
    if sys.stdin.isatty():
        print("GitHub URL gate for read_url_content PreToolUse hook.")
        return 0

    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow"}))
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"decision": "allow"}))
        return 0

    # Extract the URL from the tool call args
    url = ""
    tool_call = payload.get("toolCall", {})
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    url = str(args.get("Url") or args.get("url") or "").strip()

    if url and GITHUB_ISSUE_PR_URL.search(url):
        print(json.dumps({"decision": "deny", "reason": REASON}))
    else:
        print(json.dumps({"decision": "allow"}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
