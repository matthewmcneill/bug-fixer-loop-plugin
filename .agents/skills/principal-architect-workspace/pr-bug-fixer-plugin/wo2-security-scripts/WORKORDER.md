---
complexity: standard
parent_project: bug-fixer-plugin
work_order: 2
topic: security-scripts
branch: bug-fixer-plugin/wo2-security-scripts
base: bug-fixer-plugin/wo1-plugin-scaffold
---

# WO2: Security Scripts — Safe Ingest

## Role
You are an expert senior software engineer adapting an existing security-critical Python script for generalised, configurable use.

## Objective
Adapt the existing `gh_safe_ingest.py` (800+ lines) from the `loop-eng` reference repo into a parameterised `safe_ingest.py` for the bug-fixer-loop plugin. The adapted script must retain all core security properties (maintainer approval gate, TOCTOU verification, content redaction, self-approval blocking) while removing Google Cloud GTM-specific hardcoding and making all repo-specific values configurable.

## Strict Boundary Box

**CREATE:**
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/safe_ingest.py`
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/scripts/__init__.py`

**READ (reference — do NOT modify):**
- `/Users/mcneillm/Documents/Projects/loop-eng/scripts/gh_safe_ingest.py` — the source to adapt from
- `/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin/config.json` — the config schema

**DO NOT** modify any other files. Surface out-of-scope findings in your Completion Report.

## Context: What the Original Script Does

`gh_safe_ingest.py` is a security proxy wrapping GitHub CLI reads. It ensures untrusted external GitHub contributor content cannot enter the LLM context window without explicit maintainer approval. Key features:

1. **Command dispatcher**: Replaces raw `gh issue/pr list/view` via subcommands (`issue-list`, `issue-view`, `pr-list`, `pr-view`, `pr-comments`, `set-priority`)
2. **Maintainer approval gate**: Requires a trusted user's `approved`/`/approve` comment before external content is allowed
3. **TOCTOU verification**: Validates timestamps — issue/PR edits and force-pushes after approval invalidate it
4. **Content redaction**: Unapproved items have title/body replaced with `[REDACTED: UNAPPROVED EXTERNAL CONTENT]`
5. **GraphQL edit tracking**: Deep inspection of edit history, comments, reviews, and commits
6. **Priority resolution**: Queries GitHub Project fields for priority sorting
7. **Self-approval blocking**: Rejects bot comments, minimised comments, and self-approval attempts

## Line-Item Instructions

### 1. Configuration Loading
Add a `load_config(config_path)` function at the top of the script that reads the plugin's `config.json` and returns a dict. The script should:
- Accept `--config <path>` CLI flag (default: look for `config.json` in the script's parent directory)
- Fall back to environment variable `BUG_FIXER_CONFIG` if no flag given
- Fail with a clear error if no config found

### 2. Parameterise Trusted Users
Replace the hardcoded trusted user resolution with:
- Read `trusted_users` array from config
- Read `maintainer` from config as the primary trusted user
- If both are empty, fall back to `gh api user -q .login` (current user)

### 3. Remove Google GTM-Specific Code
Remove or replace these Google-specific elements:
- **Hardcoded Priority field IDs** (`IFSS_kgDOAj6iTg`, option IDs like `f75d1ab4`, etc.) — replace with a generic approach that discovers project field IDs dynamically via GraphQL, or skips priority enrichment entirely if no project fields are configured
- **Hardcoded org references** (`cloud-gtm`) — make org name derived from the `target_repo` config value
- **Google-specific bot prefixes** — keep the general bot-comment filtering (`[ANTIGRAVITY]`, `[CONTEXT]`, `[PR-REVIEW]`, etc.) but make the prefix list configurable. Add `[BUG-FIXER]` to the default list.

### 4. Parameterise Leak Patterns
The original has Google-internal leak patterns in the redaction logic. Replace with:
- Read `leak_patterns` array from `config.json`
- Compile them into regex at startup
- Use them in any content scanning/redaction

### 5. Retain Core Security (DO NOT weaken)
These MUST be preserved exactly:
- `is_approval_comment()` logic — regex matching, trusted user check, minimised comment rejection
- `get_latest_approval_timestamp()` — latest approval timestamp extraction
- `evaluate_and_sanitize_item()` — full TOCTOU verification chain (createdAt, lastEditedAt, commit timestamps)
- `fetch_graphql_edit_metadata()` — deep GraphQL inspection with pagination fail-closed at 100 nodes
- `sanitize_unapproved_item()` — field stripping and title/body redaction
- `REPO_SLUG_REGEX` path traversal prevention
- Quarantine label (`security:prompt-injection`) enforcement
- Exit code convention: 0=success, 1=argument error, 2=security block

### 6. Retain CLI Interface
Keep the same subcommand interface:
- `issue-list`, `issue-view`, `pr-list`, `pr-view`, `pr-comments`, `set-priority`
- Same `--repo`, `--json`, `--jq` passthrough
- Add `--config` flag

### 7. Script Header & Permissions
- Add a shebang: `#!/usr/bin/env python3`
- Add a module docstring explaining purpose, usage, and config
- Set as executable in git: the install script will `chmod 555` it

## Verification
After creating the script:
1. `python3 scripts/safe_ingest.py --help` should show usage with all subcommands
2. `python3 -c "import scripts.safe_ingest"` should import without errors
3. The script should have a `load_config()` function
4. The script should NOT contain any of: `IFSS_kgDOAj6iTg`, `cloud-gtm`, `custom-ge-demo`, `sparkforge`, `@google.com`
5. The script SHOULD contain: `is_approval_comment`, `evaluate_and_sanitize_item`, `sanitize_unapproved_item`, `REPO_SLUG_REGEX`

## Completion Report Template
```markdown
## Completion Report — WO2: Security Scripts — Safe Ingest

**Status:** PASS | FAIL
**Branch:** bug-fixer-plugin/wo2-security-scripts

### Files Created
- [ ] scripts/safe_ingest.py
- [ ] scripts/__init__.py

### Lines of Code
- safe_ingest.py: <count>

### Security Properties Retained
- [ ] Maintainer approval gate
- [ ] TOCTOU timestamp verification
- [ ] Content redaction for unapproved items
- [ ] GraphQL pagination fail-closed (100 nodes)
- [ ] Self-approval blocking
- [ ] Quarantine label enforcement
- [ ] Path traversal prevention (REPO_SLUG_REGEX)

### Google-Specific Code Removed
- [ ] No hardcoded Priority field IDs
- [ ] No `cloud-gtm` references
- [ ] No `@google.com` patterns
- [ ] No `sparkforge` paths

### Parameterisation Verified
- [ ] Config loading from file/env
- [ ] Trusted users from config
- [ ] Leak patterns from config
- [ ] Bot prefixes configurable

### Decisions Made
(list any decisions beyond the spec)

### Out-of-Scope Findings
(surface anything discovered that belongs in a later WO)
```

## Completion Report — WO2: Security Scripts — Safe Ingest

**Status:** PASS
**Branch:** bug-fixer-plugin/wo2-security-scripts

### Files Created
- [x] scripts/safe_ingest.py
- [x] scripts/__init__.py

### Lines of Code
- safe_ingest.py: 1560

### Security Properties Retained
- [x] Maintainer approval gate
- [x] TOCTOU timestamp verification
- [x] Content redaction for unapproved items
- [x] GraphQL pagination fail-closed (100 nodes)
- [x] Self-approval blocking
- [x] Quarantine label enforcement
- [x] Path traversal prevention (REPO_SLUG_REGEX)

### Google-Specific Code Removed
- [x] No hardcoded Priority field IDs
- [x] No `cloud-gtm` references
- [x] No `@google.com` patterns
- [x] No `sparkforge` paths

### Parameterisation Verified
- [x] Config loading from file/env
- [x] Trusted users from config
- [x] Leak patterns from config
- [x] Bot prefixes configurable

### Decisions Made
- Added hierarchical fallback for `config.json` discovery: `--config <path>` flag -> `BUG_FIXER_CONFIG` environment variable -> `scripts/config.json` -> plugin root `config.json` -> current working directory `config.json`.
- In `resolve_trusted_users`, retained optional compatibility with CLI `--trusted-user` and `BUG_FIXER_TRUSTED_USERS` environment variable while prioritizing `maintainer` and `trusted_users` from `config.json`, falling back to `gh api user --jq .login`.
- Implemented `compile_leak_patterns`, `redact_leaks_from_text`, and `redact_leaks_from_item` so configured regex patterns (e.g. cloud tokens, API keys, private keys) are scrubbed from ingested items (title, body, comments, reviews).
- In `set_graphql_issue_priority`, dynamically queries organization `issueFields` to find the `Priority` singleSelect field ID and option IDs instead of relying on hardcoded IDs, raising a descriptive error if the field is not present.

### Out-of-Scope Findings
- `tests/test_gh_safe_ingest.py` from `loop-eng` contains 1,091 lines of comprehensive unit tests. An adapted test suite for `safe_ingest.py` belongs in WO6 (`wo6-tests-and-docs`) as planned.

