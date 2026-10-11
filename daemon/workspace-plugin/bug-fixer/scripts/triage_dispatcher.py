#!/usr/bin/env python3
"""
triage_dispatcher.py — Lightweight pre-check for the triage sidecar.

Runs hourly. Checks if there are any untriaged issues (missing a
classification label) OR any T2 issues with pending maintainer approval
that need transitioning to T1. Only spawns an LLM triage conversation
if there's actual work to do.

No-op conditions (exit silently):
  - No config found
  - No open issues missing a classification label AND no pending T2 approvals
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from liveness import agentapi_is_reachable, record_dispatch
from injection_scanner import InjectionScanner, CachedInjectionScanner

CLASSIFICATION_LABELS = {
    "bug-fixer:engineer:needs-fix",
    "bug-fixer:human:needs-approach-review",
    "bug-fixer:human:input-needed",
    "bug-fixer:human:project-candidate",
    "bug-fixer:human:required",
}

# Prefixes used by the triage agent's approach review comments.
# If the last comment on a T2 issue does NOT start with one of these,
# a human has replied and the issue may be ready for T2→T1 transition.
BOT_COMMENT_PREFIXES = (
    "[BUG-FIXER]",
    "[BUG-FIXER-TRIAGE]",
    "[ANTIGRAVITY]",
    "[CONTEXT]",
)


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"TRIAGE_DISPATCH: {ts} — {msg}", flush=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent

# Ensure agentapi is on PATH — sidecar scheduler may not inherit user's shell PATH
AGENTAPI_DIR = Path.home() / ".gemini" / "antigravity" / "bin"
if AGENTAPI_DIR.is_dir():
    os.environ["PATH"] = str(AGENTAPI_DIR) + os.pathsep + os.environ.get("PATH", "")

if not os.environ.get("ANTIGRAVITY_PROJECT_ID"):
    log("FATAL: ANTIGRAVITY_PROJECT_ID not set — agentapi will fail with "
        "'project_id is required when providing project_env_config'. "
        "Re-run install.sh from within an Antigravity session.")
    sys.exit(1)

# Workspace config resolution:
# 1. BUG_FIXER_CONFIG env var (set by sidecar stubs — absolute, most reliable)
# 2. Relative path from plugin dir: ../bug-fixer-loop-workspace/config.json
_config_env = os.environ.get("BUG_FIXER_CONFIG", "")
_config_relative = PLUGIN_DIR / "../bug-fixer-loop-workspace/config.json"

if _config_env and Path(_config_env).exists():
    WORKSPACE_CONFIG = Path(_config_env)
elif _config_relative.exists():
    WORKSPACE_CONFIG = _config_relative
else:
    log("No workspace config found. Run install.sh first. Exiting.")
    sys.exit(0)

CONFIG_FILE = WORKSPACE_CONFIG.resolve()

with open(CONFIG_FILE) as f:
    config = json.load(f)

TARGET_REPO = config["target_repo"]
SKILL_PATH = PLUGIN_DIR / "skills/bug-fixer-triage/SKILL.md"
SAFE_INGEST = PLUGIN_DIR / "scripts/safe_ingest.py"

_state_env = os.environ.get("BUG_FIXER_STATE_DIR")
if _state_env:
    STATE_DIR = Path(_state_env)
elif CONFIG_FILE:
    STATE_DIR = CONFIG_FILE.parent / "state"
else:
    STATE_DIR = Path.cwd()
STATE_DIR.mkdir(parents=True, exist_ok=True)
_TRIAGE_STATE_DIR = STATE_DIR

TRIAGE_COOLDOWN_FILE = STATE_DIR / "triage_cooldown.json"
TRIAGE_FAILURE_FILE = STATE_DIR / "triage_failure_count"
MAX_TRIAGE_FAILURES = 3
TRIAGE_COOLDOWN_SECONDS = 3600
CLAIMS_DIR = STATE_DIR / "claims"
DISPATCHES_DIR = STATE_DIR / "dispatches"

# Circuit breaker check
if TRIAGE_COOLDOWN_FILE.exists():
    try:
        cooldown = json.loads(TRIAGE_COOLDOWN_FILE.read_text())
        if time.time() < cooldown.get("expires_at", 0):
            log(f"TRIAGE_EXIT: Cooling down until {cooldown.get('expires_at_iso', 'unknown')}")
            sys.exit(0)
        TRIAGE_COOLDOWN_FILE.unlink(missing_ok=True)
        TRIAGE_FAILURE_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        TRIAGE_COOLDOWN_FILE.unlink(missing_ok=True)

if not agentapi_is_reachable():
    log("TRIAGE_EXIT: agentapi is unreachable — skipping this cycle.")
    sys.exit(0)

log(f"Checking {TARGET_REPO} for untriaged issues")


# ---------------------------------------------------------------------------
# Check for untriaged issues
# ---------------------------------------------------------------------------

r = run([sys.executable, str(SAFE_INGEST), "issue-list",
         "--repo", TARGET_REPO, "--state", "open",
         "--limit", "200",
         "--json", "number,title,body,labels,comments"])

try:
    issues = json.loads(r.stdout)
except json.JSONDecodeError:
    log(f"Failed to parse issue list: {r.stderr}")
    sys.exit(0)

# Issues with any of these labels are already handled — skip them
SKIP_LABELS = CLASSIFICATION_LABELS | {
    "bug-fixer:engineer:in-progress",
}

# Find issues missing ALL classification/status labels
untriaged = []
for issue in issues:
    label_names = {l.get("name", "") for l in issue.get("labels", [])}
    # Always skip if a classification label or bug-fixer:engineer:in-progress is present
    if label_names & SKIP_LABELS:
        continue
    # Skip bug-fixer:architect:awaiting-review ONLY if a classification label is also present
    # (if classification label was removed, re-triage is needed)
    if "bug-fixer:architect:awaiting-review" in label_names and label_names & CLASSIFICATION_LABELS:
        continue
    untriaged.append(issue)

# ---------------------------------------------------------------------------
# Injection Scan
# ---------------------------------------------------------------------------
injection_config = config.get("scanning", {}).get("injection", {})
scanner = CachedInjectionScanner(InjectionScanner(injection_config))

safe_untriaged = []
for issue in untriaged:
    text = f"{issue.get('title', '')} {issue.get('body', '')}"
    result = scanner.scan(text)
    if result.is_injection:
        log(f"INJECTION DETECTED on #{issue['number']}: "
            f"{result.provider} confidence={result.confidence}")
        r_edit = run(["gh", "issue", "edit", str(issue["number"]),
             "--add-label", "security:prompt-injection"])
        if r_edit.returncode != 0:
            log(f"Failed to apply security label to #{issue['number']}: {r_edit.stderr}")
    else:
        safe_untriaged.append(issue)
untriaged = safe_untriaged

# ---------------------------------------------------------------------------
# Check for T2 issues with pending maintainer approval (approach reviews)
# ---------------------------------------------------------------------------
# The triage skill's Step 4 handles T2→T1 transitions when a maintainer
# replies "approved" on a needs-approach-review issue. But that logic only
# runs if the triage agent is spawned. Without this check, approved T2
# issues are a dead-end: the dispatcher sees them as "already classified"
# and never invokes the agent.
#
# Heuristic: if the last comment on a needs-approach-review issue is NOT
# from the bot, a human has replied and the issue may need transitioning.
# False positives are cheap (one extra triage spawn); the skill evaluates
# the actual approval semantics.

pending_approvals = []
for issue in issues:
    label_names = {l.get("name", "") for l in issue.get("labels", [])}
    if "bug-fixer:human:needs-approach-review" not in label_names:
        continue
    comments = issue.get("comments", [])
    if not comments:
        continue
    last_body = (comments[-1].get("body") or "").lstrip()
    if not any(last_body.startswith(prefix) for prefix in BOT_COMMENT_PREFIXES):
        pending_approvals.append(issue)

if pending_approvals:
    approval_nums = ", ".join(f"#{i['number']}" for i in pending_approvals)
    log(f"{len(pending_approvals)} T2 issue(s) with pending approval response: {approval_nums}")

if not untriaged and not pending_approvals:
    log("No untriaged issues and no pending T2 approvals. Exiting.")
    sys.exit(0)

if untriaged:
    issue_nums = ", ".join(f"#{i['number']}" for i in untriaged[:10])
    log(f"{len(untriaged)} untriaged issue(s) found: {issue_nums}")


# ---------------------------------------------------------------------------
# Spawn triage conversation
# ---------------------------------------------------------------------------

# Build the prompt with both work streams
prompt_parts = [
    f"Run the bug-fixer-triage skill for repository {TARGET_REPO}.",
    "",
    f"Read the skill at: {SKILL_PATH}",
    f"Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.",
    f"Configuration is at: {CONFIG_FILE}",
]

if untriaged:
    issue_nums = ", ".join(f"#{i['number']}" for i in untriaged[:10])
    prompt_parts.append("")
    prompt_parts.append(f"There are {len(untriaged)} untriaged issue(s) to classify: {issue_nums}")

if pending_approvals:
    approval_nums = ", ".join(f"#{i['number']}" for i in pending_approvals)
    prompt_parts.append("")
    prompt_parts.append(
        f"There are {len(pending_approvals)} T2 issue(s) with pending maintainer "
        f"approval that may need T2→T1 transition (Step 4 of the skill): {approval_nums}"
    )

total_work = len(untriaged) + len(pending_approvals)
title = f"Triage: {total_work} issue(s) to process"
prompt = "\n".join(prompt_parts)

model = config.get("triage_model", "flash")
r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={title}", prompt])

try:
    result = json.loads(r.stdout)
    conv_id = result["response"]["newConversation"]["conversationId"]
    log(f"Spawned triage conversation {conv_id}")
    record_dispatch(DISPATCHES_DIR, conv_id, "triage", timeout_minutes=30)
except (json.JSONDecodeError, KeyError):
    log(f"Failed to spawn triage conversation: {r.stderr or r.stdout}")

# Circuit breaker: track spawn failure
if r.returncode != 0 or 'conversationId' not in r.stdout:
    try:
        count = int(TRIAGE_FAILURE_FILE.read_text().strip()) + 1 if TRIAGE_FAILURE_FILE.exists() else 1
    except (ValueError, OSError):
        count = 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TRIAGE_FAILURE_FILE.write_text(str(count))
    if count >= MAX_TRIAGE_FAILURES:
        expires = time.time() + TRIAGE_COOLDOWN_SECONDS
        TRIAGE_COOLDOWN_FILE.write_text(json.dumps({
            "reason": f"{count} consecutive triage spawn failures",
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires,
            "expires_at_iso": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        }))
        log(f"Triage circuit breaker tripped after {count} failures. Cooling down for {TRIAGE_COOLDOWN_SECONDS}s.")
else:
    TRIAGE_FAILURE_FILE.unlink(missing_ok=True)
