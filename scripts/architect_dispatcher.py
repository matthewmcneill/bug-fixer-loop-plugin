#!/usr/bin/env python3
"""
architect_dispatcher.py — Polls GitHub for Draft PRs needing architect review.

Runs on a cron schedule via the sidecar. Does NOT use an LLM.
Checks for draft bug-fixer PRs and only spawns an Architect review
conversation when there's a draft to review.

No-op conditions (exit silently):
  - No config found
  - No eligible draft PRs
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_REVIEW_ROUNDS = 5


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"ARCHITECT_DISPATCH: {ts} — {msg}", flush=True)


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def gh(*args) -> subprocess.CompletedProcess:
    return run(["gh", *args])


if not os.environ.get("ANTIGRAVITY_PROJECT_ID"):
    log("FATAL: ANTIGRAVITY_PROJECT_ID not set — agentapi will fail. "
        "Re-run install.sh from within an Antigravity session.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent

# Ensure agentapi is on PATH
AGENTAPI_DIR = Path.home() / ".gemini" / "antigravity" / "bin"
if AGENTAPI_DIR.is_dir():
    os.environ["PATH"] = str(AGENTAPI_DIR) + os.pathsep + os.environ.get("PATH", "")

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
SKILL_PATH = PLUGIN_DIR / "skills/architect-review/SKILL.md"
SAFE_INGEST = PLUGIN_DIR / "scripts/safe_ingest.py"

# Derive repo dir from config file location (standard layout)
# <repo>/.agents/plugins/bug-fixer-loop-workspace/config.json
STATE_DIR = CONFIG_FILE.parent
REPO_DIR = STATE_DIR.parent.parent.parent

log(f"Polling PRs in {TARGET_REPO}")

# ---------------------------------------------------------------------------
# Polling Logic
# ---------------------------------------------------------------------------

r = gh("pr", "list", "--repo", TARGET_REPO, "--label", "bug-fixer",
       "--state", "open", "--draft",
       "--json", "number,title,labels,body")

if r.returncode != 0:
    log(f"Error fetching PRs: {r.stderr}")
    sys.exit(1)

try:
    prs = json.loads(r.stdout)
except json.JSONDecodeError:
    log("Failed to decode gh pr list output.")
    sys.exit(1)

if not prs:
    log("No draft bug-fixer PRs found. Exiting.")
    sys.exit(0)

spawned = 0

for pr in prs:
    labels = [label["name"] for label in pr.get("labels", [])]
    if "needs-iteration" in labels or "architect-approved" in labels:
        continue

    number = pr["number"]
    title = pr["title"]
    body = pr.get("body", "")

    # -----------------------------------------------------------------------
    # Max-iteration guard: count prior [ARCHITECT] review comments
    # -----------------------------------------------------------------------
    r_comments = gh("pr", "view", str(number), "--repo", TARGET_REPO,
                     "--comments", "--json", "comments")
    review_count = 0
    if r_comments.returncode == 0:
        try:
            comments_data = json.loads(r_comments.stdout)
            for c in comments_data.get("comments", []):
                if "[ARCHITECT]" in c.get("body", ""):
                    review_count += 1
        except (json.JSONDecodeError, KeyError):
            pass

    if review_count >= MAX_REVIEW_ROUNDS:
        log(f"PR #{number} has reached {review_count} architect review rounds "
            f"(max {MAX_REVIEW_ROUNDS}). Adding needs-human-intervention label.")
        gh("pr", "edit", str(number), "--repo", TARGET_REPO,
           "--add-label", "needs-human-intervention")
        continue

    log(f"Processing PR #{number}: {title} (review round {review_count + 1}/{MAX_REVIEW_ROUNDS})")

    # Check for existing architect conversation ID
    conv_id_match = re.search(r"<!-- agy_arch_conv_id:\s*([a-zA-Z0-9-]+)\s*-->", body)
    conv_id = conv_id_match.group(1) if conv_id_match else None

    # Fetch PR diff
    diff_r = gh("pr", "diff", str(number), "--repo", TARGET_REPO)
    if diff_r.returncode != 0:
        log(f"Error fetching diff for PR #{number}: {diff_r.stderr}")
        continue
    diff = diff_r.stdout

    prompt = f"""Run the architect-review skill on this PR.

Read the full architect-review skill instructions at: {SKILL_PATH}
Read config from: {CONFIG_FILE}
Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.
Work in: {REPO_DIR}

PR Number: {number}
Title: {title}
Review round: {review_count + 1} of {MAX_REVIEW_ROUNDS}

Diff:
```diff
{diff}
```"""

    spawn_new = True
    if conv_id:
        log(f"Resuming architect conversation {conv_id} for PR #{number}")
        msg_r = run(["agentapi", "message", "--conversation-id", conv_id, prompt])
        if msg_r.returncode == 0:
            spawn_new = False
        else:
            log(f"Failed to resume conversation {conv_id} (expired/deleted?). "
                f"Falling back to new conversation. Error: {msg_r.stderr or msg_r.stdout}")

    if spawn_new:
        log(f"Spawning new architect conversation for PR #{number}")
        conv_title = f"Architect Review #{number}"
        new_r = run(["agentapi", "new-conversation", f"--title={conv_title}", prompt])
        if new_r.returncode != 0:
            log(f"Failed to spawn new conversation for PR #{number}: "
                f"{new_r.stderr or new_r.stdout}")
            continue
        spawned += 1

log(f"Finished polling PRs. Spawned/resumed {spawned} review(s).")
