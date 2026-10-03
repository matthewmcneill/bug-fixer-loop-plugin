#!/usr/bin/env python3
"""
architect_dispatcher.py — Polls GitHub for Draft PRs needing architect review
and monitors approved PRs for merge conflicts.

Runs on a cron schedule via the sidecar. Does NOT use an LLM.
Checks for draft bug-fixer PRs and only spawns an Architect review
conversation when there's a draft to review.
Also checks approved PRs for merge conflicts and re-routes them to engineering.

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
TARGET_REPO = ""

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Ensure agentapi is on PATH
AGENTAPI_DIR = Path.home() / ".gemini" / "antigravity" / "bin"
if AGENTAPI_DIR.is_dir():
    os.environ["PATH"] = str(AGENTAPI_DIR) + os.pathsep + os.environ.get("PATH", "")

from liveness import record_dispatch, sweep_dispatches, agentapi_is_reachable


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"ARCHITECT_DISPATCH: {ts} — {msg}", flush=True)


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def gh(*args) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def inject_conv_tag(pr_number: int, tag_name: str, conv_id: str) -> bool:
    """Inject or update an HTML conversation-ID tag in a PR body.

    Ensures the PR body always has the correct tag, regardless of whether
    the LLM agent remembered to write it.  Idempotent — safe to call on
    every dispatch cycle.
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


def check_merge_conflicts(target_repo: str | None = None) -> None:
    """Step 0.5: Merge Conflict Monitor for Approved PRs.

    Queries open PRs with labels bug-fixer:system:pr + bug-fixer:architect:approved.
    For each, checks GitHub's mergeable status via the JSON API:
      - CONFLICTING: posts [ARCHITECT] comment, removes bug-fixer:architect:approved,
        adds bug-fixer:engineer:needs-iteration, converts back to draft (gh pr ready --undo)
      - UNKNOWN: logs and retries next tick
      - MERGEABLE: logs clean status
    """
    repo = target_repo or TARGET_REPO
    if not repo:
        log("No target repo configured for merge conflict check.")
        return

    log(f"Checking merge conflict status for approved PRs in {repo}")
    r = gh("pr", "list", "--repo", repo,
           "--label", "bug-fixer:system:pr",
           "--label", "bug-fixer:architect:approved",
           "--state", "open",
           "--json", "number,title,mergeable")

    if r.returncode != 0:
        log(f"Error fetching approved PRs: {r.stderr.strip()}")
        return

    try:
        approved_prs = json.loads(r.stdout)
    except json.JSONDecodeError:
        log("Failed to decode approved PRs JSON.")
        return

    if not approved_prs:
        log("No approved PRs to check for merge conflicts.")
        return

    for pr in approved_prs:
        number = pr.get("number")
        title = pr.get("title", "")
        mergeable = pr.get("mergeable", "UNKNOWN")

        if mergeable == "CONFLICTING":
            log(f"PR #{number} ({title}): CONFLICTING — merge conflicts detected. Returning to engineering.")
            conflict_comment = (
                "[ARCHITECT] Merge conflict detected:\n\n"
                "- This approved PR has developed merge conflicts against the base branch.\n"
                "- Re-routing to engineering for rebase, conflict resolution, and verification."
            )
            r_comment = gh("pr", "comment", str(number), "--repo", repo, "--body", conflict_comment)
            if r_comment.returncode != 0:
                log(f"Warning: Failed to post conflict comment on PR #{number}: {r_comment.stderr.strip()}")

            r_edit = gh("pr", "edit", str(number), "--repo", repo,
                        "--remove-label", "bug-fixer:architect:approved",
                        "--add-label", "bug-fixer:engineer:needs-iteration")
            if r_edit.returncode != 0:
                log(f"Warning: Failed to update labels on PR #{number}: {r_edit.stderr.strip()}")

            r_undo = gh("pr", "ready", str(number), "--undo", "--repo", repo)
            if r_undo.returncode != 0:
                log(f"Warning: Failed to convert PR #{number} back to draft: {r_undo.stderr.strip()}")

            log(f"PR #{number}: reverted to draft and labeled bug-fixer:engineer:needs-iteration.")
        elif mergeable == "UNKNOWN":
            log(f"PR #{number} ({title}): mergeable status is UNKNOWN — will retry next tick.")
        elif mergeable == "MERGEABLE":
            log(f"PR #{number} ({title}): MERGEABLE — no merge conflicts.")
        else:
            log(f"PR #{number} ({title}): unexpected mergeable status '{mergeable}'.")


def main():
    global TARGET_REPO

    if not os.environ.get("ANTIGRAVITY_PROJECT_ID"):
        log("FATAL: ANTIGRAVITY_PROJECT_ID not set — agentapi will fail. "
            "Re-run install.sh from within an Antigravity session.")
        sys.exit(1)

    if not agentapi_is_reachable():
        log("ARCHITECT_EXIT: agentapi is unreachable — skipping this cycle.")
        sys.exit(0)

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
    DISPATCHES_DIR = STATE_DIR / "dispatches"

    log(f"Polling PRs in {TARGET_REPO}")

    # ---------------------------------------------------------------------------
    # Step 0: Sweep previous dispatches for stalled/completed conversations
    # ---------------------------------------------------------------------------

    recovered = sweep_dispatches(DISPATCHES_DIR, TARGET_REPO, log)
    for r_item in recovered:
        log(f"Recovered: {r_item['type']} conv={r_item['conversation_id'][:8]}... "
            f"action={r_item.get('action', 'unknown')}")

    # ---------------------------------------------------------------------------
    # Step 0.5: Merge Conflict Monitor for Approved PRs
    # ---------------------------------------------------------------------------

    check_merge_conflicts(TARGET_REPO)

    # ---------------------------------------------------------------------------
    # Step 1: Draft PR Polling Logic
    # ---------------------------------------------------------------------------

    r = gh("pr", "list", "--repo", TARGET_REPO, "--label", "bug-fixer:system:pr,bug-fixer:architect:awaiting-review",
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

    # Build set of PRs with active review dispatches
    active_review_dispatches = set()
    if DISPATCHES_DIR.exists():
        for df in DISPATCHES_DIR.glob("*.json"):
            try:
                d = json.loads(df.read_text())
                if d.get("type") == "architect_review" and d.get("pr_number"):
                    active_review_dispatches.add(d["pr_number"])
            except (json.JSONDecodeError, OSError):
                pass

    spawned = 0

    for pr in prs:
        labels = [label["name"] for label in pr.get("labels", [])]
        if "bug-fixer:engineer:needs-iteration" in labels or "bug-fixer:architect:approved" in labels:
            continue

        number = pr["number"]
        title = pr["title"]
        body = pr.get("body", "")

        if number in active_review_dispatches:
            log(f"PR #{number} — review already dispatched, skipping.")
            continue

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
                f"(max {MAX_REVIEW_ROUNDS}). Adding bug-fixer:human:needs-intervention label.")
            gh("pr", "edit", str(number), "--repo", TARGET_REPO,
               "--add-label", "bug-fixer:human:needs-intervention",
               "--remove-label", "bug-fixer:architect:awaiting-review")
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
        dispatch_conv_id = None
        if conv_id:
            log(f"Resuming architect conversation {conv_id} for PR #{number}")
            msg_r = run(["agentapi", "send-message", conv_id, prompt])
            if msg_r.returncode == 0:
                spawn_new = False
                dispatch_conv_id = conv_id
            else:
                log(f"Failed to resume conversation {conv_id} (expired/deleted?). "
                    f"Falling back to new conversation. Error: {msg_r.stderr or msg_r.stdout}")

        if spawn_new:
            log(f"Spawning new architect conversation for PR #{number}")
            conv_title = f"Architect Review #{number}"
            model = config.get("architect_model", "pro")
            new_r = run(["agentapi", "new-conversation", f"--model={model}", f"--title={conv_title}", prompt])
            if new_r.returncode != 0:
                log(f"Failed to spawn new conversation for PR #{number}: "
                    f"{new_r.stderr or new_r.stdout}")
                continue
            try:
                result = json.loads(new_r.stdout)
                dispatch_conv_id = result["response"]["newConversation"]["conversationId"]
            except (json.JSONDecodeError, KeyError):
                pass
            spawned += 1

        if dispatch_conv_id:
            record_dispatch(DISPATCHES_DIR, dispatch_conv_id, "architect_review",
                            pr_number=number, timeout_minutes=20,
                            model=config.get("architect_model", "pro"))
            # Ensure the PR body has the architect conversation tag so the next
            # review cycle can resume this conversation instead of spawning new.
            inject_conv_tag(number, "agy_arch_conv_id", dispatch_conv_id)

    log(f"Finished polling PRs. Spawned/resumed {spawned} review(s).")


if __name__ == "__main__":
    main()
