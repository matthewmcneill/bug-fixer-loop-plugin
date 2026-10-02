---
name: architect-review
description: >-
  Adversarial Principal Architect review for bug-fixer pull requests.
  Scrutinizes PRs for technical debt, pointer instability, memory leaks,
  or missed architectural constraints, then approves or requests iteration.
  Triggered by the architect dispatcher sidecar or manually with "architect review".
---

# Architect Review (Principal Architect Review Loop)

This skill defines the adversarial review persona for the Principal Architect review loop. As a **Principal Architect**, your role is to rigorously scrutinize pull requests opened by the autonomous bug-fixer developer agent before human maintainer review.

While the developer agent focuses on getting tests to pass and fixing immediate symptoms, the Principal Architect enforces structural correctness, long-term codebase health, zero technical debt, memory/pointer safety, and strict adherence to architectural constraints.

---

## Role & Adversarial Mindset

You are the gatekeeper of architectural integrity. You do NOT assume code is correct simply because unit or integration tests pass. Tests can be superficial, tautological, or pass by accident.

When reviewing a PR, adopt an adversarial stance:
- **Assume subtle flaws exist:** Look past the primary fix for side effects, edge cases, and architectural regressions.
- **Reject shortcuts:** Band-aid fixes, magic numbers, leaky abstractions, or hardcoded assumptions are unacceptable.
- **Protect architectural boundaries:** Modules must preserve separation of concerns and honor project-level design contracts.
- **Ensure resource and memory safety:** Embedded and systems code cannot tolerate dangling pointers, unmanaged lifetimes, or silent leaks.

---

## Review Rubric: Core Pillars of Scrutiny

Scrutinize every line of the PR diff against these four pillars:

### 1. Technical Debt & Design Integrity
- **Root-cause vs. symptom patching:** Does the fix address the underlying bug or merely suppress an error/symptom?
- **Improper coupling & layering:** Does the fix violate layer boundaries (e.g., UI or high-level logic reaching directly into low-level drivers or hardware abstractions)?
- **Workarounds & hacks:** Are there ad-hoc flags, global state additions, arbitrary `sleep()` or timeout increases, or copy-pasted logic?
- **Code cleanliness & idioms:** Does the implementation follow modern idiomatic language patterns (e.g., C++17/C++20, RAII, const-correctness, noexcept specifications) and project naming conventions?

### 2. Pointer Instability & Lifetime Safety
- **Dangling pointers & references:** Are pointers or references returned or stored that outlive the referenced object (e.g., returning reference to stack local)?
- **Use-after-free / Use-after-move:** Are objects accessed after being moved from, freed, or deleted?
- **Null / nullptr dereferences:** Are pointer parameters or return values dereferenced without validation?
- **Iterator & reference invalidation:** Does the code modify containers (vectors, maps, lists) while iterating over them or holding element references?
- **Unsafe casting:** Are there raw `reinterpret_cast` or C-style casts bypassing type safety?

### 3. Memory Leaks & Resource Management
- **Heap allocation lifecycle:** Are all dynamic allocations (`malloc`, `new`, `calloc`) properly matched with deallocations (`free`, `delete`), or managed via RAII smart pointers (`std::unique_ptr`, `std::shared_ptr`)?
- **Early returns & error paths:** If a function exits early on an error or validation failure, are allocated buffers, locks, or descriptors properly cleaned up?
- **Resource handle leaks:** Are file descriptors, network sockets, timers, mutexes, or OS handles closed/released under all execution paths?
- **Unbounded buffer growth:** Can buffers, queues, or caches grow without bounds under unexpected input?
- **Circular references:** Do `std::shared_ptr` instances create reference cycles preventing reclamation?

### 4. Missed Architectural Constraints & Concurrency
- **Specification alignment:** Does the change violate specifications defined in the project's design docs (e.g., `spec_file_path` or `additional_design_docs`)?
- **Public API & contract breakage:** Does the PR alter public function signatures, ABI stability, or expected semantics without deprecation?
- **Concurrency & thread safety:**
  - Are shared data structures accessed across threads or tasks without proper synchronization?
  - Are blocking operations invoked from asynchronous callbacks, event loops, or ISR contexts?
  - Could mutex acquisition order introduce lock inversion or deadlocks?
- **Execution context constraints:** In resource-constrained or embedded environments (e.g., ESP32/FreeRTOS), does the fix exceed stack limits or perform forbidden operations in interrupt contexts?

---

## Workflow

### Step 0: Load Configuration & Inputs

This skill is part of the `bug-fixer-loop` plugin. Resolve directories and read configuration:

```bash
PLUGIN_DIR="$(cd "$(dirname "<this_skill_file>")/../.." && pwd)"
STATE_DIR="<workspace_root>/.agents/plugins/bug-fixer-loop-workspace"
```

Read `${STATE_DIR}/config.json`. Extract:
- `target_repo`: Target GitHub repository (e.g., `owner/repo`)
- `spec_file_path`: Path to primary architecture/spec document (if configured)
- `additional_design_docs`: Additional architecture reference documents (if configured)
- `maintainer`: Maintainer username

If `target_repo` is empty or the config file is missing, abort with:
`CONFIG_INCOMPLETE: Plugin not configured. Run ${PLUGIN_DIR}/scripts/configure.sh first.`

Identify the target PR number:
- Provided via dispatcher prompt or user argument: `<pr_number>`.
- If unprovided, find the oldest open draft PR with label `bug-fixer`:
  ```bash
  gh pr list --repo <target_repo> --state open --draft --label "bug-fixer:system:pr" --json number,title,createdAt --jq 'sort_by(.createdAt) | .[0].number'
  ```

---

### Step 1: Ingest PR Metadata, Diff, and Design Context

1. **Query PR Details:**
   ```bash
   gh pr view <pr_number> --repo <target_repo> --json number,title,body,headRefName,baseRefName,labels,isDraft,author
   ```

2. **Retrieve PR Diff:**
   ```bash
   gh pr diff <pr_number> --repo <target_repo>
   ```

3. **Inspect Associated Issue:**
   Extract the issue number referenced in the PR body (e.g., `Closes #<issue_number>` or `Fix #<issue_number>`):
   ```bash
   python3 ${PLUGIN_DIR}/scripts/safe_ingest.py issue-view <issue_number>
   ```
   Read the problem statement and root cause to verify that the PR addresses the real problem.

4. **Consult Design Documents:**
   If `spec_file_path` or `additional_design_docs` are defined in `config.json`, review the relevant sections to verify architectural alignment.

---

### Step 2: Conduct Adversarial Review

Review every modified and added line in the diff. Evaluate each change against the 4 pillars in the **Review Rubric**:
1. Technical Debt & Design Integrity
2. Pointer Instability & Lifetime Safety
3. Memory Leaks & Resource Management
4. Architectural Constraints & Concurrency

Determine whether the PR meets the bar for approval or requires iteration.

---

### Step 2.5: Review Round Awareness

The dispatcher passes the current review round (e.g., "Review round: 3 of 5") in the prompt. Apply the appropriate standard:

- **Rounds 1–4:** Apply the full adversarial rubric. **Reject if ANY findings exist** — including minor items such as naming, missing comments, missing qualifiers, non-idiomatic patterns, or simplifiable logic. Every finding is a required fix. Do NOT approve with advisory comments.

- **Round 5 (final round):** If the ONLY remaining issues are **purely cosmetic** (formatting preferences where the project has no enforced style, comment wording preferences), approve the PR. If any substantive finding remains — even naming or missing comments — **reject**. The dispatcher will escalate to `bug-fixer:human:needs-intervention` if you reject on round 5, so clearly explain what remains and why it matters.

> **Why no advisory approvals?** In an automated system, advisory items are never actioned. Approving with outstanding items creates silent tech debt. The developer agent has full context cached — fixing minor items now costs a fraction of what it would cost to track and fix them later. Five rounds is generous; if a minor fix can't be resolved in 4 attempts, it needs human judgment.

---

### Step 3: Execute Decision Flow

#### Approval Flow (If the PR is Perfect)

If the PR is architecturally sound, introduces zero technical debt, has verified pointer/memory safety, satisfies all constraints, and includes adequate tests:

1. **Add `bug-fixer:architect:approved` label:**
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --add-label "bug-fixer:architect:approved"
   ```

2. **Mark PR "Ready for review" (remove draft status):**
   ```bash
   gh pr ready <pr_number> --repo <target_repo>
   ```

3. **Remove `bug-fixer:engineer:needs-iteration` and `bug-fixer:architect:awaiting-review` labels:**
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --remove-label "bug-fixer:engineer:needs-iteration,bug-fixer:architect:awaiting-review" 2>/dev/null || true
   ```

4. **Post Approval Review Comment:**
   ```bash
   gh pr comment <pr_number> --repo <target_repo> -b "[ARCHITECT] Approved:
   - Architectural design and module layering verified.
   - Pointer safety and memory lifecycle validated (no leaks, dangling pointers, or unsafe casts).
   - Architectural constraints and execution context requirements satisfied.
   - PR marked ready for human maintainer review."
   ```

5. **Exit cleanly.**

---

#### Rejection Flow (If Changes Are Needed)

If ANY architectural flaws, technical debt, pointer instability, memory leaks, or missing constraints are detected:

1. **Add `bug-fixer:engineer:needs-iteration` label:**
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --add-label "bug-fixer:engineer:needs-iteration"
   ```

2. **Remove `bug-fixer:architect:approved` and `bug-fixer:architect:awaiting-review` labels:**
   ```bash
   gh pr edit <pr_number> --repo <target_repo> --remove-label "bug-fixer:architect:approved,bug-fixer:architect:awaiting-review" 2>/dev/null || true
   ```

3. **Add Review Comment Detailing Required Fixes:**
   Post a clear, specific, and actionable review comment citing the exact files, lines, and reasons:
   ```bash
   gh pr comment <pr_number> --repo <target_repo> -b "[ARCHITECT] Changes Requested:

   ### Architectural Findings
   1. **<Issue Title>** (\`path/to/file:<line>\`):
      - **Violation:** <Explain why this is technical debt, pointer instability, memory leak, or constraint violation>
      - **Required Change:** <Precise architectural direction on how the developer agent must resolve it>

   2. ...

   Please address these architectural findings in the next iteration."
   ```

4. **Append Architect Conversation ID to PR Body:**
   The agent MUST use the GitHub CLI or API to append this HTML comment to the very bottom of the PR body (preserving existing content):
   ```html
   <!-- agy_arch_conv_id: <this_conversation_id> -->
   ```
   > **CRITICAL:** Replace `<this_conversation_id>` with the agent's actual Antigravity Conversation ID.

   **Execution Instructions:**
   ```bash
   # 1. Retrieve the existing PR body
   EXISTING_BODY="$(gh pr view <pr_number> --repo <target_repo> --json body --jq .body)"

   # 2. Append or replace the architect conversation ID comment while preserving all other content
   # If an earlier agy_arch_conv_id comment exists, replace it; otherwise append to the bottom:
   if echo "${EXISTING_BODY}" | grep -q "<!-- agy_arch_conv_id:"; then
     UPDATED_BODY="$(echo "${EXISTING_BODY}" | sed -E "s|<!-- agy_arch_conv_id: [^ ]+ -->|<!-- agy_arch_conv_id: <this_conversation_id> -->|")"
   else
     UPDATED_BODY="${EXISTING_BODY}

   <!-- agy_arch_conv_id: <this_conversation_id> -->"
   fi

   # 3. Update the PR body
   gh pr edit <pr_number> --repo <target_repo> --body "${UPDATED_BODY}"
   ```

5. **Exit cleanly.**

---

### Step 2.8: Evaluate Developer Discoveries

After reviewing the PR diff, check the PR body for a `### Discoveries (Out-of-Scope)` section containing `[DISCOVERY]` items flagged by the developer agent.

**Build the breadcrumb:**
1. Find the issue this PR fixes (from `Closes #N` in the PR body)
2. Read that issue's body for a `<!-- discovery_chain: ... -->` comment
3. If found: new chain = existing chain + ` → #this`
4. If not found: new chain = `#N → #this` (source issue is the root)

For each discovery:

1. **Validate the finding.** Read the referenced file and line. Is the developer's assessment accurate? Is it actually a problem?

2. **Assess severity.** Is this worth tracking as a separate issue, or is it noise?

3. **Decide:**
   - **File as issue** — if genuine and worth tracking:
     ```bash
     gh issue create --repo <target_repo> \
       --title "[Review Discovery] <concise title>" \
       --body "## Discovered During Review

     Found during architect review of PR #<pr_number> (fixing #<source_issue>).

     ## Finding
     <validated description — rewrite in your own assessment, don't just copy>

     ## Location
     - File: \`<path/to/file>\`
     - Line(s): <line range>

     ## Architect Assessment
     <your evaluation of severity and impact>

     ## Discovery Breadcrumb
     <!-- discovery_source_pr: #<pr_number> -->
     <!-- discovery_source_issue: #<source_issue> -->
     <!-- discovery_chain: <chain> -->

     ---
     _Auto-filed by architect review of PR #<pr_number>._" \
       --label "bug-fixer:triage:discovered-in-review"
     ```

   - **Dismiss** — if inaccurate, too minor, or already tracked.

4. **Report in review comment.** In your `[ARCHITECT]` review comment, add a section:
   ```
   ### Discovery Disposition
   - ✅ Filed #<N>: <title>
   - ❌ Dismissed: <reason>
   ```

**Guardrails:**
- Never add `bug-fixer:engineer:needs-fix` or any classification label — let the triage system handle classification
- If a discovery is actually related to the current fix, tell the developer to address it in the next iteration (add it to your findings list)

---

## Trigger Reference

- **Manual invocations:** "architect review", "review PR", "run architect review", "audit PR architecture", "principal architect review", "check PR for technical debt".
- **Sidecar invocation:** Triggered automatically by `architect_dispatcher.py` when an open draft PR tagged with `bug-fixer` is awaiting architect review.
