# Bug Fixer Threat Model & Security Scope

The bug-fixer exists to fix bugs. Its security controls exist to stop **its own
autonomous agents** from being turned against the maintainer. They are not a
general-purpose policy layer for GitHub, for the maintainer's other repositories,
or for the maintainer's own interactive sessions.

This note defines what we defend, against whom, and where the line is. New gate
rules and hardening PRs should be checked against it.

## The risk we're defending

Autonomous agents started by the sidecars:

- read **untrusted text** (issue bodies, PR descriptions, comments, CI logs);
- act with the **maintainer's GitHub credentials** and the **maintainer's machine**;
- run **unattended**, with no human reviewing each tool call.

The main threat is **prompt injection**: text an outsider controls steering an
agent into acting against the maintainer. A secondary threat is an agent that
makes a mistake and then "helpfully" works around a guardrail.

## Assets

| Asset | Why it matters |
|---|---|
| Secrets on the machine and in the repo | Leaking them is the worst realistic outcome |
| The review chain (architect approval, human merge) | It's what stops bad code reaching `main` |
| The bug-fixer's own controls (gate scripts, hooks, sidecar config, plugin manifests) | If an agent can edit these, every other control falls |
| The target repo's default branch | Changes should only land through the review chain |

## Invariants for autonomous sessions

These must hold in every autonomous session in an enrolled repo, whatever an
agent is told:

1. **No self-approval.** An agent can't approve, merge or otherwise mark its own
   work as accepted, directly or through a proxy. That includes posting
   `[ARCHITECT] Approved` from a session that wasn't dispatched as architect, and
   adding trust-escalating labels.
2. **No secret egress.** Secrets don't leave the machine through GitHub writes,
   pushed commits or fetched URLs carrying them.
3. **No self-modification.** Agents can't change the daemon's scripts, hooks,
   manifests, sidecar config or `.agents/plugins.json`, by any route.
4. **No unreviewed injection.** Untrusted GitHub content reaches agents only
   through `safe_ingest.py`, after trust filtering, visible-content normalisation,
   and prompt-injection scanning.

Everything else an agent does in its worktree (editing code, running tests,
opening PRs) is normal work and shouldn't be gated.

## Ingress defence-in-depth (stopping prompt injection at the source)

Because every downstream risk (secret exfiltration, review-chain bypass, or
guardrail tampering) begins when untrusted GitHub text steers an agent, the
primary control surface is **ingress**:

1. **Channel enforcement (`pre_tool_gate.py` + `github_url_gate.py`):** Raw
   `gh issue/pr list|view|status|diff`, `gh run view|watch`, `gh search`,
   `gh api` reads of issues/PRs/comments/reviews, and `read_url_content`
   requests targeting `github.com`, `www.github.com`, or `api.github.com`
   issue/PR endpoints are blocked in enrolled repos. Agents must read GitHub
   data through `safe_ingest.py`.
2. **Trust & temporal approval gating (`safe_ingest.py`):** Untrusted issue and
   PR bodies are redacted unless a trusted maintainer explicitly approved the
   item *after* its latest edit (`POST_APPROVAL_EDIT` detection), and untrusted
   comments and reviews are stripped before output.
3. **Visible content normalisation (`normalise_visible_content`):** HTML
   comments, collapsed `<details>` blocks, invisible image `alt`/`title`
   attributes, markdown reference-link titles, and zero-width, bidirectional, or
   Unicode tag characters are stripped before text reaches either the maintainer
   during triage or the agent during execution.
4. **Centralised prompt-injection scanning (`injection_scanner.py`):** Every
   item, comment, review, inline review comment, and PR diff flowing through
   `safe_ingest.py` (`issue-view`, `issue-list`, `pr-view`, `pr-list`,
   `pr-comments`, `pr-diff`) as well as the sidecar dispatchers
   (`triage_dispatcher.py`, `engineering_dispatcher.py`,
   `architect_dispatcher.py`) is scanned against an always-on heuristic floor
   (`BUILTIN_INJECTION_PATTERNS`) plus optional Prompt Shield / LLM-as-judge
   backends, quarantining injected content before any agent sees it.

## Trust zones

| Zone | What applies |
|---|---|
| **Autonomous session, enrolled repo** | Full gate: all four invariants, ingress channel enforcement, workflow rules, `[ARCHITECT]` prefix restriction to `architect_review` sessions, and egress scanning. |
| **Interactive session, enrolled repo** | Self-modification protection, secret-egress scanning, and mandatory `safe_ingest.py` / `github_url_gate.py` ingress protection (so interactive issue/PR inspection in an enrolled repo still gets HTML/Unicode normalisation and prompt-injection scanning). |
| **Unenrolled repo** | Self-modification protection of bug-fixer files and credential-disclosure blocking (`gh auth token`) only. Workflow rules (`gh pr merge`, `gh pr review --approve`, raw `gh` reads, approval-comment blocks) do not apply, and malformed sidecar configs for other workspaces do not lock unenrolled workspaces. |

How sessions are classified:

- **Autonomous** = `ANTIGRAVITY_SOURCE_METADATA` marks a sidecar,
  `BUG_FIXER_SESSION_TYPE` is set, or the conversation ID matches a dispatch
  record.
- **Enrolled** = a sidecar `config.json` whose `repo_dir` contains the workspace.

A malformed sidecar config fails closed **for that enrolled workspace**, not
every workspace on the machine.

## Where each control should live

Use the strongest, simplest layer that can enforce an invariant, in this order:

1. **GitHub platform controls**, preferred because they can't be bypassed with
   shell syntax:
   - **Branch protection on `main`, requiring a human review.** Agents can't
     merge whatever command they find.
   - **A separate bot identity** (bot account or fine-grained token scoped to the
     enrolled repo, no merge or admin rights). This makes "trusted author" mean
     something again: an agent's `[ARCHITECT]` comment is no longer indistinguishable
     from the maintainer's.
   - **Secret scanning with push protection.** It checks the commits actually
     pushed, on the server side.
2. **Host isolation:** a VM, container or network policy for general exfiltration
   (`read_url_content`, MCP tools, anything that reaches the network). A regex
   gate can't solve this, and shouldn't try.
3. **The PreToolUse gate (`pre_tool_gate.py`):** only for what the platform can't
   see. That means protecting local files, scanning outbound content before it
   leaves, and keeping raw untrusted reads behind `safe_ingest.py`. Keep it thin.
4. **`integrity_monitor.py`:** a backstop that catches indirect writes to the
   bug-fixer's own files that the gate didn't recognise.

## Non-goals and accepted risks

- **General GitHub policy.** Blocking `gh pr merge` or `gh pr review --approve` in
  unenrolled repos or interactive sessions is out of scope.
- **Complete shell analysis.** The gate will never parse every bash construct.
  Where an invariant matters, back it with a platform or isolation control, not
  more parsing.
- **Exfiltration of non-secret content** (for example source code fetched by URL
  to an arbitrary host) isn't prevented by the gate. That's a job for host network
  isolation, and the README says so.
- **A malicious maintainer, or a compromised maintainer machine.** Out of scope.

## Checklist for new gate rules

Before adding a rule to `pre_tool_gate.py`:

1. **Which invariant does it protect?** If none, it doesn't belong in the gate.
2. **Can a platform or isolation control enforce it instead?** If so, use that.
   Add a gate rule only for defence in depth, with a comment saying so.
3. **Is it scoped to the right zone?** Autonomous + enrolled by default.
4. **What happens when it misfires?** A false positive should cost the bug-fixer
   a tick, not lock the maintainer's other work.
5. **Is there a test for the bypass you're closing,** and one showing that
   ordinary work in the worktree isn't blocked?

## Applying this to the current hardening stack (PRs #71–#74)

As of 2026-10-13 (#74 stacked on #73), measured against this note:

| Item | PR | Decision |
|---|---|---|
| `[ARCHITECT] Approved` can be posted from any autonomous session, and the dispatcher trusts it because agents post as the maintainer | #71 / #74 | **Implemented in #74 (invariant 1).** Long-term: bot identity. Short-term: `pre_tool_gate.py` and `safe_post.py` restrict the `[ARCHITECT]` prefix to `architect_review` sessions. |
| `safe_ingest.py`, `architect_dispatcher.py` PR diffs, and `triage_dispatcher.py` pending-approval summaries lacked centralised prompt-injection scanning | #71 / #74 | **Implemented in #74 (invariant 4).** `injection_scanner.py` enforces an always-on heuristic floor (`BUILTIN_INJECTION_PATTERNS`) wired into `safe_ingest.py` (`issue-view`, `issue-list`, `pr-view`, `pr-list`, `pr-comments`, `pr-diff`), `architect_dispatcher.py`, and `triage_dispatcher.py`; raw `gh pr diff` and `gh run view\|watch` are blocked in `pre_tool_gate.py`. |
| `github_url_gate.py` missed `api.github.com` / `www.github.com` and failed open on malformed stdin | #71 / #74 | **Implemented in #74 (invariant 4).** Expanded URL regex to cover `www.github.com` and `api.github.com/repos/.../(issues\|pulls)` and made non-TTY stdin fail closed. |
| `git commit -nm` skips hooks; the bundled-flag check misses it | #72 / #74 | **Implemented in #74.** Updated bundled short-flag check in `_check_git` to catch `-n` combined with `-m` (`-nm`, `-anm`). |
| Push scan checks the working tree, not the pushed commits | #72 | **Fix, or replace with GitHub push protection** (invariant 2). |
| `.agents/` excluded from leak scanning | #72 / #74 | **Implemented in #74.** Narrowed `DEFAULT_REGEX_EXCLUDE_PATHS` from `.agents/` to `.agents/skills/principal-architect-workspace/`. |
| Receipt forgery by writing `.git/leak_audit_receipt.json` | #72 | **Drop the receipt mechanism.** The push scan already does this job. |
| Merge, approve and approval-injection blocks applied to unenrolled repos | #73 / #74 | **Implemented in #74.** Added `REASON_AUTO_MERGE`, `REASON_SELF_APPROVAL`, and `REASON_APPROVAL_INJECTION` to `_workflow_reasons` so unenrolled repos only enforce self-protection and credential-disclosure blocking. |
| One malformed sidecar config denies every tool call machine-wide | #73 / #74 | **Implemented in #74.** Scoped sidecar config parse errors in `load_config` to fail closed only when `workspace_paths` matches that sidecar's `repo_dir`. |
| `read_url_content` still reaches arbitrary hosts in autonomous sessions | #73 | **Accept and document**; host isolation's job. |
