---
name: bug-fixer-tick
description: >-
  Execute one cycle of the autonomous bug fixer loop. Picks the highest-priority
  auto-fixable issue respecting milestone order, implements the fix, runs tests,
  and opens a PR with the bug-fixer label. Triggered by the tick sidecar every 2
  hours, or manually with "fix bugs" or "run bug fixer".
---

# Bug Fixer Tick (Execution Engine)

This skill executes one cycle of the autonomous bug fixer loop. It checks for review feedback and approved approaches, selects the highest-priority eligible auto-fixable issue (with milestone awareness), implements a fix, tests it, and opens a pull request.

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

List all issues tagged `status:in-progress`:
```bash
gh issue list --repo <target_repo> --label "status:in-progress" --state open --json number,title
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

After reconciliation, count issues tagged `status:in-progress` that are **owned by other active sessions** (PID alive, not this tick):
```bash
gh issue list --repo <target_repo> --label "status:in-progress" --state open --json number,title
```
Exclude any issues this tick just reattached to.

If count >= `max_concurrent_prs`, output `TICK_EXIT: Active development cap reached (<count>/<max>).` and exit cleanly.

> **Note:** The cap limits issues *being actively coded* (`status:in-progress`), not PRs waiting for review (`status:awaiting-review`). Once a PR is opened and the issue transitions to `awaiting-review`, the slot is freed immediately.

3. **Check for PR Feedback (Highest Priority):** For each open agent PR:
   - Query comments and reviews via: `python3 ${PLUGIN_DIR}/scripts/safe_ingest.py pr-comments <number>`
   - Look for any unaddressed maintainer comments (comments from `maintainer` or `trusted_users` that do not have a subsequent `[BUG-FIXER]` reply).
   - If found: check out the PR branch, implement the requested changes, run tests, push the changes, reply with `[BUG-FIXER] Addressed review feedback in <sha>`, and exit this tick.

### Step 3.5: Check for Approved Approaches
Query issues with the `needs-approach-review` label:
```bash
python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label needs-approach-review --state open --json number,title,labels,comments
```
For each issue, check if the maintainer has posted an approval comment (contains `approved` or `/approve` from a trusted user). If found:
- Remove `needs-approach-review` label
- Add `auto-fixable` label
- Log: `[BUG-FIXER] Approach approved for #<number>, now eligible for fixing.`

This ensures previously-reviewed issues enter the auto-fixable queue.

4. **Select Issue:**
   - **Only T1 issues are eligible for immediate fixing.** The tick skill MUST NOT pick up `needs-approach-review`, `input-needed`, `project-candidate`, or `human-required` issues. Only `auto-fixable` issues are eligible.
   - Query open, auto-fixable issues:
     ```bash
     python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --label auto-fixable --state open --json number,title,labels,body,milestone,priority,priorityRank
     ```
   - Query milestones when selecting:
     ```bash
     gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on}'
     ```
     and cross-reference with the issue's milestone field.
   - Exclude issues with `status:in-progress` label.
   - **Milestone-aware priority ordering:**
     1. First: `auto-fixable` issues in the `active_milestone` (from config), sorted by priority label then issue number
     2. Then: `auto-fixable` issues in other milestones with due dates, sorted by due date ascending
     3. Finally: `auto-fixable` issues with no milestone, sorted by priority label then issue number
   - Pick the first issue from this sorted list.
   - Note if the issue was converted from T2 (`needs-approach-review` with maintainer approval) to accurately classify it in the PR body.

5. **Claim Issue:** Act as a lease to prevent other agents from picking up the same work:
   ```bash
   gh issue edit <number> --repo <target_repo> --add-label status:in-progress
   ```
   Write a lock file so stale recovery can verify this session is alive:
   ```bash
   mkdir -p ${STATE_DIR}/claims
   echo '{"issue": <number>, "pid": '$$', "conversation_id": "<this_conversation_id>", "claimed_at": "<iso_now>"}' > ${STATE_DIR}/claims/<number>.lock
   ```
   The `pid` is the current process ID (`$$`). The `conversation_id` is this agent's conversation ID (available as context). Both are used by Step 2 to determine liveness.

6. **Branch:** Check out a new branch from `origin/main`:
   ```bash
   git checkout -b <branch_prefix>/issue-<number>-<slug> origin/main
   ```
   *(where `<slug>` is a kebab-case version of the issue title, max 40 chars)*

7. **Understand the Issue:** Read the issue body and all comments. Look for:
   - **`[BUG-FIXER-TRIAGE]` analysis comments** — the triager posts these with its
     classification rationale, scope estimate, files likely affected, and checks
     already performed (exclusion zone, PA memory, design docs). Use these findings
     as a head start: adopt the file list as your starting point and note which
     design sources were already consulted.
   - **`[ISSUE-REVIEW]` execution contract comments** — additional context from
     issue reviewers.
   - Read the project's spec file (`spec_file_path`) for relevant sections.
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
- Unclaim the issue (`--remove-label status:in-progress`).
- Reclassify as `needs-approach-review` (T2).
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

8. **Implement the Fix:**
   - Make the minimal code changes necessary to resolve the issue.
   - Follow the project's coding style (check existing files in the same module).
   - Add or update tests to cover the fix.
   - Ensure changes remain within the module boundary — do NOT make cross-cutting changes.

9. **Verify:**
   - Run `<test_command>` and capture the full output. Respect any project-level rules about build/test tooling (e.g., MCP lock protocols, shared resource contention). If the project rules mandate using MCP server tools instead of CLI commands, follow them.
   - Run `<lint_command>` if configured.
   - Run `python3 ${PLUGIN_DIR}/scripts/pre_tool_gate.py --scan-only .` for leak check.
   - All checks must pass. If any fails, debug and retry up to 2 times. If tests or lint still fail after 3 total attempts, unclaim the issue (`--remove-label status:in-progress`), add the `human-required` label, comment `[BUG-FIXER] Attempted fix but tests/lint failed after 3 attempts. Needs human attention.`, and exit.

10. **Self-Review Against Design Sources:** Re-read `spec_file_path` and any PA design history or additional design documents discovered in Step 7.5. Verify the completed fix does not contradict any specification, ADR, or documented design decision. If it does, unclaim the issue, label it `spec-change-request,input-needed`, comment explaining the conflict, and exit.

11. **Open PR:**
    - Stage and commit changes:
      ```bash
      git add -A
      git commit -m "<pr_prefix> Fix #<number>: <title>"
      ```
    - Push the branch:
      ```bash
      git push -u origin <branch>
      ```
    - Create the PR:
      ```bash
      gh pr create --repo <target_repo> --base main --title "<pr_prefix> Fix #<number>: <title>" --body "<body>"
      ```
      The PR body must include:
      - **Closing keyword:** `Closes #<number>` — this auto-closes the issue when the PR merges
      - Issue tier: `**Classification:** T1 (auto-fixable)` or `T2 (approach approved by @maintainer)`
      - Milestone: `**Milestone:** <name>` if applicable
      - The `bug-fixer` label must be listed (e.g., `**Labels:** bug-fixer`)
      - What was changed and why it was changed
      - **Design review note** from Step 7.5.6
      - **Platform-gap warning** (if applicable): If the fix moves work between execution contexts (e.g. main loop ↔ event callback, timer, ISR, or background task), include: `⚠️ This fix changes execution context. Native tests cannot validate stack limits or async dispatch timing. Hardware testing recommended.`
      - Full test output
      - Leak scan result
    - Apply `bug-fixer` label to the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --add-label bug-fixer
      ```
    - Carry over milestone: If the resolved issue belongs to a milestone, set the same milestone on the PR:
      ```bash
      gh pr edit <pr_number> --repo <target_repo> --milestone "<milestone_title>"
      ```
    - Transition the issue from `in-progress` to `awaiting-review` and delete the lock file:
      ```bash
      gh issue edit <number> --repo <target_repo> --remove-label status:in-progress --add-label status:awaiting-review
      rm -f ${STATE_DIR}/claims/<number>.lock
      ```
      > **Why `status:awaiting-review`?** The issue is no longer being actively coded (frees a concurrency slot), but not done — the PR needs human review. Keeps it off the triage queue and easy to find.

### Step 12: Loop or Exit

After successfully opening a PR, **loop back to Step 4** and pick the next eligible issue. Keep fixing issues until one of these conditions is met:

- **No eligible issues remain** → output `TICK_EXIT: NO_ELIGIBLE_WORK — opened <count> PR(s) this tick.` and exit.
- **Concurrency cap reached** → re-check `status:in-progress` count. If >= `max_concurrent_prs`, output `TICK_EXIT: Active development cap reached — opened <count> PR(s) this tick.` and exit.
- **A fix attempt fails** (tests fail after 3 retries) → unclaim that issue, then continue to the next eligible issue rather than exiting entirely.

> **Important:** Each fix must start from a fresh branch off `origin/main` (Step 6). Pull latest main before each iteration to pick up any changes merged since the tick started.

Never invent work, create issues, or expand scope.
