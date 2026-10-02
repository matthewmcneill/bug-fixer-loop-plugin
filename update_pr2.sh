#!/bin/bash

COMMENT="[ARCHITECT] Changes Requested:

### Architectural Findings
1. **Incomplete Fix: Infinite Dispatch Loop on Circuit Breaker in Step 4** (\`scripts/engineering_dispatcher.py\` - needs-fix loop):
   - **Violation:** You correctly fixed the circuit breaker label removal in Step 1 (reattach) and Step 2 (PR iteration). However, you missed the exact same bug in Step 4 (issue claim loop). When the circuit breaker triggers in Step 4, it removes \`bug-fixer:engineer:in-progress\` but **fails to remove** \`bug-fixer:engineer:needs-fix\`. Since the dispatcher pulls issues based on the \`needs-fix\` label, this issue will be endlessly picked up and escalated on every tick.
   - **Required Change:** In Step 4, when escalating to human intervention, you must remove the \`bug-fixer:engineer:needs-fix\` label.

2. **Broken Collision Workaround (Detached HEAD Checkout & Push Failure)** (\`scripts/engineering_dispatcher.py\` - \`ensure_worktree\` & \`CONTAINMENT\`):
   - **Violation:** When a branch collision is detected in the main repo (\`checked_out_at == str(REPO_DIR)\`), you correctly create a detached worktree. However, you then attempt \`git checkout -B {branch_name} origin/{branch_name}\`. This will ALWAYS fail with \`fatal: branch is already checked out\` because Git prevents checking out the same branch twice. Since you now properly return \`None\` on checkout failure, this collision will just repeatedly hit the circuit breaker. Furthermore, if the agent were to work in a detached HEAD, the current push instruction (\`git push origin HEAD\`) would fail because Git requires a fully qualified ref when pushing from detached HEAD.
   - **Required Change:** 
     1. In \`ensure_worktree\`, when falling back to the detached worktree, do NOT attempt to \`checkout -B\` the branch name. Instead, just ensure the detached HEAD is up to date: \`git -C {wt_path} reset --hard origin/{branch_name}\`.
     2. In \`CONTAINMENT\`, update the push instruction to: \`- To push: git -C {wt_path} push origin HEAD:{branch}\`. This ensures pushes always succeed regardless of local branch state.

Please address these architectural findings in the next iteration."

gh pr comment 28 --repo matthewmcneill/bug-fixer-loop-plugin -b "$COMMENT"

# Fetch existing body using safe_ingest.py
EXISTING_BODY=$(python3 scripts/safe_ingest.py pr-view 28 | jq -r .body)
CONV_ID="9f825c3d-dd74-48af-883a-f471c0a9756e"

# Update body
if echo "$EXISTING_BODY" | grep -q "<!-- agy_arch_conv_id:"; then
  UPDATED_BODY=$(echo "$EXISTING_BODY" | sed -E "s|<!-- agy_arch_conv_id: [^ ]+ -->|<!-- agy_arch_conv_id: $CONV_ID -->|")
else
  UPDATED_BODY="$EXISTING_BODY

<!-- agy_arch_conv_id: $CONV_ID -->"
fi

gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --body "$UPDATED_BODY"
gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --add-label "bug-fixer:engineer:needs-iteration"
gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --remove-label "bug-fixer:architect:approved" || true
