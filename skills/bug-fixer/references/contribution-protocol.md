<!--
  Bug Fixer Loop Plugin
  Skill Reference — Contribution Protocol
  Loaded by: bug-fixer skill agents
-->

# Contribution Protocol

This document covers how to contribute changes to the bug-fixer-loop plugin —
the counterpart to [`issue-management.md`](./issue-management.md) (which covers
reporting defects).

---

## Route Selection Table

Use this table to determine the appropriate workflow for your change:

| Situation | Route |
|---|---|
| Defect you cannot or should not fix | File an issue → [`issue-management.md`](./issue-management.md) |
| Defect you can fix | Follow this protocol (contribution) |
| New capability, shape is obvious and small | Follow this protocol, proposal step optional |
| New capability, shape needs agreement | Proposal step first, then contribution |
| Change to a safety rule in `AGENTS.md` | Human-only — file issue with `component:rules` |
| New skill or sidecar | Proposal required |
| Change to `safe_ingest.py` or `pre_tool_gate.py` | Proposal required (security-sensitive) |

---

## Enhancement / Proposal Process

### When a Proposal is Required vs Optional

- **Required:** Any change to security scripts (`safe_ingest.py`,
  `pre_tool_gate.py`), safety rules (`AGENTS.md`), new skills/sidecars,
  or changes affecting the triage tier classification logic.
- **Optional:** Documentation fixes, skill wording improvements, small
  isolated bug fixes with no security or classification impact.

### How to File a Proposal

File a GitHub issue on `matthewmcneill/bug-fixer-loop-plugin` with the
`P4-enhancement` label.

### Lightweight Proposal Format

Use the following structure for proposal issues:

```markdown
## Problem
<!-- What limitation, missing feature, or friction does this proposal address? -->

## Proposed Shape
<!-- Design summary, proposed changes, affected skills/scripts. -->

## Alternatives Considered
<!-- What other options were considered and why were they rejected? -->

## Affected Surface
<!-- List of files, skills, scripts, or sidecars affected. -->

## Security Impact
<!-- Does this change affect the ingestion boundary, pre-tool gate,
     or any safety rule? If yes, explain how. -->
```

---

## Protected Files — What You Cannot Touch

The following files are **human-controlled artifacts** and must never be
modified by autonomous agents:

| File | Why |
|---|---|
| `rules/AGENTS.md` | Safety invariants — human-authored, human-edited only |
| `pre_tool_gate.py` | Security enforcement — self-defense rule prevents autonomous edits |
| `safe_ingest.py` | Ingestion boundary — self-defense rule prevents autonomous edits |

> **Note:** The `pre_tool_gate.py` script actively blocks its own modification
> as a self-defense measure. Even with a proposal approved, changes to security
> scripts must be implemented by a human or with explicit human supervision.

---

## PR Mechanics

### Branch Naming

Use descriptive branch names prefixed with the change type:

```
fix/safe-ingest-unicode-titles
feat/add-milestone-inheritance-to-triage
docs/clarify-tier-classification-criteria
```

### Branching Rule

Always branch from the latest `origin/main`. Never branch from another
contributor's feature branch.

### Issue Linking

The PR description body must include `Fixes #N` referencing the parent issue
so GitHub auto-closes it upon merge.

### Test Requirements

All tests must pass before opening a PR:

```bash
# Run the full test suite
python3 -m unittest discover -s tests -v
```

Include the full test output in the PR description body.

If you add or modify a script, add corresponding test coverage in `tests/`.
The existing test files provide patterns to follow:
- [`tests/test_safe_ingest.py`](../../tests/test_safe_ingest.py) — ingestion/redaction tests
- [`tests/test_pre_tool_gate.py`](../../tests/test_pre_tool_gate.py) — hook enforcement tests

### Leak Scan

Run the leak scanner before opening the PR:

```bash
python3 scripts/pre_tool_gate.py --scan-only .
```

---

## Skill Co-Update Duty

To prevent documentation drift, any PR modifying plugin capabilities must
update corresponding skill documentation in lockstep:

| What you changed | What to update |
|---|---|
| Triage classification logic | `skills/bug-fixer-triage/SKILL.md` tier definitions |
| Engineering workflow steps | `skills/bug-fixer-engineering/SKILL.md` step descriptions |
| Config.json fields (add/remove/rename) | `README.md` Configuration Reference table |
| New sidecar or skill | `plugin.json`, `README.md` Architecture section |
| Safety rule change | `rules/AGENTS.md`, `README.md` Safety Rules Summary |
| Script interface change | Any SKILL.md that invokes the script |
| Issue/contribution process change | `skills/bug-fixer/references/` docs (this directory) |

> **Cautionary precedent:** The ESP32 App Foundation project learned this
> lesson the hard way (Issue #19) when skill docs drifted from actual API
> implementation. Co-updating skill docs with code changes is mandatory.

---

## Versioning

The plugin version is declared in `plugin.json`. Follow these rules:

- **Patch bump** (`0.1.x`): Bug fixes, doc improvements, no behaviour change.
- **Minor bump** (`0.x.0`): New skills, new config fields, new sidecar, changed
  classification logic — any functional change.
- **Major bump** (`x.0.0`): Breaking changes to config.json schema, install/uninstall
  process, or safety rules that require consumer re-configuration.

Update the version in `plugin.json` as part of your PR. The `README.md` badge
references this version.

---

## Authority & Contribution Origins

- **Security-critical files** (`safe_ingest.py`, `pre_tool_gate.py`,
  `rules/AGENTS.md`) require human implementation or direct human supervision.
  Agents may propose changes via issues but must not implement them autonomously.
- **Contributions from target repos:** If you discover a plugin defect while
  working in a target repository, file the issue on the plugin repo with the
  `found-in:<target-repo-name>` attribution label. Fixes should be branched
  in the plugin repo, not in the target repo.

---

## Related

- **Filing a defect without contributing a fix →** [`issue-management.md`](./issue-management.md)
- **File an issue when you cannot or should not act; follow this protocol when you can.**
