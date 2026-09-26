#!/bin/bash
# bug-fixer-tick-dispatcher.sh
# Lightweight control plane for the bug fixer loop.
# Runs on a cron schedule. Does NOT use an LLM.
# Checks for eligible work and only spawns an LLM conversation
# when there's an actual bug to fix.
#
# Usage: Called by the sidecar schedule. No arguments needed.

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Try workspace config first, fall back to plugin config
WORKSPACE_CONFIG="${PLUGIN_DIR}/../../.agents/plugins/bug-fixer-loop-workspace/config.json"
PLUGIN_CONFIG="${PLUGIN_DIR}/config.json"

if [ -f "$WORKSPACE_CONFIG" ]; then
  CONFIG_FILE="$WORKSPACE_CONFIG"
elif [ -f "$PLUGIN_CONFIG" ]; then
  CONFIG_FILE="$PLUGIN_CONFIG"
else
  echo "TICK_DISPATCH: No config.json found. Exiting."
  exit 0
fi

# Parse config
TARGET_REPO=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['target_repo'])")
MAX_CONCURRENT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('max_concurrent_prs', 4))")
BRANCH_PREFIX=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('branch_prefix', 'bugfix'))")

# Resolve paths for the spawned conversation
SKILL_PATH="${PLUGIN_DIR}/skills/bug-fixer-tick/SKILL.md"
SAFE_INGEST="${PLUGIN_DIR}/scripts/safe_ingest.py"

# Derive repo dir from workspace config location
if [ -f "$WORKSPACE_CONFIG" ]; then
  REPO_DIR="$(cd "$(dirname "$WORKSPACE_CONFIG")/../.." && pwd)"
else
  REPO_DIR="$(pwd)"
fi

STATE_DIR="${REPO_DIR}/.agents/plugins/bug-fixer-loop-workspace"

echo "TICK_DISPATCH: $(date -u +%Y-%m-%dT%H:%M:%SZ) — checking ${TARGET_REPO}"

# --- Step 1: Stale claim recovery ---
# Check for orphaned claims (lock files with dead PIDs)
CLAIMS_DIR="${STATE_DIR}/claims"
REATTACH_COUNT=0
if [ -d "$CLAIMS_DIR" ]; then
  for lockfile in "$CLAIMS_DIR"/*.lock; do
    [ -f "$lockfile" ] || continue
    ISSUE_NUM=$(python3 -c "import json; print(json.load(open('$lockfile'))['issue'])")
    CLAIM_PID=$(python3 -c "import json; print(json.load(open('$lockfile')).get('pid', 0))")
    CONV_ID=$(python3 -c "import json; print(json.load(open('$lockfile')).get('conversation_id', ''))")
    ISSUE_TITLE=$(gh issue view "$ISSUE_NUM" --repo "$TARGET_REPO" --json title --jq '.title' 2>/dev/null || echo "Unknown")

    # --- Layer 1: PID check (microseconds) ---
    if kill -0 "$CLAIM_PID" 2>/dev/null; then
      echo "TICK_DISPATCH: Issue #${ISSUE_NUM} — PID ${CLAIM_PID} alive, skipping."
      continue
    fi

    # --- Layer 2: Transcript mtime check ---
    TRANSCRIPT="$HOME/.gemini/antigravity/brain/${CONV_ID}/.system_generated/logs/transcript.jsonl"
    if [ -f "$TRANSCRIPT" ]; then
      MTIME=$(stat -f %m "$TRANSCRIPT")
      NOW=$(date +%s)
      AGE=$(( NOW - MTIME ))
      if [ "$AGE" -lt 300 ]; then
        echo "TICK_DISPATCH: Issue #${ISSUE_NUM} — PID dead but transcript active ${AGE}s ago. May have just finished."
        # Check if a PR was already opened (clean exit)
        PR_EXISTS=$(gh pr list --repo "$TARGET_REPO" --search "Resolves #${ISSUE_NUM}" --json number --jq 'length' 2>/dev/null || echo "0")
        if [ "$PR_EXISTS" -gt 0 ]; then
          echo "TICK_DISPATCH: Issue #${ISSUE_NUM} — PR exists, cleaning up lock."
          rm -f "$lockfile"
          gh issue edit "$ISSUE_NUM" --repo "$TARGET_REPO" --remove-label "status:in-progress" 2>/dev/null || true
        fi
        continue
      fi
    fi

    # --- Layer 3: Both PID dead and transcript stale → ORPHANED ---
    echo "TICK_DISPATCH: Issue #${ISSUE_NUM} — ORPHANED (PID ${CLAIM_PID} dead, transcript stale)."

    # Check for existing branch (durable code state)
    EXISTING_BRANCH=$(git ls-remote origin "${BRANCH_PREFIX}/${ISSUE_NUM}-*" 2>/dev/null | head -1 | awk '{print $2}' | sed 's|refs/heads/||')

    # Build reattach context for the LLM
    REATTACH_CONTEXT=""
    if [ -n "$EXISTING_BRANCH" ]; then
      REATTACH_CONTEXT="REATTACH CONTEXT: A previous agent was working on this issue and made progress.
Branch: ${EXISTING_BRANCH} — check it out and inspect the diff against main.
"
    fi
    if [ -f "$TRANSCRIPT" ]; then
      REATTACH_CONTEXT="${REATTACH_CONTEXT}Previous agent transcript: ${TRANSCRIPT}
Read the last 20 steps to understand what was tried, where it stopped, and any errors encountered.
"
    fi
    if [ -z "$REATTACH_CONTEXT" ]; then
      REATTACH_CONTEXT="No previous progress found (no branch, no transcript). Start fresh from Step 6."
    fi

    # Spawn a REATTACH conversation (shows in IDE as the fix, not as a control task)
    PROMPT="You are the bug-fixer agent. REATTACH to orphaned issue #${ISSUE_NUM} in repository ${TARGET_REPO}.

Issue title: ${ISSUE_TITLE}

${REATTACH_CONTEXT}

Read the full tick skill instructions at: ${SKILL_PATH}
Read config from: ${CONFIG_FILE}
Use safe_ingest.py at: ${SAFE_INGEST} for all GitHub reads.
Work in: ${REPO_DIR}

The issue is already claimed (status:in-progress label applied).
Update the lock file at: ${CLAIMS_DIR}/${ISSUE_NUM}.lock with your conversation_id and PID.

Continue from the best available state — DO NOT start over if there is existing progress.
After opening the PR, remove status:in-progress and delete the lock file."

    RESULT=$(agentapi new-conversation --title "Fix #${ISSUE_NUM}: ${ISSUE_TITLE} (reattach)" "$PROMPT" 2>&1)
    NEW_CONV_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('response',{}).get('newConversation',{}).get('conversationId',''))" 2>/dev/null || echo "")

    if [ -n "$NEW_CONV_ID" ]; then
      echo "{\"issue\": ${ISSUE_NUM}, \"pid\": $$, \"conversation_id\": \"${NEW_CONV_ID}\", \"claimed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "${CLAIMS_DIR}/${ISSUE_NUM}.lock"
      echo "TICK_DISPATCH: Reattached #${ISSUE_NUM} → conversation ${NEW_CONV_ID}"
      gh issue comment "$ISSUE_NUM" --repo "$TARGET_REPO" --body "[BUG-FIXER] Reattached to orphaned work from session ${CONV_ID}. New session: ${NEW_CONV_ID}." 2>/dev/null || true
      REATTACH_COUNT=$(( REATTACH_COUNT + 1 ))
    else
      echo "TICK_DISPATCH: Failed to reattach #${ISSUE_NUM}. Reclaiming for fresh retry."
      gh issue edit "$ISSUE_NUM" --repo "$TARGET_REPO" --remove-label "status:in-progress" 2>/dev/null || true
      rm -f "$lockfile"
    fi
  done
fi

# --- Step 2: Concurrency check ---
IN_PROGRESS=$(gh issue list --repo "$TARGET_REPO" --label "status:in-progress" --state open --json number --jq 'length')
# Subtract issues we just reattached (those are handled, not blocking)
ACTIVE_OTHERS=$(( IN_PROGRESS - REATTACH_COUNT ))
if [ "$ACTIVE_OTHERS" -lt 0 ]; then ACTIVE_OTHERS=0; fi
if [ "$ACTIVE_OTHERS" -ge "$MAX_CONCURRENT" ]; then
  echo "TICK_DISPATCH: Slots full (${ACTIVE_OTHERS}/${MAX_CONCURRENT}). Exiting."
  exit 0
fi
AVAILABLE_SLOTS=$(( MAX_CONCURRENT - ACTIVE_OTHERS ))
echo "TICK_DISPATCH: ${AVAILABLE_SLOTS} slot(s) available (${ACTIVE_OTHERS} active, ${REATTACH_COUNT} reattached)."

# --- Step 3: Find eligible issues ---
ISSUES=$(python3 "$SAFE_INGEST" issue-list \
  --repo "$TARGET_REPO" \
  --label "auto-fixable" \
  --state open \
  --json number,title,milestone,labels 2>/dev/null || echo "[]")

# Filter out issues already in-progress
ELIGIBLE=$(echo "$ISSUES" | python3 -c "
import json, sys
issues = json.load(sys.stdin)
eligible = [i for i in issues if not any(l.get('name') == 'status:in-progress' for l in i.get('labels', []))]
print(json.dumps(eligible))
")

ELIGIBLE_COUNT=$(echo "$ELIGIBLE" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

if [ "$ELIGIBLE_COUNT" -eq 0 ]; then
  echo "TICK_DISPATCH: No eligible issues. Exiting."
  exit 0
fi

echo "TICK_DISPATCH: ${ELIGIBLE_COUNT} eligible issue(s) found."

# --- Step 4: Spawn fix conversations (up to available slots) ---
SPAWNED=0
echo "$ELIGIBLE" | python3 -c "
import json, sys
issues = json.load(sys.stdin)
for i in issues:
    print(f'{i[\"number\"]}|{i[\"title\"]}')
" | while IFS='|' read -r ISSUE_NUM ISSUE_TITLE; do
  if [ "$SPAWNED" -ge "$AVAILABLE_SLOTS" ]; then
    break
  fi

  # Claim the issue BEFORE spawning the conversation
  echo "TICK_DISPATCH: Claiming issue #${ISSUE_NUM}: ${ISSUE_TITLE}"
  gh issue edit "$ISSUE_NUM" --repo "$TARGET_REPO" --add-label "status:in-progress" 2>/dev/null

  # Write lock file (PID will be updated by the spawned conversation)
  mkdir -p "$CLAIMS_DIR"
  echo "{\"issue\": ${ISSUE_NUM}, \"pid\": $$, \"conversation_id\": \"pending\", \"claimed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "${CLAIMS_DIR}/${ISSUE_NUM}.lock"

  # Spawn the LLM conversation — THIS is what shows up in the IDE
  CONV_TITLE="Fix #${ISSUE_NUM}: ${ISSUE_TITLE}"
  PROMPT="You are the bug-fixer agent. Fix issue #${ISSUE_NUM} in repository ${TARGET_REPO}.

Issue title: ${ISSUE_TITLE}

Read the full tick skill instructions at: ${SKILL_PATH}
Read config from: ${CONFIG_FILE}
Use safe_ingest.py at: ${SAFE_INGEST} for all GitHub reads.
Work in: ${REPO_DIR}

The issue is already claimed (status:in-progress label applied).
The lock file is at: ${CLAIMS_DIR}/${ISSUE_NUM}.lock — update it with your conversation_id and PID.

Start from Step 6 (Branch) in the skill — Steps 1-5 are already done by the dispatcher.
After opening the PR, remove status:in-progress and delete the lock file.
Then pick the next eligible issue if slots remain (Step 12: Loop)."

  RESULT=$(agentapi new-conversation --title "$CONV_TITLE" "$PROMPT" 2>&1)
  NEW_CONV_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('response',{}).get('newConversation',{}).get('conversationId',''))" 2>/dev/null || echo "")

  if [ -n "$NEW_CONV_ID" ]; then
    # Update lock file with actual conversation ID
    echo "{\"issue\": ${ISSUE_NUM}, \"pid\": $$, \"conversation_id\": \"${NEW_CONV_ID}\", \"claimed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "${CLAIMS_DIR}/${ISSUE_NUM}.lock"
    echo "TICK_DISPATCH: Spawned conversation ${NEW_CONV_ID} for #${ISSUE_NUM}"
    SPAWNED=$(( SPAWNED + 1 ))
  else
    echo "TICK_DISPATCH: Failed to spawn conversation for #${ISSUE_NUM}: ${RESULT}"
    # Unclaim on failure
    gh issue edit "$ISSUE_NUM" --repo "$TARGET_REPO" --remove-label "status:in-progress" 2>/dev/null || true
    rm -f "${CLAIMS_DIR}/${ISSUE_NUM}.lock"
  fi
done

echo "TICK_DISPATCH: Done. Spawned ${SPAWNED} fix conversation(s)."
