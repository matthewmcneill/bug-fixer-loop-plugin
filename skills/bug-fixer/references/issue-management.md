<!--
  Bug Fixer Loop Plugin
  Skill Reference — Issue Management Process
  Loaded by: bug-fixer skill agents
-->

# Issue Management Process

GitHub Issues on **`matthewmcneill/bug-fixer-loop-plugin`** are the canonical
system of record for all plugin defects. This document tells you everything
you need to raise, track, link, and close a bug-fixer-loop issue.

---

## Plugin Issues vs Target Repo Issues

Not every problem belongs on the plugin repo. Use this table to decide where
to file:

| Symptom | File against |
|---|---|
| Triage misclassifies an issue (wrong tier) | `matthewmcneill/bug-fixer-loop-plugin` |
| `safe_ingest.py` false-positive redacts valid content | `matthewmcneill/bug-fixer-loop-plugin` |
| `pre_tool_gate.py` blocks a legitimate operation | `matthewmcneill/bug-fixer-loop-plugin` |
| Tick sidecar crashes, orphans a claim, or opens a broken PR | `matthewmcneill/bug-fixer-loop-plugin` |
| Trajectory review misidentifies a failure pattern | `matthewmcneill/bug-fixer-loop-plugin` |
| Test quality audit rubric is too strict/lenient | `matthewmcneill/bug-fixer-loop-plugin` |
| `configure.sh` or `install.sh` fails or misconfigures | `matthewmcneill/bug-fixer-loop-plugin` |
| Sidecar cron schedule is wrong or sidecars don't fire | `matthewmcneill/bug-fixer-loop-plugin` |
| A bug in the **target project's code** (not the plugin) | The target repo configured in `config.json` |
| A bug-fixer PR contains a wrong fix for a target repo issue | The target repo (review feedback on the PR) |

> **Rule of thumb:** If the problem is in *how the agent operates*, file it
> on the plugin repo. If the problem is in *what the agent produced*, address
> it via PR review on the target repo.

---

## Label Taxonomy

Apply labels at filing time. Every issue gets one severity label, plus any
applicable attribution/meta labels.

### Severity (choose one)

| Label | When to use |
|---|---|
| `P1-blocker` | Plugin is non-functional — sidecars crash, claims orphan, PRs fail to open |
| `P2-correctness` | Plugin operates but produces wrong results — misclassification, incorrect exclusion zones, false-positive blocks |
| `P3-docs-dx` | Confusing docs, misleading skill instructions, cosmetic issues, unclear error messages |
| `P4-enhancement` | New capability or improvement — not a defect |

### Component (apply all that fit)

| Label | When to use |
|---|---|
| `component:triage` | Bug is in the triage skill or triage sidecar logic |
| `component:tick` | Bug is in the tick/fixer skill or tick sidecar logic |
| `component:trajectory` | Bug is in the trajectory review skill or sidecar |
| `component:safe-ingest` | Bug is in `safe_ingest.py` ingestion or redaction |
| `component:pre-tool-gate` | Bug is in `pre_tool_gate.py` hook enforcement |
| `component:install-config` | Bug is in `install.sh`, `configure.sh`, or `uninstall.sh` |
| `component:test-quality` | Bug is in the test quality audit rubric |
| `component:rules` | Bug relates to safety rules (AGENTS.md) being too strict/lenient |

### Attribution / meta (apply all that fit)

| Label | When to use |
|---|---|
| `found-in:<target-repo-name>` | Issue was surfaced while the plugin operated against a specific target repo (e.g. `found-in:esp32-app-foundation`). Create the label if it doesn't exist yet. |
| `backfill` | Historical issue filed after the fix was already merged; close immediately |

> **Important for agents:** The `found-in:` label must match the target repo
> the plugin was operating against when the issue was discovered. Do not copy
> a label from an example without checking it matches your context.

---

## How to File an Issue

### Identity Rule

File under whatever GitHub identity your `gh` CLI is currently authenticated
as — that is your true author identity for this action. Before filing, confirm
which account that is:

```bash
gh auth status
# Shows which account you will file as. Make sure it is correct.
```

Do not attempt to impersonate another user. File as yourself.

### Issue Title Format

```
[Component] One-line description of the defect
```

Examples:
- `[safe_ingest] Maintainer approval gate rejects issues with unicode titles`
- `[triage] Small-scope override not applied to doc-only issues in security module`
- `[tick] Orphaned claim not cleaned up when transcript is missing`
- `[install] configure.sh fails silently when gh CLI is not authenticated`

### Issue Body Template

```markdown
## Symptom
<!-- What goes wrong. What the user or agent sees or doesn't see. -->

## Evidence
<!-- File paths, line numbers, error output, sidecar logs. -->
<!-- Format: `scripts/safe_ingest.py:142` -->

## Transcript Context
<!-- If the issue was discovered during a sidecar run, include:
     - Conversation ID of the affected session
     - Relevant transcript excerpt (last 5-10 steps)
     - Any error messages from the agent log -->

## Config Context
<!-- Relevant fields from config.json that may affect reproduction. -->
<!-- Redact any secrets or tokens. -->
```json
{
  "target_repo": "<repo>",
  "max_concurrent_prs": 1,
  "test_command": "<command>"
}
```

## Found Against Commit
<!-- The plugin commit SHA where the bug was confirmed. -->
`<SHA>`

## Suggested Fix
<!-- Optional but helpful. Concrete enough to act on. -->

## Consumer Context
<!-- Which target repo was the plugin operating against? -->
Found in: <target-repo-name>, <sidecar or session context>
```

### Filing Command

```bash
gh issue create \
  --repo matthewmcneill/bug-fixer-loop-plugin \
  --title "[Component] Description" \
  --body-file issue-body.md \
  --label "P2-correctness,component:tick,found-in:<target-repo-name>"
```

If the `found-in:` label does not exist on the repo yet, create it first:

```bash
gh label create "found-in:<target-repo-name>" \
  --repo matthewmcneill/bug-fixer-loop-plugin \
  --color "a855f7" \
  --description "Discovered while operating against <target-repo-name>"
```

---

## How Fixes Close Issues

The fixing PR's description must include:

```
Fixes #N
```

GitHub auto-closes the issue when the PR merges to `main`. No manual closing
is needed for issues resolved this way.

---

## Backfill Convention

When a defect was fixed before a GitHub issue was created (e.g. discovered
retrospectively from a PR or audit):

1. File the issue using the standard body template.
2. Add the `backfill` label alongside the severity and component labels.
3. In the body, include: `**Resolved in:** commit \`<SHA>\`` (or PR #M).
4. **Immediately close** the issue with reason "completed".

This preserves history and lets consumers link their context to a real
issue number without leaving a ghost open issue on the board.

```bash
# Create a backfill issue and immediately close it
NUM=$(gh issue create \
  --repo matthewmcneill/bug-fixer-loop-plugin \
  --title "[Component] Description" \
  --body-file body.md \
  --label "P2-correctness,component:tick,found-in:<target-repo-name>,backfill" | grep -oE '[0-9]+$')

gh issue close "$NUM" \
  --repo matthewmcneill/bug-fixer-loop-plugin \
  --reason completed \
  --comment "Resolved in commit \`<SHA>\` (merged in PR #M, <date>)."
```

---

## Related

- **Contributing a fix or proposing a new capability →** [`contribution-protocol.md`](./contribution-protocol.md)
- **File an issue when you cannot or should not act; follow the contribution protocol when you can.**
