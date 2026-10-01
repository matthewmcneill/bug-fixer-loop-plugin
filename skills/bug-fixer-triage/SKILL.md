---
name: bug-fixer-triage
description: >-
  Triage GitHub issues for the autonomous bug fixer. Classifies issues using a
  5-tier complexity routing system and handles milestone-aware prioritisation.
  Triggered by the triage sidecar on a scheduled cron, or manually with "triage bug-fixer issues".
---

# Bug Fixer Triage

This skill classifies open issues into 5 tiers of complexity, handles approach reviews for non-trivial bugs, enforces active-PR exclusion zones, and manages milestone-aware prioritisation.

## Workflow

## Step 0: Configuration Guard
This skill is part of the `bug-fixer-loop` plugin. Resolve:
```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```
Read `${STATE_DIR}/config.json`. If `target_repo` is empty or the file is missing, output `CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.` and exit immediately.

1. **Load Configuration:** Read `${STATE_DIR}/config.json`. Extract `target_repo`, `maintainer`, `exclusion_labels`, `milestone_priority_boost`, and `active_milestone`.

2. **Query Open Issues:** Run the safe ingest proxy to list all open issues:
   ```bash
   python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-list --repo <target_repo> --state open --limit 200 --json number,title,labels,body,milestone,author,assignees,comments
   ```

3. **Query Maintainer's Active PRs:** Find the maintainer's open PRs and their changed files to build the **exclusion file set**:
   ```bash
   gh pr list --repo <target_repo> --author <maintainer> --state open --json number,title,headRefName,files
   ```
   The exclusion file set is the union of all files touched by these active PRs.

4. **Classify Each Unclassified Issue:**
   For each issue that does not yet have a classification label (`auto-fixable`, `needs-approach-review`, `input-needed`, `project-candidate`, `human-required`):

   a. **Has exclusion label?** If the issue has any label found in `exclusion_labels`, classify as `human-required` (T5) and skip.

   b. **Check exclusion zone (PR overlap):** Read the issue and estimate which files/modules would need modification. If any overlap with the exclusion file set, classify as `human-required` (T5) and post this comment:
   `[BUG-FIXER] Skipped: overlaps with active PR #<number> (<branch>). Will re-evaluate when PR merges.`

   c. **Hardware dependency:** If the issue requires physical hardware access (pins, sensors, oscilloscope, soldering, antenna measurement), classify as `human-required` (T5). Note: mentioning hardware *concepts* (e.g. "WiFi roaming behaviour") is NOT the same as requiring hardware access.

   d. **Ambiguity check:** If the bug report is ambiguous, lacks repro steps, or has conflicting information, classify as `input-needed` (T3). Post this comment and skip:
   `[BUG-FIXER] Clarification needed: <ask specific questions based on ambiguity>.`

   e. **Small-scope override (CHECK THIS BEFORE T4/T2):** If the issue is about renaming, adding comments/docs, fixing typos, updating URLs, CSS changes, log message changes, or moving files — it is `auto-fixable` (T1) regardless of what domain it touches. A comment about security code is still just a comment. A rename of an architecture folder is still just a rename.

      This override also applies to **mechanical pattern-based code replacements** — changes that follow a repetitive, well-defined substitution pattern with no design decisions, even if they touch multiple files or "public API" signatures. Examples:
      - `bare new` → `std::nothrow` conversion (type substitution, same semantics)
      - `std::function` → typed callback replacement (mechanical signature change)
      - Adding `noexcept` or `const` qualifiers across a codebase
      - Adding scripts or CI tooling for checks that already exist manually
      - Batch documentation cleanup (consistent formatting, header standardisation)

      **The test:** Can each individual change be described as "apply pattern X to occurrence Y" without weighing alternatives? If yes → T1.

      > **⚠️ Depth check — read the body, not just the title.** This override applies to the **actual change required**, not the issue title. Before applying this override, read the full issue body and comments to verify the change is genuinely mechanical. Common false matches:
      > - Title says "remove X from config" but the config entry existed for a reason that needs investigation (e.g. a test was ignored because it fails — un-ignoring means fixing the test)
      > - Title says "add skeleton/template" but the body reveals design decisions about scope, API shape, or what to include
      > - Title says "rename X" but the body reveals cross-project or cross-module scope with dependency implications
      > - Title says "make X configurable" but the body reveals new runtime behaviour (rotation logic, preflight checks, fallback strategies)
      >
      > If the body reveals complexity beyond the surface description, **do not apply this override** — continue to steps 4f/4g for proper classification.

   f. **Size / Scope check:** Classify as `project-candidate` (T4) ONLY if ALL of these are true:
      - Spans 3+ modules/files
      - Requires architectural or design decisions
      - Estimated > 500 lines changed or multi-day effort
      Example T4: "Add CI/CD pipeline", "Rewrite BLE stack", "New module for mesh networking"
      Example NOT T4: "Refactor getTabIcon() docs" (single file), "Rename skill folder" (trivial)

      **Batch decomposition:** When an issue describes a large batch of *independent, repetitive* small changes (e.g. "fix doc formatting across 49 dirs"), classify by the **individual change unit**, not the total scope. If each individual change is trivial and mechanical, the issue is T1 (via step 4e override), not T4 — even if the total line count is large.

      > **T4 vs T5 boundary:** T5 (`human-required`) is reserved for work that an agent **physically cannot do** — needs hardware access, or conflicts with an active PR exclusion zone. T4 (`project-candidate`) is work that is too large or needs architectural decisions, but *could theoretically* be done by an agent with guidance. "Needs human design decisions" alone is NOT T5 — that's T2 (if single-module) or T4 (if multi-module). T5 means impossible for the agent, not merely difficult.

   g. **Complexity check:** Classify as `needs-approach-review` (T2) ONLY if:
      - The fix changes runtime *behaviour* in sensitive code (security, boot, OTA, crypto, memory safety)
      - OR has multiple valid implementation approaches that need human judgment
      - OR the fix **moves work between execution contexts**: main loop / `tick()` ↔ event-bus callback, timer callback, ISR, background task, or async handler. Also: the fix changes startup, shutdown, or restart sequencing.
      - OR the issue is an **audit or code-quality finding that asks to restructure behaviour that already works** (e.g. "polls X — should use events", "blocking loop — make non-blocking", "replace polling with interrupts"). The existing design may be deliberate, and its rationale may live outside the spec.
      Example T2: "Fix race condition in EventBus", "Change OTA reboot logic",
                  "Module::tick() polls SiblingModule::isReady() — should use EventBus"
      Example NOT T2: "Document why setInsecure() is used" (just adding a comment)

      > **Public API changes in a library/framework ARE T2.** If the target repo is consumed by downstream projects, any change to a public API signature — even a mechanically simple one (e.g. `std::function` → typed callback, parameter type changes) — is T2 because downstream callers compile against those headers and will break. The small-scope override (step 4e) applies only to changes that are **internal** to the repo: implementation files, private methods, build scripts, docs, config. If it's in a public header that downstream code `#include`s, it needs approach review.

   g.1. **PA memory-assisted triage (conditional).** If the issue reached this step
      (i.e. it is NOT a small-scope override from step 4e), and you are considering
      classifying it as T1, first check whether the target repo has Principal Architect
      memory installed:
      ```
      .agents/skills/principal-architect-workspace/memory/MODULE_INDEX.md
      ```
      If present, look up the affected module(s) in MODULE_INDEX. If the module has
      recorded design history (linked PROJECT.md files), scan the most recent 2-3
      entries for explicit design decisions or rejected approaches related to the
      issue. If any are found that the proposed fix might contradict, escalate to T2.
      If MODULE_INDEX is not present, skip this sub-step — the heuristic triggers
      above are the primary safety net.

   For T2 issues, post the approach review comment:
   ```
   [BUG-FIXER] Proposed approach for #<number>:
   
   **Problem:** <1-2 sentence summary>
   
   **Proposed fix:**
   - <specific change 1>
   - <specific change 2>
   
   **Files to modify:**
   - `<path/to/file1>`
   - `<path/to/file2>`
   
   **Risk assessment:** <low/medium/high>
   
   Reply with `approved` to proceed, or provide alternative direction.
   ```

   h. **Otherwise:** Classify as `auto-fixable` (T1). Single module, clear acceptance criteria, testable with host tests, no architectural decisions, <200 lines, no spec conflicts. Apply the label.

   i. **Post triage analysis comment:** For every issue classified in this run,
      post a structured `[BUG-FIXER-TRIAGE]` comment capturing the reasoning
      and findings. This preserves the triager's analysis for downstream
      consumers (the tick skill, the status dashboard, and the maintainer) and
      creates an audit trail for the classification decision.

      **T2 and T3 already post structured comments above — skip them here.**

      For **T1 (auto-fixable)** issues, post:
      ```
      [BUG-FIXER-TRIAGE] Classification: T1 (auto-fixable)

      **Scope estimate:**
      - Files likely affected: `<path/to/file1>`, `<path/to/file2>`
      - Estimated change size: <small/medium> (<N lines)
      - Modules touched: <count>

      **Checks performed:**
      - Exclusion zone (active PRs): ✅ No overlap
      - Hardware dependency: ✅ None
      - PA memory consulted: <Yes — no conflicting decisions / No — not installed>
      - Design doc references: <any relevant sections noted, or "None identified">

      **Why not T2:** <1-2 sentence rationale — e.g. "Single module, no behaviour
      change in sensitive code, clear acceptance criteria, no alternative
      approaches to weigh.">
      ```

      For **T1 via small-scope override** (step 4e — typos, renames, docs, URLs,
      CSS, log messages, file moves), use a shorter form:
      ```
      [BUG-FIXER-TRIAGE] Classification: T1 (auto-fixable, small-scope override)

      **Type:** <rename / typo fix / docs update / URL change / etc.>
      **Files likely affected:** `<path/to/file1>`
      ```

      For **T4 (project-candidate)** issues, post:
      ```
      [BUG-FIXER-TRIAGE] Classification: T4 (project-candidate)

      **Why T4:**
      - Estimated scope: <modules/files affected, line count estimate>
      - Requires architectural decisions: <yes/no + brief explanation>
      - Suggested decomposition: <if obvious, list 2-3 sub-tasks>

      **Recommendation:** Use /principal-architect to decompose into work orders.
      ```

      For **T5 (human-required)** issues that don't already have a comment from
      step 4b (PR-overlap already posts its own comment), post:
      ```
      [BUG-FIXER-TRIAGE] Classification: T5 (human-required)

      **Reason:** <exclusion label "<label>" matched / requires physical hardware
      access: <specific hardware need identified>>
      ```


5. **Handle Approach Reviews (T2 Re-evaluation):**
   For issues already labeled `needs-approach-review`:
   Check if the maintainer has replied with an approval comment (e.g. `approved`). If approved:
   - Remove `needs-approach-review` label
   - Add `auto-fixable` label (T1)

6. **Milestone Query & Prioritisation:**
   Query milestones to get their status:
   ```bash
   gh api repos/<target_repo>/milestones --jq '.[] | {number, title, due_on, open_issues, state}'
   ```
   For all T1 and T2 issues, evaluate milestones:
   - If the issue belongs to a milestone, note it in the classification summary: `Milestone: <name> (due: <date>)`.
   - If the issue has no milestone but clearly relates to an open milestone's theme, suggest the milestone in the classification comment.

7. **Priority Ordering for Tick (Documented in Output):**
   When summarizing the output for the tick skill, output the issues in this priority order (assuming `milestone_priority_boost` is true):
   1. Issues in `active_milestone` (by priority label, then issue number)
   2. Issues in other milestones with due dates (by due date ascending, then priority)
   3. Issues without milestones (by priority label, then issue number)

8. **Fast Exit Conditions:** If no open issues exist, or if all open issues are already classified, output `TRIAGE_COMPLETE: No new issues to classify` and exit cleanly.

9. **Loop Until Complete:** After classifying a batch of issues, re-query the issue list (Step 2) and check for any remaining unclassified issues. If unclassified issues remain, loop back to Step 4 and continue classifying. Do NOT exit with unclassified issues remaining unless you hit an unrecoverable error. If you must exit early, report which issues remain unclassified in the summary.

### Step 9.5: Bundle Affinity Grouping

After all issues are classified, group related T1 issues into **batches** so
the tick skill can implement them as single PRs. This reduces review overhead
when an audit or sweep produces many small, thematically related issues.

**When to bundle:** Only group issues that meet ALL of these criteria:
- All are T1 (`auto-fixable`)
- They share a clear affinity (see signals below)
- Each individual fix is independent (no ordering dependency between them)
- The combined change set would still be a reviewable PR (estimated < ~500 lines total)
- They share the same milestone assignment (or all have no milestone)

**Affinity signals (in priority order):**

1. **Same audit rule / spec section** — issues cite the same spec clause
   (e.g., "memory-rules §6"), same audit check name, or same audit group.
2. **Same change pattern** — mechanical substitutions following the same
   template (e.g., all "bare `new` → `std::nothrow`", all "add `noexcept`").
3. **Overlapping files** — issues that modify the same files or the same
   module. Combining them avoids merge conflicts between sequential PRs.
4. **Shared context label** — issues sharing an `audit`, `tech-debt`,
   `release-*`, or similar thematic label that indicates common origin.

Two signals is strong evidence for a bundle. One signal alone is sufficient
only when the issues are clearly part of the same body of work (e.g., same
audit group, sequential issue numbers, cross-references in the body text).

**How to assign batches:**

a. Generate a batch slug from the shared theme — e.g.,
   `batch:memory-rules-nothrow`, `batch:audit-script-fixes`,
   `batch:doc-header-cleanup`. Keep slugs short, lowercase, hyphenated,
   and descriptive.

b. Create the label if it doesn't exist:
   ```bash
   gh label create "batch:<slug>" --repo <target_repo> --color "C2E0C6" \
     --description "PR bundle: <theme>" 2>/dev/null || true
   ```

c. Apply the label to every issue in the group:
   ```bash
   gh issue edit <number> --repo <target_repo> --add-label "batch:<slug>"
   ```

d. Post a grouping comment on each issue in the batch:
   ```
   [BUG-FIXER-TRIAGE] Batch assignment: `batch:<slug>`

   Grouped with: #<sibling1>, #<sibling2>, ...
   **Reason:** <1-line affinity rationale — e.g., "Same spec section
   (memory-rules §6), overlapping files (BLEStack.cpp, DataManager.cpp)">

   The bug fixer will implement these together in a single PR.
   ```

**Do NOT bundle:**
- T2, T3, T4, or T5 issues (only T1s are eligible)
- Issues where the combined diff would exceed ~500 lines
- Issues with different milestone assignments
- Issues where one fix depends on another being done first (order dependency)
- A single issue with no natural siblings

**Re-bundling on subsequent triage runs:** If a new issue is classified as T1
and shares affinity with issues that already have a `batch:*` label, add the
new issue to the existing batch (apply the same label, post the grouping
comment referencing the updated sibling list). Do not create a new batch when
an existing one covers the same theme.

**Unbatched T1 issues are fine.** Not every issue needs a batch — standalone
T1s continue to get their own PR as before.

10. **Summary Report:** At the end of the run, output a summary table of all classifications made during this run, the prioritized list of T1/T2 issues for the bug fixer to work on, and any batch groups created or extended during this run (listing the batch slug, member issues, and affinity rationale).
