#!/usr/bin/env python3
"""
trajectory_dispatcher.py — Lightweight pre-check for the trajectory review sidecar.

Runs daily at 18:00. Checks if there are any bug-fixer transcripts
from the last 24 hours. Only spawns an LLM review conversation if
there's activity to review.

No-op conditions (exit silently):
  - No config found
  - No bug-fixer transcripts in the last 24 hours
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"TRAJECTORY_DISPATCH: {ts} — {msg}", flush=True)


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
SKILL_PATH = PLUGIN_DIR / "skills/bug-fixer-trajectory-review/SKILL.md"
SAFE_INGEST = PLUGIN_DIR / "scripts/safe_ingest.py"

# Workspace tmp directory — scratch space for spawned review conversations
WORKSPACE_TMP_DIR = CONFIG_FILE.parent / "tmp"
WORKSPACE_TMP_DIR.mkdir(parents=True, exist_ok=True)

# Purge stale tmp files (>12h old)
_tmp_cutoff = datetime.now().timestamp() - 43200  # 12 hours
_purged = 0
for _f in WORKSPACE_TMP_DIR.iterdir():
    try:
        if _f.stat().st_mtime < _tmp_cutoff:
            if _f.is_file():
                _f.unlink()
            elif _f.is_dir():
                import shutil
                shutil.rmtree(_f, ignore_errors=True)
            _purged += 1
    except OSError:
        continue
if _purged:
    log(f"Purged {_purged} stale tmp file(s) older than 12h.")

from liveness import agentapi_is_reachable
if not agentapi_is_reachable():
    log("TRAJECTORY_EXIT: agentapi is unreachable — skipping this cycle.")
    sys.exit(0)

log(f"Checking for recent bug-fixer activity in {TARGET_REPO}")


# ---------------------------------------------------------------------------
# Check for recent transcripts
# ---------------------------------------------------------------------------

brain_dir = Path.home() / ".gemini/antigravity/brain"
cutoff = datetime.now().timestamp() - 86400  # 24 hours ago

recent_transcripts = []
if brain_dir.exists():
    for conv_dir in brain_dir.iterdir():
        if not conv_dir.is_dir():
            continue
        transcript = conv_dir / ".system_generated/logs/transcript.jsonl"
        if not transcript.exists():
            continue
        if transcript.stat().st_mtime < cutoff:
            continue

        # Check if this is a bug-fixer conversation (check first few lines)
        try:
            with open(transcript) as f:
                head = f.read(2000)
            if any(kw in head for kw in [
                "bug-fixer-engineering", "bug-fixer-triage",
                "Fix #", "Triage:", "bug-fixer:engineer:needs-fix",
                "engineering_dispatcher", "triage_dispatcher",
            ]):
                recent_transcripts.append(str(transcript))
        except OSError:
            continue

# Also check the sidecar logs
sidecar_log_dir = Path.home() / ".gemini/antigravity/sidecar_data"
recent_sidecar_logs = []
for sidecar_name in ["bug-fixer-engineering-loop", "bug-fixer-triage-loop"]:
    log_file = sidecar_log_dir / sidecar_name / "logs/sidecar.log"
    if log_file.exists() and log_file.stat().st_mtime > cutoff:
        recent_sidecar_logs.append(str(log_file))

total_activity = len(recent_transcripts) + len(recent_sidecar_logs)

if total_activity == 0:
    log("No bug-fixer activity in last 24h. Exiting.")
    sys.exit(0)

log(f"Found {len(recent_transcripts)} transcript(s) and {len(recent_sidecar_logs)} sidecar log(s) from last 24h.")


# ---------------------------------------------------------------------------
# Check for obvious problems worth reviewing
# ---------------------------------------------------------------------------

# Quick check: any stale in-progress issues?
r = run(["gh", "issue", "list", "--repo", TARGET_REPO,
         "--label", "bug-fixer:engineer:in-progress", "--state", "open",
         "--json", "number,title", "--jq", "length"])
in_progress_count = int(r.stdout.strip() or "0")

# Quick check: any recent PRs from the bug-fixer?
r = run(["gh", "pr", "list", "--repo", TARGET_REPO,
         "--label", "bug-fixer:engineer:pr", "--state", "all",
         "--json", "number", "--jq", "length"])
recent_prs = int(r.stdout.strip() or "0")

# Build context summary for the LLM
context_lines = [
    f"- {len(recent_transcripts)} bug-fixer conversation(s) in last 24h",
    f"- {in_progress_count} issue(s) currently in-progress",
    f"- {recent_prs} bug-fixer PR(s) total",
]
context_summary = "\n".join(context_lines)


# ---------------------------------------------------------------------------
# Spawn trajectory review conversation
# ---------------------------------------------------------------------------

title = "Trajectory Review: daily check-in"
prompt = f"""Run the bug-fixer-trajectory-review skill for repository {TARGET_REPO}.

Read the skill at: {SKILL_PATH}
Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.
Configuration is at: {CONFIG_FILE}
Workspace tmp directory for scratch files: {WORKSPACE_TMP_DIR}

Activity summary for the last 24 hours:
{context_summary}

Recent bug-fixer transcripts to analyze:
{chr(10).join(recent_transcripts[:20])}

Sidecar logs:
{chr(10).join(recent_sidecar_logs)}

Review these for recurring failure patterns, stale claims, and quality trends."""

model = config.get("trajectory_model", "flash")
r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={title}", prompt])

try:
    result = json.loads(r.stdout)
    conv_id = result["response"]["newConversation"]["conversationId"]
    log(f"Spawned trajectory review conversation {conv_id}")
except (json.JSONDecodeError, KeyError):
    log(f"Failed to spawn trajectory review: {r.stderr or r.stdout}")
    sys.exit(1)
