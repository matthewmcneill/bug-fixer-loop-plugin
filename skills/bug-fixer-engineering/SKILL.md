---
name: bug-fixer-engineering
description: >-
  Execute one cycle of the autonomous bug fixer loop. Picks the highest-priority
  bug-fixer:engineer:needs-fix issue respecting milestone order, implements the fix, runs tests,
  and opens a PR with the bug-fixer label. Triggered by the engineering-loop sidecar every 2
  hours, or manually with "fix bugs" or "run bug fixer".
---

# Bug Fixer Engineering (Execution Engine)

This skill executes one cycle of the autonomous bug fixer loop. It checks for review feedback and approved approaches, selects the highest-priority eligible bug-fixer:engineer:needs-fix issue (with milestone awareness), implements a fix, tests it, and opens a pull request.

## Workflow

## Step 0: Configuration Guard
This skill is part of the `bug-fixer-loop` plugin. Resolve:
```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```
Read `${STATE_DIR}/config.json`. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.` and exit immediately.

1. **Load Configuration:** Read `${STATE_DIR}/config.json`. Extract all fields (`target_repo`, `maintainer`, `trusted_users`, `spec_file_path`, `additional_design_docs`, `test_command`, `lint_command`, `max_concurrent_prs`, `pr_prefix`, `branch_prefix`, `active_milestone`, `milestone_priority_boost`).

### Step 2: Reconcile In-Progress Issues

List all issues tagged `bug-fixer:engineer:in-progress`:
```bash
gh issue list --repo <target_repo> --label "bug-fixer:engineer:in-progress" --state open --json number,title
```

For each, read the lock file at `${STATE_DIR}/claims/<number>.lock`:
```json
{"issue": 231, "pid": 12345, "conversation_id": "abc-123", "claimed_at": "2026-09-26T17:20:00Z"}
```

**Determine liveness using three checks (in order):**

1. **PID check (fastest):** Is the process still running?
   ```bash
   kill -0 <pid> 2>/dev/null && echo "ALIVE" || echo "DEAD"
   ```
   If alive → **SKIP**, agent is working. Move to next in-progress issue.

2. **Transcript check (if PID is dead):** Was the session recently active?
   ```bash
   stat -f %m ~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl
   ```
   If mtime < 5 minutes ago → process may have just exited cleanly (PR opened). Check if a PR was opened for this issue — if yes, it's done, just clean up the lock file.

3. **If both PID dead and transcript stale → ORPHANED. Reattach:**

**Reattach to orphaned work (3 layers of recovery context):**

a. **Read the previous agent's transcript** (richest context):
   ```
   ~/.gemini/antigravity/brain/<conversation_id>/.system_generated/logs/transcript.jsonl
   ```
   Scan the last 20 steps to understand:
   - Which files were edited
   - What approach was taken
   - Where it stopped (error? timeout? crash?)
   - Any test output or diagnostics

b. **Check out the existing git branch** (durable code state):
   ```bash
   git ls-remote origin "<branch_prefix>/<number>-*" 2>/dev/null
   ```
   - **Branch exists:** `git fetch origin <branch> && git checkout <branch>`. Read the diff against main to see what's been done. Run tests to assess state.
   - **No branch:** Previous agent crashed before any commits. Start fresh from Step 6.

c. **Continue from the best available state:**
   - Transcript + branch → full context, continue exactly where it left off
   - Branch only (transcript gone) → read the diff, infer intent from the issue, continue
   - Neither → treat as fresh issue, start from Step 6

d. Update the lock file with THIS conversation's PID and conversation_id.

e. Post comment: `[BUG-FIXER] Reattached to orphaned work from session <old_conversation_id>. Continuing fix.`

**No lock file at all?** The label is orphaned (pre-lock-file or manual). Check if a branch exists — if yes, reattach. If no branch and label age > 2 hours (via events API), remove the label.

### Step 3: Concurrency Guard

After reconciliation, count issues tagged `bug-fixer:engineer:in-progress` that are **owned by other active sessions** (PID alive, not this session):
```bash
gh issue list --repo <target_repo> --label "bug-fixer:engineer:in-progress" --state open --json number,title
```
Exclude any issues this session just reattached to.

If count >= `max_concurrent_prs`, output `ENGINEERING_EXIT: Active development cap reached (<count>/<max>).` and exit cleanly.

> **Note:** The cap limits issues *being actively coded* (`bug-fixer:engineer:in-progress`), not PRs waiting for review (`bug-fixer:architect:awaiting-review`). Once a PR is opened and the issue transitions to `awaiting-review`, the slot is freed immediately.

3. **Check for PR Feedback (Highest Priority):** For each open agent PR:
   - Query comments and reviews via: `python3 ${PLUGIN_DIR}/scripts/safe_ingest.py pr-comments <number>`
   - Look for any unaddressed maintainer comments (comments from `maintainer` or `trusted_users` that do not have a subsequent `[BUG-FIXER]` reply).
   - If found: check out the PR branch, implement the requested changes, run tests, push the changes, reply with `[BUG-FIXER] Addressed review feedback in <sha>`, and exit this cycle.
   - After pushing iteration changes, remove the `bug-fixer:engineer:needs-iteration` label:
     ```bash
     gh pr edit <pr_number> --repo <target_repo> --remove-label bug-fixer:engineer:needs-iteration
     ```

### Step 3.5: Check for Approved Approaches
Query issues with the `bug-fixer:human:needs-approach-review` label:
```bash
python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label bug-fixer:human:needs-approach-review --state open --json number,title,labels,comments
```
For each issue, check if the maintainer has posted an approval comment (contains `approved` or `/approve` from a trusted user). If found:
- Remove `bug-fixer:human:needs-approach-review` label
- Add `bug-fixer:engineer:needs-fix` label
- Log: `[BUG-FIXER] Approach approved for #<number>, now eligible for fixing.`

This ensures previously-reviewed issues enter the bug-fixer:engineer:needs-fix queue.

4. **Select Issue:**
   - **Only T1 issues are eligible for immediate fixing.** The engineering skill MUST NOT pick up `bug-fixer:human:needs-approach-review`, `bug-fixer:human:input-needed`, `bug-fixer:human:project-candidate`, or `bug-fixer:human:required` issues. Only `bug-fixer:engineer:needs-fix` issues are eligible.
   - Query open, bug-fixer:engineer:needs-fix issues:
     ```bash
     python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label bug-fixer:engineer:needs-fix --state open --json number,title,labels,body,milestone,priority,priorityRank
     ```
   - Query milestones when selecting:
     ```bash
     gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on}'
     ```
     and cross-reference with the issue's milestone field.
   - Exclude issues with `bug-fixer:engineer:in-progress` label.
   - **Milestone-aware priority ordering:**
     1. First: `bug-fixer:engineer:needs-fix` issues in the `active_milestone` (from config), sorted by priority label then issue number
     2. Then: `bug-fixer:engineer:needs-fix` issues in other milestones with due dates, sorted by due date ascending
     3. Finally: `bug-fixer:engineer:needs-fix` issues with no milestone, sorted by priority label then issue number
   - Pick the first issue from this sorted list.
   - **Batch detection:** Check if the selected issue has a `bug-fixer:batch:*` label.
     If it does, query for all other `bug-fixer:engineer:needs-fix` issues with the same batch
     label that are not `bug-fixer:engineer:in-progress`:
     ```bash
     BATCH_LABEL=$(echo "<labels>" | grep -o 'batch:[^ "]*')
     python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> \
       --label "${BATCH_LABEL}" --label bug-fixer:engineer:needs-fix --state open \
       --json number,title,labels,body,milestone
     ```
     Exclude any issues already labeled `bug-fixer:engineer:in-progress`. The selected
     issue plus all eligible siblings form the **batch set** for this cycle.
     If no batch label is present, the batch set contains only the single
     selected issue.

     > **Concurrency check:** The entire batch set counts as one concurrency
     > slot (one branch, one PR). However, verify that claiming all batch
     > issues would not exceed `max_concurrent_prs` when counted individually
     > against `bug-fixer:engineer:in-progress`. If it would, reduce the batch to fit.

   - Note if any issue was converted from T2 (`bug-fixer:human:needs-approach-review` with maintainer approval) to accurately classify it in the PR body.

5. **Claim Issue(s):** Claim every issue in the batch set to prevent other agents from picking them up:
   ```bash
   # For each issue in the batch set:
   gh issue edit <number> --repo <target_repo> --add-label bug-fixer:engineer:in-progress
   ```
   Write a lock file for each issue so stale recovery can verify this session is alive:
   ```bash
   mkdir -p ${STATE_DIR}/claims
   echo '{"issue": <number>, "pid": '$$', "conversation_id": "<this_conversation_id>", "claimed_at": "<iso_now>", "batch": "<batch_label_or_null>"}' > ${STATE_DIR}/claims/<number>.lock
   ```
   The `pid` is the current process ID (`$$`). The `conversation_id` is this agent's conversation ID (available as context). The `batch` field links sibling claims for stale recovery. All lock files in a batch share the same `conversation_id`.

6. **Branch:** The dispatcher has already created an ephemeral git worktree and checked out the correct branch for this issue/batch.
   - Do NOT run `git checkout -b`.
   - Do NOT create or destroy git worktrees — the dispatcher manages the worktree lifecycle.
   - You are already in the correct worktree directory.

7. **Understand the Issue(s):** For each issue in the batch set, read the issue body and all comments. Look for:
   - **`[BUG-FIXER-TRIAGE]` analysis comments** — the triager posts these with its
     classification rationale, scope estimate, files likely affected, and checks
     already performed (exclusion zone, PA memory, design docs). Use these findings
     as a head start: adopt the file list as your starting point and note which
     design sources were already consulted.
   - **`[BUG-FIXER-TRIAGE] Batch assignment`** comments — these confirm the batch
     grouping and list sibling issues. Verify the batch is still coherent (no
     issue has been reclassified since the triage assigned the batch).
   - **`[ISSUE-REVIEW]` execution contract comments** — additional context from
     issue reviewers.
   - Read the project's spec file (`spec_file_path`) for relevant sections.
   - Build a **combined file list** across all batch issues. Note where issues
     touch the same files — those changes must be composed together, not
     applied sequentially.
   - Identify the exact files to modify (refine the triager's estimate if needed).

### Step 7.5: Architectural Design Review

Before implementing the fix, review the design context for the affected module(s).
This step discovers available design sources dynamically — not all repos have the
same documentation structure.

> **Triage acceleration:** If a `[BUG-FIXER-TRIAGE]` comment exists on this issue,
> check its **Checks performed** section. Where the triager reports a source was
> already consulted with no conflicts found, you may do a verification pass (confirm
> the finding) rather than a full deep read. This saves significant tokens on issues
> where the triager already did thorough analysis. You MUST still read the spec
> (7.5.1) in full — the spec is the primary reference and is always authoritative.

**7.5.1 — Read the spec.** Read `spec_file_path` (from config) and extract sections
relevant to the affected module(s). This is the primary design reference and is
always available.

**7.5.2 — Read additional design documents.** If `additional_design_docs` is set in
config, read each listed path from the target repo's working tree. These are
maintainer-curated references (e.g. ADR directories, architecture decision logs,
`docs/architecture/` files) that contain design rationale beyond the spec.

**7.5.3 — Probe for Principal Architect memory.** Check whether the target repo has
a PA memory system installed:
```bash
PA_MEMORY=".agents/skills/principal-architect-workspace/memory"
if [ -f "${PA_MEMORY}/MEMORY.md" ]; then
    echo "PA_MEMORY_PRESENT"
fi
```

If present:
1. Read `${PA_MEMORY}/MODULE_INDEX.md`. Find entries for the affected module(s).
2. For the most recent 2-3 projects listed against each module, read the linked
   `PROJECT.md` file. Scan for:
   - **Explicit design decisions** ("Do NOT use X", "Y is acceptable here")
   - **Rejected approaches** (graveyard entries, `rejected-approaches.md`)
   - **ADR references** that constrain the module's implementation
3. If any linked context files (e.g. `context_bridge.md`, ADR docs) are referenced
   from the PROJECT.md for the relevant work orders, read those too.

If NOT present: skip this sub-step entirely. Do not fail or warn — many repos
have no PA history and that's fine. The spec file, additional design docs, and
in-code rationale (7.5.4) are the only design sources.

**7.5.4 — Check for in-code rationale.** Search the affected source files for comments
containing rationale markers:
- `// ADR`, `// DELIBERATE`, `// PA:`, `// WO:`, `// Design:`
- Block comments with `@rationale` or `DESIGN NOTE`

These indicate intentional patterns that should not be casually reversed.

**7.5.5 — Evaluate compatibility.** If the proposed fix would contradict any discovered
design decision, ADR, rejected approach, or in-code rationale:
- **DO NOT implement the fix.**
- Unclaim the issue (`--remove-label bug-fixer:engineer:in-progress`).
- Reclassify as `bug-fixer:human:needs-approach-review` (T2).
- Post a comment:
  ```
  [BUG-FIXER] Architectural conflict detected for #<number>:

  **Proposed change:** <what the fix would do>

  **Conflicts with:** <source — PROJECT.md / ADR / in-code comment>
  - <quote the relevant design decision>

  **Risk:** <what could break>

  Reclassifying as T2 for maintainer review.
  ```
- Exit this issue and continue to the next eligible issue.

**7.5.6 — If no conflicts found:** Proceed to Step 8 with the additional context.
Include a note in the PR body (Step 11):
```
**Design review:** Checked <sources consulted> for <module>. No conflicting decisions found.
```
Where `<sources consulted>` is one of:
- `spec` (spec file only, no PA memory present)
- `spec + PA MODULE_INDEX (N projects traced)` (PA memory was present and used)
- `spec + additional design docs` (if additional_design_docs was consulted)

### Step 7.8: Flag Out-of-Scope Discoveries

While reading the codebase to understand and fix the assigned issue, you may discover significant bugs or improvement opportunities that are **unrelated to the current issue**. Do NOT fix them — they are out of scope. Do NOT file issues directly.

Instead, include a structured discovery section in your **PR body** (Step 11), after the test output and before the conversation ID comment:

~~~
### Discoveries (Out-of-Scope)

> [!NOTE]
> The following were observed during investigation but are out of scope
> for this fix. Flagged for architect review.

1. **[DISCOVERY]** `path/to/file:line` — <one-line description>
   **Detail:** <2-3 sentences explaining what's wrong and why it matters>
   **Suggested severity:** <low/medium/high>

2. **[DISCOVERY]** `path/to/file:line` — <one-line description>
   **Detail:** <explanation>
   **Suggested severity:** <low/medium/high>
~~~

**Rules:**
- Only flag findings that are genuinely significant (would warrant a bug report)
- Do NOT flag style issues, minor TODOs, or things covered by linting
- Do NOT flag things in files you're already modifying (those are in-scope — fix them)
- Do NOT file issues yourself — the architect will evaluate and decide

8. **Implement the Fix(es):**
   - For each issue in the batch set, make the minimal code changes necessary to resolve it.
   - When multiple issues touch the same file, compose the changes together
     rather than making separate passes.
   - Follow the project's coding style (check existing files in the same module).
   - Add or update tests to cover each fix.
   - Ensure changes remain within the module boundary — do NOT make cross-cutting changes.
   - **Batch commit strategy:** Use a single commit for the batch if all
     changes are thematically related (e.g., all `std::nothrow` conversions).
     Use one commit per issue if the changes are conceptually distinct (e.g.,
     an audit script fix vs. a code fix). In either case, all commits go on
     the same branch and the same PR.

9. **Verify:**
   - Run `<test_command>` and capture the full output. Respect any project-level rules about build/test tooling (e.g., MCP lock protocols, shared resource contention). If the project rules mandate using MCP server tools instead of CLI commands, follow them.
   - Run `<lint_command>` if configured.
   - Run `python3 ${PLUGIN_DIR}/scripts/pre_tool_gate.py --scan-only .` for leak check.
   - All checks must pass. If any fails, debug and retry up to 2 times. If tests or lint still fail after 3 total attempts, unclaim **all issues in the batch set** (`--remove-label bug-fixer:engineer:in-progress`), add the `bug-fixer:human:required` label to each, comment `[BUG-FIXER] Attempted fix but tests/lint failed after 3 attempts. Needs human attention.` on each, and exit.

10. **Self-Review Against Design Sources:** Re-read `spec_file_path` and any PA design history or additional design documents discovered in Step 7.5. Verify the completed fix does not contradict any specification, ADR, or documented design decision. If it does, unclaim the issue, label it `bug-fixer:human:spec-change-request,bug-fixer:human:input-needed`, comment explaining the conflict, and exit.

11. **Open PR:**
    - Stage and commit changes:
      ```bash
      git add -A
      # Single issue:
      git commit -m "<pr_prefix> Fix #<number>: <title>"
      # Batch:
      git commit -m "<pr_prefix> Fix #<n1>, #<n2>, ...: <batch_theme>"
      ```
    - Push the branch:
      ```bash
      git push -u origin <branch>
      ```
    - Create the PR:
      ```bash
      # Single issue:
      gh pr create --draft --repo <target_repo> --base main --title "<pr_prefix> Fix #<number>: <title>" --body "<body>"
      # Batch:
      gh pr create --draft --repo <target_repo> --base main --title "<pr_prefix> Batch fix: <batch_theme> (#<n1>, #<n2>, ...)" --body "<body>"
      ```
      The PR body must include:
      - **Closing keywords:** `Closes #<number>` for **every** issue in the batch set — this auto-closes all issues when the PR merges. For a batch: list each on its own line.
      - **Batch context (if batch):** `**Batch:** \`batch:<slug>\` — <count> issues` with a brief table listing each issue number, title, and what was changed for it.
      - Issue tier: `**Classification:** T1 (engineer:needs-fix)` or `T2 (approach approved by @maintainer)`
      - Milestone: `**Milestone:** <name>` if applicable
      - The `bug-fixer:engineer:pr` label must be listed (e.g., `**Labels:** bug-fixer`)
      - What was changed and why it was changed (for batches, organise by issue)
      - **Design review note** from Step 7.5.6
      - **Platform-gap warning** (if applicable): If the fix moves work between execution contexts (e.g. main loop ↔ event callback, timer, ISR, or background task), include: `⚠️ This fix changes execution context. Native tests cannot validate stack limits or async dispatch timing. Hardware testing recommended.`
      - Full test output
      - Leak scan result
      - **Discoveries (Out-of-Scope)** section from Step 7.8 (if any discoveries were flagged)
      - The agent MUST include the following HTML comment at the very end of the body: `<!-- agy_dev_conv_id: <this_conversation_id> -->`. Note: `<this_conversation_id>` must be replaced by its actual Conversation ID.
    - Apply `bug-fixer:engineer:pr` label to the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --add-label bug-fixer
      ```
    - Carry over milestone: If the resolved issues belong to a milestone, set the same milestone on the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --milestone "<milestone_title>"
      ```
    - Transition **every** issue in the batch set from `in-progress` to `awaiting-review` and delete lock files:
      ```bash
      # For each issue in the batch set:
      gh issue edit <number> --repo <target_repo> --remove-label bug-fixer:engineer:in-progress --add-label bug-fixer:architect:awaiting-review
      rm -f ${STATE_DIR}/claims/<number>.lock
      ```
      > **Why `bug-fixer:architect:awaiting-review`?** The issue is no longer being actively coded (frees a concurrency slot), but not done — the PR needs human review. Keeps it off the triage queue and easy to find.

### Step 12: Loop or Exit

After successfully opening a PR, **loop back to Step 4** and pick the next eligible issue. Keep fixing issues until one of these conditions is met:

- **No eligible issues remain** → output `ENGINEERING_EXIT: NO_ELIGIBLE_WORK — opened <count> PR(s) this cycle.` and exit.
- **Concurrency cap reached** → re-check `bug-fixer:engineer:in-progress` count. If >= `max_concurrent_prs`, output `ENGINEERING_EXIT: Active development cap reached — opened <count> PR(s) this cycle.` and exit.
- **A fix attempt fails** (tests fail after 3 retries) → unclaim that issue, then continue to the next eligible issue rather than exiting entirely.

> **Important:** Each fix must start from a fresh branch off `origin/main` (Step 6). Pull latest main before each iteration to pick up any changes merged since the cycle started.

Never invent work, create issues, or expand scope.
