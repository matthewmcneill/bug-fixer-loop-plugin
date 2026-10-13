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
   through `safe_ingest.py`, after trust filtering.

Everything else an agent does in its worktree (editing code, running tests,
opening PRs) is normal work and shouldn't be gated.

## Trust zones

| Zone | What applies |
|---|---|
| **Autonomous session, enrolled repo** | Full gate: all four invariants, workflow rules, egress scanning |
| **Interactive session, enrolled repo** | Self-modification protection and secret-egress scanning only. A human is in the loop, so workflow rules (raw `gh` reads, merge/approve blocks) don't apply. |
| **Unenrolled repo** | Self-modification protection only. The bug-fixer has no business in these repos. |

How sessions are classified:

- **Autonomous** = `ANTIGRAVITY_SOURCE_METADATA` marks a sidecar, or the
  conversation ID matches a dispatch record.
- **Enrolled** = a sidecar `config.json` whose `repo_dir` contains the workspace.

A malformed sidecar config should fail closed **for the bug-fixer**, not lock
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

## Applying this to the current hardening stack (PRs #71–#73)

As of 2026-10-13 (#73 head `0aca142`), measured against this note:

| Item | PR | Decision |
|---|---|---|
| `[ARCHITECT] Approved` can be posted from any autonomous session, and the dispatcher trusts it because agents post as the maintainer | #71 | **Fix (invariant 1).** Long-term: bot identity. Short-term: only architect-dispatched sessions may post the `[ARCHITECT]` prefix. |
| `git commit -nm` skips hooks; the bundled-flag check misses it | #72 | **Fix.** One line; autonomous sessions only. |
| Push scan checks the working tree, not the pushed commits | #72 | **Fix, or replace with GitHub push protection** (invariant 2). |
| `.agents/` excluded from leak scanning | #72 | **Narrow** to the specific example files. |
| Receipt forgery by writing `.git/leak_audit_receipt.json` | #72 | **Drop the receipt mechanism.** The push scan already does this job. |
| Merge, approve and approval-injection blocks applied to unenrolled repos | #73 | **Revert** to unenrolled = self-protection only. |
| One malformed sidecar config denies every tool call machine-wide | #73 | **Scope** the failure to the bug-fixer. |
| `read_url_content` still reaches arbitrary hosts in autonomous sessions | #73 | **Accept and document**; host isolation's job. |
