#!/usr/bin/env python3
"""
engineering_dispatcher.py — Lightweight control plane for the bug fixer loop.

Runs on a cron schedule via the sidecar. Does NOT use an LLM.
Checks for eligible work and only spawns LLM conversations
when there are actual bugs to fix.

Architecture:
  - This script: bash-free control plane (invisible in IDE)
  - agentapi new-conversation: spawns fix conversations (visible in IDE)
  - Each fix conversation handles ONE issue
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from liveness import (record_dispatch, sweep_dispatches,
                      transcript_age_seconds, transcript_path, is_stalled)


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"ENGINEERING_DISPATCH: {ts} — {msg}", flush=True)


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def gh(*args) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def inject_conv_tag(pr_number: int, tag_name: str, conv_id: str) -> bool:
    """Inject or update an HTML conversation-ID tag in a PR body.

    Ensures the PR body always has the correct tag, regardless of whether
    the LLM agent remembered to write it.  Idempotent — safe to call on
    every dispatch cycle.

    Args:
        pr_number: GitHub PR number.
        tag_name:  Tag key, e.g. 'agy_dev_conv_id' or 'agy_arch_conv_id'.
        conv_id:   Conversation ID to embed.

    Returns True on success, False on failure (logged, non-fatal).
    """
    try:
        r = gh("pr", "view", str(pr_number), "--repo", TARGET_REPO,
               "--json", "body", "--jq", ".body")
        if r.returncode != 0:
            log(f"inject_conv_tag: failed to read PR #{pr_number} body: {r.stderr}")
            return False

        body = r.stdout.rstrip()

        # Remove any existing tag with this name
        new_body = re.sub(
            rf'<!--\s*{re.escape(tag_name)}:\s*[a-zA-Z0-9-]+\s*-->\s*',
            '', body
        ).rstrip()

        # Append the tag
        new_body += f"\n<!-- {tag_name}: {conv_id} -->"

        gh("pr", "edit", str(pr_number), "--repo", TARGET_REPO,
           "--body", new_body)
        log(f"inject_conv_tag: wrote {tag_name}={conv_id[:8]}... to PR #{pr_number}")
        return True
    except Exception as e:
        log(f"inject_conv_tag: error updating PR #{pr_number}: {e}")
        return False


# PA-aligned complexity → model mapping (same table as the Principal Architect harness)
COMPLEXITY_MODEL_MAP = {
    "mechanical": "flash_lite",
    "standard": "flash",
    "architect": "pro",
}


if not os.environ.get("ANTIGRAVITY_PROJECT_ID"):
    log("FATAL: ANTIGRAVITY_PROJECT_ID not set — agentapi will fail with "
        "'project_id is required when providing project_env_config'. "
        "Re-run install.sh from within an Antigravity session.")
    sys.exit(1)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def file_mtime_age_seconds(path: Path) -> int | None:
    """Return seconds since file was last modified, or None if missing."""
    if not path.exists():
        return None
    return int(datetime.now().timestamp() - path.stat().st_mtime)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent

# Ensure agentapi is on PATH — sidecar scheduler may not inherit user's shell PATH
AGENTAPI_DIR = Path.home() / ".gemini" / "antigravity" / "bin"
if AGENTAPI_DIR.is_dir():
    os.environ["PATH"] = str(AGENTAPI_DIR) + os.pathsep + os.environ.get("PATH", "")

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
MAX_CONCURRENT = config.get("max_concurrent_prs", 4)
BRANCH_PREFIX = config.get("branch_prefix", "bugfix")
SKILL_PATH = PLUGIN_DIR / "skills/bug-fixer-engineering/SKILL.md"
SAFE_INGEST = PLUGIN_DIR / "scripts/safe_ingest.py"


def get_model_for_issue(issue: dict) -> str:
    """Read complexity label from triage, map to agentapi model tier.

    Uses the PA-aligned complexity mapping. Falls back to the
    engineering_model config key, then defaults to 'pro'.
    """
    for label in issue.get("labels", []):
        name = label.get("name", "")
        if name.startswith("complexity:"):
            tier = name.split(":", 1)[1]
            if tier in COMPLEXITY_MODEL_MAP:
                return COMPLEXITY_MODEL_MAP[tier]
    return config.get("engineering_model", config.get("tick_model", "pro"))

# Derive all paths from config file location (standard layout)
# <repo>/.agents/plugins/bug-fixer-loop-workspace/config.json
STATE_DIR = CONFIG_FILE.parent
REPO_DIR = STATE_DIR.parent.parent.parent
CLAIMS_DIR = STATE_DIR / "claims"
DISPATCHES_DIR = STATE_DIR / "dispatches"

COOLDOWN_FILE = STATE_DIR / "cooldown.json"
FAILURE_COUNT_FILE = STATE_DIR / "failure_count"
MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_SECONDS = 3600  # 1 hour

# Circuit breaker: check cooldown before doing any work
if COOLDOWN_FILE.exists():
    try:
        cooldown = json.loads(COOLDOWN_FILE.read_text())
        if time.time() < cooldown.get("expires_at", 0):
            log(f"ENGINEERING_EXIT: Cooling down until {cooldown.get('expires_at_iso', 'unknown')} — "
                f"reason: {cooldown.get('reason', 'unknown')}")
            sys.exit(0)
        # Cooldown expired, clean up
        COOLDOWN_FILE.unlink(missing_ok=True)
        FAILURE_COUNT_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        COOLDOWN_FILE.unlink(missing_ok=True)

log(f"Checking {TARGET_REPO} (max_concurrent={MAX_CONCURRENT})")


# ---------------------------------------------------------------------------
# Step 0: Sweep previous dispatches for stalled/completed conversations
# ---------------------------------------------------------------------------

recovered = sweep_dispatches(DISPATCHES_DIR, TARGET_REPO, log,
                             claims_dir=CLAIMS_DIR)
for r in recovered:
    log(f"Recovered: {r['type']} conv={r['conversation_id'][:8]}... "
        f"action={r.get('action', 'unknown')}")


# ---------------------------------------------------------------------------
# Step 1: Stale claim recovery (3-layer detection)
# ---------------------------------------------------------------------------

reattach_count = 0

if CLAIMS_DIR.exists():
    for lockfile in CLAIMS_DIR.glob("*.lock"):
        try:
            claim = json.loads(lockfile.read_text())
        except (json.JSONDecodeError, OSError):
            lockfile.unlink(missing_ok=True)
            continue

        issue_num = claim["issue"]
        conv_id = claim.get("conversation_id", "")

        # Get issue title for conversation naming
        r = gh("issue", "view", str(issue_num), "--repo", TARGET_REPO,
               "--json", "title", "--jq", ".title")
        issue_title = r.stdout.strip() or "Unknown"

        # --- Layer 1: Transcript-based liveness check ---
        # (Replaces old PID check — the PID in lock files is the dispatcher's
        # PID, not the agent's, so it's always dead by the next tick.)
        age = transcript_age_seconds(conv_id)

        if age is not None and age < 300 and not is_stalled(conv_id):
            log(f"Issue #{issue_num} — conv {conv_id[:8]}... active ({age}s ago), skipping.")
            continue

        # --- Check if PR was already opened (clean exit) ---
        r = gh("pr", "list", "--repo", TARGET_REPO,
               "--search", f"#{issue_num}",
               "--json", "number", "--jq", "length")
        if r.stdout.strip() not in ("", "0"):
            log(f"Issue #{issue_num} — PR exists, cleaning up lock.")
            lockfile.unlink(missing_ok=True)
            gh("issue", "edit", str(issue_num), "--repo", TARGET_REPO,
               "--remove-label", "status:in-progress")
            continue

        # --- ORPHANED: transcript stale, no PR opened ---
        log(f"Issue #{issue_num} — ORPHANED (conv {conv_id[:8]}... stale).")

        # Check for existing branch (durable code state)
        r = run(["git", "ls-remote", "origin", f"{BRANCH_PREFIX}/{issue_num}-*"],
                check=False)
        existing_branch = ""
        if r.stdout.strip():
            existing_branch = r.stdout.strip().split()[1].replace("refs/heads/", "")

        # Build reattach context
        reattach_lines = []
        if existing_branch:
            reattach_lines.append(
                f"REATTACH CONTEXT: Previous agent made progress on branch: {existing_branch}\n"
                f"Check it out and inspect the diff against main.")
        transcript = transcript_path(conv_id)
        if transcript.exists():
            reattach_lines.append(
                f"Previous agent transcript: {transcript}\n"
                f"Read the last 20 steps to understand what was tried and where it stopped.")
        if not reattach_lines:
            reattach_lines.append(
                "No previous progress found (no branch, no transcript). Start fresh from Step 6.")

        reattach_context = "\n".join(reattach_lines)

        prompt = f"""You are the bug-fixer agent. REATTACH to orphaned issue #{issue_num} in repository {TARGET_REPO}.

Issue title: {issue_title}

{reattach_context}

Read the full engineering skill instructions at: {SKILL_PATH}
Read config from: {CONFIG_FILE}
Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.
Work in: {REPO_DIR}

The issue is already claimed (status:in-progress label applied).
Update the lock file at: {CLAIMS_DIR}/{issue_num}.lock with your conversation_id and PID.

Continue from the best available state — DO NOT start over if there is existing progress.
After opening the PR (with 'Closes #N' in the body), transition the issue to status:awaiting-review and delete the lock file.
This conversation handles ONE issue only."""

        title = f"Fix #{issue_num}: {issue_title} (reattach)"
        model = config.get("engineering_model", config.get("tick_model", "pro"))  # reattach always pro
        r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={title}", prompt])

        try:
            result = json.loads(r.stdout)
            new_conv_id = result["response"]["newConversation"]["conversationId"]
        except (json.JSONDecodeError, KeyError):
            log(f"Failed to reattach #{issue_num}. Reclaiming for fresh retry.")
            gh("issue", "edit", str(issue_num), "--repo", TARGET_REPO,
               "--remove-label", "status:in-progress")
            lockfile.unlink(missing_ok=True)
            continue

        # Update lock file with new conversation
        lockfile.write_text(json.dumps({
            "issue": issue_num,
            "dispatcher_pid": os.getpid(),  # debug only — not used for liveness
            "conversation_id": new_conv_id,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        }))
        record_dispatch(DISPATCHES_DIR, new_conv_id, "reattach",
                        issue_number=issue_num, timeout_minutes=45)
        log(f"Reattached #{issue_num} → conversation {new_conv_id}")
        gh("issue", "comment", str(issue_num), "--repo", TARGET_REPO,
           "--body", f"[BUG-FIXER] Reattached to orphaned work from session {conv_id}. New session: {new_conv_id}.")
        reattach_count += 1


# ---------------------------------------------------------------------------
# Step 2: Concurrency check
# ---------------------------------------------------------------------------

r = gh("issue", "list", "--repo", TARGET_REPO,
       "--label", "status:in-progress", "--state", "open",
       "--json", "number", "--jq", "length")
in_progress = int(r.stdout.strip() or "0")

# Subtract reattached issues (already being handled)
active_others = max(0, in_progress - reattach_count)

if active_others >= MAX_CONCURRENT:
    log(f"Slots full ({active_others}/{MAX_CONCURRENT}). Exiting.")
    sys.exit(0)

available_slots = MAX_CONCURRENT - active_others
log(f"{available_slots} slot(s) available ({active_others} active, {reattach_count} reattached).")


# ---------------------------------------------------------------------------
# Step 2.5: Handle needs-iteration PRs
# ---------------------------------------------------------------------------

r_prs = gh("pr", "list", "--repo", TARGET_REPO,
           "--label", "bug-fixer,needs-iteration", "--state", "open",
           "--json", "number,body,url")

if r_prs.returncode == 0:
    try:
        needs_iteration_prs = json.loads(r_prs.stdout)
    except json.JSONDecodeError:
        log(f"Failed to parse needs-iteration PRs: {r_prs.stderr}")
        needs_iteration_prs = []

    # Build set of PRs with active dispatches to skip re-dispatch
    active_pr_dispatches = set()
    if DISPATCHES_DIR.exists():
        for df in DISPATCHES_DIR.glob("*.json"):
            try:
                d = json.loads(df.read_text())
                if d.get("type") == "pr_iteration" and d.get("pr_number"):
                    active_pr_dispatches.add(d["pr_number"])
            except (json.JSONDecodeError, OSError):
                pass

    for pr in needs_iteration_prs:
        pr_num = pr["number"]
        pr_body = pr.get("body", "")

        if pr_num in active_pr_dispatches:
            log(f"PR #{pr_num} — iteration already dispatched, skipping.")
            continue

        # Fetch the latest architect review comments
        r_comments = gh("pr", "view", str(pr_num), "--repo", TARGET_REPO, "--comments", "--json", "comments")
        comments_text = ""
        if r_comments.returncode == 0:
            try:
                comments_data = json.loads(r_comments.stdout)
                if comments_data.get("comments"):
                    comments_text = comments_data["comments"][-1].get("body", "")
            except Exception as e:
                log(f"Failed to parse comments for PR #{pr_num}: {e}")

        # Fetch the PR diff for context (needed if we must spawn fresh)
        diff_r = gh("pr", "diff", str(pr_num), "--repo", TARGET_REPO)
        pr_diff = diff_r.stdout if diff_r.returncode == 0 else "(diff unavailable)"

        prompt = (f"The Architect has reviewed your PR #{pr_num} in repository {TARGET_REPO} "
                  f"and requested changes. Please fix the issues and push to the same PR branch.\n\n"
                  f"Read the full engineering skill instructions at: {SKILL_PATH}\n"
                  f"Read config from: {CONFIG_FILE}\n"
                  f"Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.\n"
                  f"Work in: {REPO_DIR}\n\n"
                  f"After pushing changes, remove the needs-iteration label:\n"
                  f"  gh pr edit {pr_num} --repo {TARGET_REPO} --remove-label needs-iteration\n\n"
                  f"Architect comments:\n{comments_text}")

        # Try to resume the existing developer conversation
        dev_conv_id_match = re.search(r"<!-- agy_dev_conv_id:\s*([a-zA-Z0-9-]+)\s*-->", pr_body)
        dispatched = False
        dispatch_conv_id = None

        if dev_conv_id_match:
            dev_conv_id = dev_conv_id_match.group(1)
            log(f"Resuming developer conversation {dev_conv_id} for PR #{pr_num} iteration.")
            msg_r = run(["agentapi", "send-message", dev_conv_id, prompt])
            if msg_r.returncode == 0:
                dispatched = True
                dispatch_conv_id = dev_conv_id
            else:
                log(f"Failed to resume conversation {dev_conv_id} (expired/deleted?). "
                    f"Falling back to new conversation. Error: {msg_r.stderr or msg_r.stdout}")
        else:
            log(f"PR #{pr_num} has needs-iteration but no agy_dev_conv_id. Spawning fresh developer.")

        # Fallback: spawn a fresh developer agent with full context
        if not dispatched:
            fresh_prompt = (f"You are the bug-fixer agent. The Architect has reviewed PR #{pr_num} "
                           f"in repository {TARGET_REPO} and requested changes.\n\n"
                           f"Read the full engineering skill instructions at: {SKILL_PATH}\n"
                           f"Read config from: {CONFIG_FILE}\n"
                           f"Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.\n"
                           f"Work in: {REPO_DIR}\n\n"
                           f"Check out the PR branch, apply the fixes requested below, run tests, "
                           f"and push to the same branch.\n\n"
                           f"After pushing changes, remove the needs-iteration label:\n"
                           f"  gh pr edit {pr_num} --repo {TARGET_REPO} --remove-label needs-iteration\n\n"
                           f"IMPORTANT: After pushing, ensure the PR body ends with this HTML comment "
                           f"(replace YOUR_CONVERSATION_ID with your actual conversation ID):\n"
                           f"  <!-- agy_dev_conv_id: YOUR_CONVERSATION_ID -->\n\n"
                           f"Architect comments:\n{comments_text}\n\n"
                           f"Current PR diff for context:\n```diff\n{pr_diff}\n```")
            title = f"Fix PR #{pr_num} iteration (architect feedback)"
            model = config.get("engineering_model", config.get("tick_model", "pro"))  # iteration always pro
            new_r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={title}", fresh_prompt])
            if new_r.returncode == 0:
                try:
                    result = json.loads(new_r.stdout)
                    dispatch_conv_id = result["response"]["newConversation"]["conversationId"]
                    dispatched = True
                except (json.JSONDecodeError, KeyError):
                    pass
                log(f"Spawned fresh developer conversation for PR #{pr_num} iteration.")
            else:
                log(f"Failed to spawn fresh conversation for PR #{pr_num}: {new_r.stderr or new_r.stdout}")

        if dispatched and dispatch_conv_id:
            # Track the dispatch — don't remove needs-iteration label yet.
            # The agent removes it after pushing. If the agent stalls, the
            # liveness sweep will post state to the PR and the label stays
            # for the next cycle to re-dispatch.
            record_dispatch(DISPATCHES_DIR, dispatch_conv_id, "pr_iteration",
                            pr_number=pr_num,
                            labels_to_restore=["needs-iteration"],
                            timeout_minutes=30)
            # Ensure the PR body has the dev conversation tag so the next
            # iteration cycle can resume this conversation instead of
            # spawning a new one.
            inject_conv_tag(pr_num, "agy_dev_conv_id", dispatch_conv_id)
            active_others += 1  # Occupies a slot since the developer is running
            available_slots = MAX_CONCURRENT - active_others

# ---------------------------------------------------------------------------
# Step 3: Find eligible issues
# ---------------------------------------------------------------------------

r = run([sys.executable, str(SAFE_INGEST), "issue-list",
         "--repo", TARGET_REPO, "--label", "auto-fixable",
         "--state", "open", "--json", "number,title,milestone,labels"])

try:
    issues = json.loads(r.stdout)
except json.JSONDecodeError:
    log(f"Failed to parse issue list: {r.stderr}")
    sys.exit(0)

# Filter out issues already in-progress or awaiting review
eligible = [
    i for i in issues
    if not any(l.get("name") in ("status:in-progress", "status:awaiting-review") for l in i.get("labels", []))
]

if not eligible:
    log("No eligible issues. Exiting.")
    sys.exit(0)

log(f"{len(eligible)} eligible issue(s) found.")


# ---------------------------------------------------------------------------
# Step 4: Spawn fix conversations (up to available slots)
# ---------------------------------------------------------------------------

spawned = 0

for issue in eligible:
    if spawned >= available_slots:
        log(f"Slot cap reached ({spawned}/{available_slots}). Remaining issues next cycle.")
        break

    issue_num = issue["number"]
    issue_title = issue["title"]

    log(f"Claiming #{issue_num}: {issue_title} (slot {spawned + 1}/{available_slots})")

    # Claim issue
    gh("issue", "edit", str(issue_num), "--repo", TARGET_REPO,
       "--add-label", "status:in-progress")

    # Write lock file
    CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CLAIMS_DIR / f"{issue_num}.lock"
    lock_path.write_text(json.dumps({
        "issue": issue_num,
        "dispatcher_pid": os.getpid(),  # debug only — not used for liveness
        "conversation_id": "pending",
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }))

    # Spawn LLM conversation — this shows up in the IDE
    prompt = f"""You are the bug-fixer agent. Fix issue #{issue_num} in repository {TARGET_REPO}.

Issue title: {issue_title}

Read the full engineering skill instructions at: {SKILL_PATH}
Read config from: {CONFIG_FILE}
Use safe_ingest.py at: {SAFE_INGEST} for all GitHub reads.
Work in: {REPO_DIR}

The issue is already claimed (status:in-progress label applied).
The lock file is at: {lock_path} — update it with your conversation_id and PID.

Start from Step 6 (Branch) in the skill — Steps 1-5 are already done by the dispatcher.
After opening the PR (with 'Closes #N' in the body), transition the issue to status:awaiting-review and delete the lock file.
This conversation handles ONE issue only. Do not pick up additional issues."""

    title = f"Fix #{issue_num}: {issue_title}"
    model = get_model_for_issue(issue)
    r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={title}", prompt])

    try:
        result = json.loads(r.stdout)
        new_conv_id = result["response"]["newConversation"]["conversationId"]
    except (json.JSONDecodeError, KeyError):
        log(f"Failed to spawn for #{issue_num}: {r.stderr or r.stdout}")
        gh("issue", "edit", str(issue_num), "--repo", TARGET_REPO,
           "--remove-label", "status:in-progress")
        lock_path.unlink(missing_ok=True)
        continue

    # Update lock file with conversation ID
    lock_path.write_text(json.dumps({
        "issue": issue_num,
        "dispatcher_pid": os.getpid(),  # debug only — not used for liveness
        "conversation_id": new_conv_id,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }))
    record_dispatch(DISPATCHES_DIR, new_conv_id, "fresh_fix",
                    issue_number=issue_num, timeout_minutes=45)
    log(f"Spawned conversation {new_conv_id} for #{issue_num}")
    spawned += 1

log(f"Done. Spawned {spawned} fix conversation(s), {reattach_count} reattached.")

# Circuit breaker: track consecutive spawn failures
if spawned == 0 and len(eligible) > 0:
    try:
        count = int(FAILURE_COUNT_FILE.read_text().strip()) + 1 if FAILURE_COUNT_FILE.exists() else 1
    except (ValueError, OSError):
        count = 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FAILURE_COUNT_FILE.write_text(str(count))
    if count >= MAX_CONSECUTIVE_FAILURES:
        expires = time.time() + COOLDOWN_SECONDS
        COOLDOWN_FILE.write_text(json.dumps({
            "reason": f"{count} consecutive spawn failures",
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires,
            "expires_at_iso": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        }))
        log(f"Circuit breaker tripped after {count} consecutive failures. "
            f"Cooling down for {COOLDOWN_SECONDS}s.")
else:
    FAILURE_COUNT_FILE.unlink(missing_ok=True)
