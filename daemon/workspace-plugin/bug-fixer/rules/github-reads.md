# GitHub Issue & PR Access — Mandatory Procedure

Both `safe_ingest.py` (reads) and `safe_post.py` (writes) live in this
plugin's `scripts/` directory. If a raw `gh` command is denied, the gate's
error message includes the exact command with the full installed path.

When a user references a GitHub issue or PR — whether by URL
(`https://github.com/.../issues/20`), by number (`#20`), or by description —
you MUST use `safe_ingest.py` **immediately**. Do NOT attempt any other
approach first.

## Prohibited approaches (will fail or be blocked)

- ❌ `read_url_content` on any `github.com` URL → private repos return 404
- ❌ `gh issue view` / `gh pr view` → blocked by pre-tool hook
- ❌ `gh issue list` / `gh pr list` → blocked by pre-tool hook

## Correct approach — use safe_ingest.py

When the hook blocks a prohibited approach, the deny message includes the exact
command to use. Run it with `run_command` from the repository root (safe_ingest
selects the repo's config from the working directory):

```bash
# View a single issue (extract the number from the URL/reference)
python3 safe_ingest.py issue-view <number>

# View a single PR
python3 safe_ingest.py pr-view <number>

# List open issues
python3 safe_ingest.py issue-list

# List open PRs
python3 safe_ingest.py pr-list

# View PR comments (review + inline)
python3 safe_ingest.py pr-comments <number>
```

The output is JSON matching the `gh` CLI format, with untrusted external
content filtered and sanitized.

## URL parsing

If the user gives a full URL like `https://github.com/owner/repo/issues/20`,
extract the number (`20`) and run:

```bash
python3 safe_ingest.py issue-view 20
```

If the URL contains `/pull/` instead of `/issues/`, use `pr-view`.

## GitHub Writes — use safe_post.py

All GitHub write operations (comments, reviews, issue creation) MUST go through
`safe_post.py`. This proxy scans outbound content for leaked secrets before
posting. The gate scans all outbound `gh` content for leaked secrets and blocks
unscannable payloads. Use `safe_post.py` to ensure your content is scanned
before posting.

```bash
# Post a PR review comment
python3 safe_post.py pr-review <number> --repo <owner/repo> --body "Review text here"

# Post an issue comment
python3 safe_post.py issue-comment <number> --repo <owner/repo> --body "Comment text"

# Post a PR comment
python3 safe_post.py pr-comment <number> --repo <owner/repo> --body "Comment text"
```

Run from the repository root. The `--repo` flag is required. The proxy
accepts the same flags as `gh` for body text (`--body`, `--body-file`),
scans for configured leak patterns, and calls `gh` only if the scan passes.
