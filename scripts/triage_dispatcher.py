#!/usr/bin/env python3
"""
triage_dispatcher.py — Lightweight pre-check for the triage sidecar.

Runs hourly. Checks if there are any untriaged issues (missing a
classification label). Only spawns an LLM triage conversation if
there's actual work to do.

No-op conditions (exit silently):
  - No config found
  - No open issues missing a classification label
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CLASSIFICATION_LABELS = {
    "auto-fixable",
    "needs-approach-review",
    "input-needed",
    "project-candidate",
    "human-required",
}


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

# Workspace config — the ONLY config location (plugin root is immutable)
WORKSPACE_CONFIG = PLUGIN_DIR / "../../bug-fixer-loop-workspace/config.json"

if not WORKSPACE_CONFIG.exists():
    log("No workspace config found. Run install.sh first. Exiting.")
    sys.exit(0)

CONFIG_FILE = WORKSPACE_CONFIG.resolve()

with open(CONFIG_FILE) as f:
    config = json.load(f)

TARGET_REPO = config["target_repo"]
SKILL_PATH = PLUGIN_DIR / "skills/bug-fixer-triage/SKILL.md"
SAFE_INGEST = PLUGIN_DIR / "scripts/safe_ingest.py"

# Derive state dir from config file location (standard layout)
_TRIAGE_STATE_DIR = CONFIG_FILE.parent

TRIAGE_COOLDOWN_FILE = _TRIAGE_STATE_DIR / "triage_cooldown.json"
TRIAGE_FAILURE_FILE = _TRIAGE_STATE_DIR / "triage_failure_count"
MAX_TRIAGE_FAILURES = 3
TRIAGE_COOLDOWN_SECONDS = 3600

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

log(f"Checking {TARGET_REPO} for untriaged issues")


# ---------------------------------------------------------------------------
# Check for untriaged issues
# ---------------------------------------------------------------------------

r = run([sys.executable, str(SAFE_INGEST), "issue-list",
         "--repo", TARGET_REPO, "--state", "open",
         "--json", "number,title,labels"])

try:
    issues = json.loads(r.stdout)
except json.JSONDecodeError:
    log(f"Failed to parse issue list: {r.stderr}")
    sys.exit(0)

# Issues with any of these labels are already handled — skip them
SKIP_LABELS = CLASSIFICATION_LABELS | {
    "status:in-progress",
    "status:awaiting-review",
}

# Find issues missing ALL classification/status labels
untriaged = []
for issue in issues:
    label_names = {l.get("name", "") for l in issue.get("labels", [])}
    if not label_names & SKIP_LABELS:
        untriaged.append(issue)

if not untriaged:
    log("No untriaged issues. Exiting.")
    sys.exit(0)

issue_nums = ", ".join(f"#{i['number']}" for i in untriaged[:10])
log(f"{len(untriaged)} untriaged issue(s) found: {issue_nums}")


# ---------------------------------------------------------------------------
# Spawn triage conversation
# ---------------------------------------------------------------------------

title = f"Triage: {len(untriaged)} new issue(s)"
prompt = f"""Run the bug-fixer-triage skill for repository {TARGET_REPO}.

Read the skill at: {SKILL_PATH}
Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.
Configuration is at: {CONFIG_FILE}

There are {len(untriaged)} untriaged issue(s) to classify: {issue_nums}"""

r = run(["agentapi", "new-conversation", f"--title={title}", prompt])

try:
    result = json.loads(r.stdout)
    conv_id = result["response"]["newConversation"]["conversationId"]
    log(f"Spawned triage conversation {conv_id}")
except (json.JSONDecodeError, KeyError):
    log(f"Failed to spawn triage conversation: {r.stderr or r.stdout}")

# Circuit breaker: track spawn failure
if r.returncode != 0 or 'conversationId' not in r.stdout:
    try:
        count = int(TRIAGE_FAILURE_FILE.read_text().strip()) + 1 if TRIAGE_FAILURE_FILE.exists() else 1
    except (ValueError, OSError):
        count = 1
    _TRIAGE_STATE_DIR.mkdir(parents=True, exist_ok=True)
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
