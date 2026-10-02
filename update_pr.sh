#!/bin/bash

COMMENT="[ARCHITECT] Changes Requested:

### Architectural Findings
1. **Incorrect Label in PR Cleanup** (\`scripts/engineering_dispatcher.py\` inside \`_cleanup_merged_worktrees\`):
   - **Violation:** The function uses \`--label \"bug-fixer\"\` which doesn't exist in the project labels list. The correct label for PRs is \`bug-fixer:engineer:pr\`. This is a technical debt flaw that makes the cleanup function a no-op.
   - **Required Change:** Change the label to \`bug-fixer:engineer:pr\`.

2. **Infinite Dispatch Loop on Circuit Breaker** (\`scripts/engineering_dispatcher.py\` - reattach and PR iteration loops):
   - **Violation:** In both the reattach loop (Step 1) and PR iteration loop (Step 2), when the worktree circuit breaker triggers (\`count >= MAX_WORKTREE_FAILURES\`), it adds \`bug-fixer:human:needs-intervention\` but **fails to remove** the label that caused it to be dispatched (\`bug-fixer:engineer:in-progress\` and \`bug-fixer:engineer:needs-iteration\`, respectively). This will cause the dispatcher to instantly pick it up again on the next tick, spamming the PR and bypassing the circuit breaker.
   - **Required Change:** Ensure that when escalating to human intervention, the script removes the current triggering label so it drops out of the automated queue.

3. **Unchecked Detached Checkout** (\`scripts/engineering_dispatcher.py\` inside \`ensure_worktree\`):
   - **Violation:** When creating a detached worktree, you run \`git checkout -B ...\` inside an \`if r.returncode == 0:\` block and then immediately return \`wt_path\` regardless of whether the checkout actually succeeded. If checkout fails, the agent works in a detached HEAD state and its pushes will fail.
   - **Required Change:** Check the return code of the \`git checkout -B\` command. If it fails, log the error and return \`None\`.

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
gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --remove-label "bug-fixer:architect:approved" || true
