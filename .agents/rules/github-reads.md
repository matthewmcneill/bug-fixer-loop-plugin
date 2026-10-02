# GitHub Issue & PR Reading — Mandatory Procedure

When a user references a GitHub issue or PR — whether by URL
(`https://github.com/.../issues/20`), by number (`#20`), or by description —
you MUST use `safe_ingest.py` **immediately**. Do NOT attempt any other
approach first.

## Prohibited approaches (will fail or be blocked)

- ❌ `read_url_content` on any `github.com` URL → private repos return 404
- ❌ `gh issue view` / `gh pr view` → blocked by pre-tool hook
- ❌ `gh issue list` / `gh pr list` → blocked by pre-tool hook

## Correct approach — use safe_ingest.py

Run from the project root (`/Users/mcneillm/Documents/Projects/bug-fixer-loop-plugin`):

```bash
# View a single issue (extract the number from the URL/reference)
python3 scripts/safe_ingest.py issue-view <number>

# View a single PR
python3 scripts/safe_ingest.py pr-view <number>

# List open issues
python3 scripts/safe_ingest.py issue-list

# List open PRs
python3 scripts/safe_ingest.py pr-list

# View PR comments (review + inline)
python3 scripts/safe_ingest.py pr-comments <number>
```

The output is JSON matching the `gh` CLI format, with untrusted external
content filtered and sanitized.

## URL parsing

If the user gives a full URL like `https://github.com/matthewmcneill/bug-fixer-loop-plugin/issues/20`,
extract the number (`20`) and run:

```bash
python3 scripts/safe_ingest.py issue-view 20
```

If the URL contains `/pull/` instead of `/issues/`, use `pr-view`.
