#!/usr/bin/env python3
"""
safe_post.py — Egress proxy for GitHub write operations.

All GitHub write commands (issue/PR create, edit, comment, review) MUST
go through this proxy instead of raw `gh`. The dispatchers route body-carrying
writes through this proxy.
Raw `gh` writes from agent skills still go through the regex gate in
pre_tool_gate.py. Migrating skills and denying raw `gh` writes is
tracked on issue #22.

Architecture:
  Agent/Dispatcher -> safe_post.py (scans body, then calls gh)

The proxy:
1. Receives the final, resolved body/title text as CLI args
2. Scans for configured leak patterns
3. Calls `gh` only if the scan passes
4. Returns the gh exit code and output

This eliminates all shell-expansion bypass vectors (heredocs, $(cmd),
variable expansion, @file references) because the proxy sees the
actual content, not a shell command string.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Derive config path from this script's location (deterministic)
SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
WORKSPACE_DIR = PLUGIN_DIR / ".agents" / "plugins" / "bug-fixer-loop-workspace"
DEFAULT_CONFIG = WORKSPACE_DIR / "config.json"


# Patterns enforced regardless of config: a config can add patterns, never
# remove these. Kept identical to pre_tool_gate.BUILTIN_LEAK_PATTERNS (tested).
BUILTIN_LEAK_PATTERNS = (
    r"ya29\.[A-Za-z0-9_-]+",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{22,}",
)

APPROVAL_TEXT_REGEX = re.compile(r"^\s*/?(?:approved?|lgtm)\s*[.!]*\s*$", re.IGNORECASE)


def _candidate_configs() -> list[Path]:
    """Every workspace config this invocation could legitimately belong to."""
    candidates: list[Path] = []
    env_path = os.environ.get("BUG_FIXER_CONFIG", "")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(DEFAULT_CONFIG)
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if top:
            workspace = Path(top) / ".agents" / "plugins" / "bug-fixer-loop-workspace"
            candidates.append(workspace / "config.json")
    except (OSError, subprocess.SubprocessError):
        pass
    unique: list[Path] = []
    for c in candidates:
        resolved = c.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def load_leak_patterns() -> list[str]:
    """Built-in floor plus the patterns of every reachable workspace config.

    BUG_FIXER_CONFIG is set by the sidecar for dispatchers, but anyone running
    the proxy can set it, so a config can only ever add patterns: the floor
    and the plugin/target workspace configs are always included. Fails closed
    when no config can be read, or when a config exists but is unreadable.
    """
    patterns = list(BUILTIN_LEAK_PATTERNS)
    loaded_any = False
    for config_path in _candidate_configs():
        if not config_path.is_file():
            continue
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            log(f"WARNING: config unreadable at {config_path}, failing closed")
            return [".*"]
        if not isinstance(config, dict):
            log(f"WARNING: config at {config_path} is not an object, failing closed")
            return [".*"]
        loaded_any = True
        scanning = config.get("scanning", {})
        secrets = scanning.get("secrets", {}) if isinstance(scanning, dict) else {}
        extra = secrets.get("patterns") if isinstance(secrets, dict) else None
        if extra is None:
            extra = config.get("leak_patterns", [])
        for pat in extra or []:
            if isinstance(pat, str) and pat and pat not in patterns:
                patterns.append(pat)

    if not loaded_any:
        log("WARNING: no config found, failing closed")
        return [".*"]  # Matches everything — blocks all posts
    return patterns


def scan_text(text: str, patterns: list[str]) -> list[str]:
    """Scan text for leak patterns. Returns list of matched rule names."""
    violations = []
    for pat in patterns:
        try:
            if re.search(pat, text):
                violations.append(pat)
        except re.error:
            pass
    return violations


def log(msg: str):
    """Log to stderr (visible in sidecar logs, not in agent output)."""
    print(f"SAFE_POST: {msg}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Egress proxy for GitHub write operations"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- issue-create ---
    p_ic = subparsers.add_parser("issue-create")
    p_ic.add_argument("--repo", required=True)
    p_ic.add_argument("--title", required=True)
    p_ic.add_argument("--body", default="")
    p_ic.add_argument("--body-file", dest="body_file")
    p_ic.add_argument("--label", action="append", default=[])

    # --- issue-edit ---
    p_ie = subparsers.add_parser("issue-edit")
    p_ie.add_argument("number", type=str)
    p_ie.add_argument("--repo", required=True)
    p_ie.add_argument("--title")
    p_ie.add_argument("--body")
    p_ie.add_argument("--body-file", dest="body_file")
    p_ie.add_argument("--add-label", action="append", default=[])
    p_ie.add_argument("--remove-label", action="append", default=[])

    # --- pr-comment ---
    p_pc = subparsers.add_parser("pr-comment")
    p_pc.add_argument("number", type=str)
    p_pc.add_argument("--repo", required=True)
    p_pc.add_argument("--body", default="")
    p_pc.add_argument("--body-file", dest="body_file")

    # --- issue-comment ---
    p_icom = subparsers.add_parser("issue-comment")
    p_icom.add_argument("number", type=str)
    p_icom.add_argument("--repo", required=True)
    p_icom.add_argument("--body", default="")
    p_icom.add_argument("--body-file", dest="body_file")

    # --- pr-edit ---
    p_pe = subparsers.add_parser("pr-edit")
    p_pe.add_argument("number", type=str)
    p_pe.add_argument("--repo", required=True)
    p_pe.add_argument("--title")
    p_pe.add_argument("--body")
    p_pe.add_argument("--body-file", dest="body_file")
    p_pe.add_argument("--add-label", action="append", default=[])
    p_pe.add_argument("--remove-label", action="append", default=[])

    # --- pr-review ---
    p_pr = subparsers.add_parser("pr-review")
    p_pr.add_argument("number", type=str)
    p_pr.add_argument("--repo", required=True)
    p_pr.add_argument("--body", default="")
    p_pr.add_argument("--body-file", dest="body_file")
    p_pr.add_argument("--approve", action="store_true")
    p_pr.add_argument("--request-changes", action="store_true")
    p_pr.add_argument("--comment", action="store_true", dest="review_comment")

    # --- label-only (no body, fast path for dispatchers) ---
    p_lo = subparsers.add_parser("label-only",
        help="Add/remove labels without body content (no scanning needed)")
    p_lo.add_argument("type", choices=["issue", "pr"])
    p_lo.add_argument("number", type=str)
    p_lo.add_argument("--repo", required=True)
    p_lo.add_argument("--add-label", action="append", default=[])
    p_lo.add_argument("--remove-label", action="append", default=[])

    args = parser.parse_args()
    patterns = load_leak_patterns()

    # Resolve body from --body-file if provided
    body = getattr(args, "body", None) or ""
    body_file = getattr(args, "body_file", None)
    if body_file:
        if body_file == "-":
            log("DENIED: --body-file - (stdin) is unscannable")
            sys.exit(1)
        try:
            body = Path(body_file).read_text()
        except (FileNotFoundError, PermissionError) as exc:
            log(f"DENIED: cannot read body file {body_file}: {exc}")
            sys.exit(1)

    title = getattr(args, "title", None) or ""

    # Scan all outbound text: title, body and every other argument (labels, repo, …)
    other_args = [str(v) for v in vars(args).values() if isinstance(v, str)]
    other_args += [str(x) for v in vars(args).values() if isinstance(v, list) for x in v]
    scannable = "\n".join([title, body, *other_args]).strip()
    if scannable and patterns:
        violations = scan_text(scannable, patterns)
        if violations:
            log(f"DENIED: body/title contains {len(violations)} leak pattern match(es)")
            for v in violations:
                log(f"  matched: {v[:40]}...")
            sys.exit(1)

    # Gate B parity: block self-approval, approval injection, and label abuse
    if args.command == "pr-review":
        if getattr(args, "approve", False) or getattr(args, "request_changes", False):
            log("DENIED: self-approval / request-changes is prohibited")
            sys.exit(1)

    # Block approval injection in any body
    if body:
        if APPROVAL_TEXT_REGEX.match(body.strip()):
            log("DENIED: posting fake maintainer approval comment is prohibited")
            sys.exit(1)

    # Block removing security labels via label-only
    if args.command == "label-only":
        for label in getattr(args, "remove_label", []):
            if label.startswith("security:"):
                log(f"DENIED: removing security label '{label}' is prohibited")
                sys.exit(1)

    # Also block removing security labels via issue-edit / pr-edit
    if args.command in ("issue-edit", "pr-edit"):
        for label in getattr(args, "remove_label", []):
            if label.startswith("security:"):
                log(f"DENIED: removing security label '{label}' is prohibited")
                sys.exit(1)

    # Build the gh command
    if args.command == "label-only":
        # No body, no scanning needed — just label management
        gh_cmd = ["gh", args.type, "edit", args.number, "--repo", args.repo]
        for label in args.add_label:
            gh_cmd.extend(["--add-label", label])
        for label in args.remove_label:
            gh_cmd.extend(["--remove-label", label])

    elif args.command == "issue-create":
        gh_cmd = ["gh", "issue", "create", "--repo", args.repo,
                  "--title", title, "--body", body]
        for label in args.label:
            gh_cmd.extend(["--label", label])

    elif args.command == "issue-edit":
        gh_cmd = ["gh", "issue", "edit", args.number, "--repo", args.repo]
        if title:
            gh_cmd.extend(["--title", title])
        if body:
            gh_cmd.extend(["--body", body])
        for label in args.add_label:
            gh_cmd.extend(["--add-label", label])
        for label in args.remove_label:
            gh_cmd.extend(["--remove-label", label])

    elif args.command == "pr-comment":
        gh_cmd = ["gh", "pr", "comment", args.number, "--repo", args.repo,
                  "--body", body]

    elif args.command == "issue-comment":
        gh_cmd = ["gh", "issue", "comment", args.number, "--repo", args.repo,
                  "--body", body]

    elif args.command == "pr-edit":
        gh_cmd = ["gh", "pr", "edit", args.number, "--repo", args.repo]
        if title:
            gh_cmd.extend(["--title", title])
        if body:
            gh_cmd.extend(["--body", body])
        for label in args.add_label:
            gh_cmd.extend(["--add-label", label])
        for label in args.remove_label:
            gh_cmd.extend(["--remove-label", label])

    elif args.command == "pr-review":
        gh_cmd = ["gh", "pr", "review", args.number, "--repo", args.repo,
                  "--body", body]
        if args.approve:
            gh_cmd.append("--approve")
        elif args.request_changes:
            gh_cmd.append("--request-changes")
        elif args.review_comment:
            gh_cmd.append("--comment")

    else:
        log(f"Unknown command: {args.command}")
        sys.exit(1)

    # Execute
    result = subprocess.run(gh_cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
