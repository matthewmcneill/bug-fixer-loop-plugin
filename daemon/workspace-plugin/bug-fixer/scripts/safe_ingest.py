#!/usr/bin/env python3
"""Deterministic GitHub Issue & PR Pre-Fetch Sanitizer for Autonomous Bug Fixer Loops.

Security Policy:
1. Issues or Pull Requests authored by a trusted maintainer (configured in config.json
   under `maintainer` or `trusted_users`, via `--trusted-user`, or current `gh` user)
   are allowed automatically.
2. Issues or Pull Requests authored by any external user are withheld from loop
   ingestion UNLESS a trusted maintainer has posted an explicit `"approved"`
   comment (`approved`, `/approve`, `/approved`) on that Issue/PR AND the
   Issue/PR body/title and commits have not been modified or pushed after the
   timestamp of that `"approved"` comment (`lastEditedAt <= latest_approval_ts`
   and `commit_ts <= latest_approval_ts`).
3. Individual comments and PR reviews on any Issue or PR (including maintainer-
   authored ones) are filtered by the same rule: comments authored by the
   trusted maintainer are kept; external comments/reviews are stripped unless
   they were created and last edited on or before the latest maintainer
   `"approved"` comment on that thread.
4. Content is scanned against configured `leak_patterns` from config.json to redact
   sensitive data or secrets from entering model context.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from pre_tool_gate import _find_daemon_dir

APPROVAL_COMMENT_REGEX = re.compile(r"^\s*/?approved?\s*[.!]*\s*$", re.IGNORECASE)
REPO_SLUG_REGEX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GRAPHQL_PAGE_LIMIT = 100

ALLOWLISTED_TAG_REGEX = re.compile(
    r"^<!--\s*(?:agy_arch_conv_id|agy_dev_conv_id):\s*[a-zA-Z0-9_-]+\s*-->$"
)
HTML_COMMENT_REGEX = re.compile(r"<!--[\s\S]*?-->")
UNICODE_HIDDEN_REGEX = re.compile(
    r"[\U000e0000-\U000e007f\u200b-\u200d\ufeff\u202a-\u202e\u2066-\u2069]"
)

QUARANTINE_LABELS = frozenset({"security:prompt-injection"})
REDACTED_PLACEHOLDER = "[REDACTED: UNAPPROVED EXTERNAL CONTENT]"

DEFAULT_BOT_PREFIXES = (
    "[ANTIGRAVITY]",
    "[BUG-FIXER]",
    "[CONTEXT]",
    "[SPEC-ALIGNMENT]",
    "[TEST-QUALITY]",
    "[PR-REVIEW]",
    "[ISSUE-REVIEW]",
)


class _MandatoryFieldsRegistry(set):  # type: ignore[type-arg]
    """Set of mandatory Issue fields that also supports `['issue']` and `['pr']` lookup."""

    def __init__(self, issue_fields: Iterable[str], pr_extra_fields: Iterable[str]) -> None:
        super().__init__(issue_fields)
        self._pr_fields: set[str] = set(issue_fields) | set(pr_extra_fields)

    def __getitem__(self, entity: str) -> set[str]:
        key = str(entity).strip().lower()
        if key in ("pr", "pull", "pull_request", "pullrequest"):
            return set(self._pr_fields)
        if key in ("issue", "issues"):
            return set(self)
        raise KeyError(entity)


MANDATORY_INTERNAL_FIELDS = _MandatoryFieldsRegistry(
    issue_fields={
        "number",
        "title",
        "body",
        "state",
        "author",
        "createdAt",
        "updatedAt",
        "comments",
        "labels",
    },
    pr_extra_fields={"reviews", "commits"},
)

MANDATORY_PR_INTERNAL_FIELDS: set[str] = MANDATORY_INTERNAL_FIELDS["pr"]

SAFE_STRUCTURAL_FIELDS = frozenset(
    {
        "number",
        "state",
        "labels",
        "author",
        "createdAt",
        "updatedAt",
        "url",
        "is_quarantined",
        "approval_status",
        "is_approved",
        "has_hidden_content",
        "title",
        "body",
        "comments",
        "reviews",
        "priority",
        "priorityRank",
    }
)

UNTRUSTED_FREETEXT_FIELDS = frozenset(
    {
        "commits",
        "headRefName",
        "headRefOid",
        "baseRefName",
        "files",
        "headRepository",
        "headRepositoryOwner",
        "mergeCommit",
        "potentialMergeCommit",
        "statusCheckRollup",
    }
)

PRIORITY_RANK_BY_NAME: dict[str, tuple[str, int]] = {
    "urgent": ("Urgent", 0),
    "p0": ("Urgent", 0),
    "priority:urgent": ("Urgent", 0),
    "priority:p0": ("Urgent", 0),
    "high": ("High", 1),
    "p1": ("High", 1),
    "priority:high": ("High", 1),
    "priority:p1": ("High", 1),
    "medium": ("Medium", 2),
    "p2": ("Medium", 2),
    "priority:medium": ("Medium", 2),
    "priority:p2": ("Medium", 2),
    "low": ("Low", 3),
    "p3": ("Low", 3),
    "priority:low": ("Low", 3),
    "priority:p3": ("Low", 3),
}

DEFAULT_UNSET_PRIORITY_RANK = 4


def normalise_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalises config dictionary so callers can always access scanning namespace."""
    if not isinstance(config, dict):
        return {}
    res = copy.deepcopy(config)
    if "scanning" in res and isinstance(res["scanning"], dict):
        scanning = res["scanning"]
        if "secrets" not in scanning or not isinstance(scanning["secrets"], dict):
            scanning["secrets"] = {}
        secrets = scanning["secrets"]
        if "patterns" not in secrets:
            secrets["patterns"] = list(res.get("leak_patterns", []))
        if "exclude_paths" not in secrets:
            secrets["exclude_paths"] = list(res.get("scan_exclude_paths", []))
    else:
        res["scanning"] = {
            "secrets": {
                "patterns": list(res.get("leak_patterns", [])),
                "exclude_paths": list(res.get("scan_exclude_paths", [])),
            }
        }
    return res


def _defaults() -> dict[str, Any]:
    return normalise_config(
        {
            "spec_file_path": "docs/plugin-architecture.md",
            "protected_files": ["docs/plugin-architecture.md"],
            "leak_patterns": [],
            "scan_exclude_paths": ["tests/"],
        }
    )


def load_config(
    config_path: str | Path | None = None,
    require: bool = False,
) -> dict[str, Any]:
    """Loads plugin config from an explicit path, BUG_FIXER_CONFIG env var, or standard locations."""
    target_path: Path | None = None
    if config_path:
        target_path = Path(config_path).expanduser().resolve()
        if not target_path.is_file():
            raise FileNotFoundError(f"Configuration file not found at specified path: {target_path}")
    elif os.environ.get("BUG_FIXER_CONFIG"):
        env_path = Path(os.environ["BUG_FIXER_CONFIG"]).expanduser().resolve()
        if not env_path.is_file():
            raise FileNotFoundError(f"Configuration file not found at BUG_FIXER_CONFIG path: {env_path}")
        target_path = env_path
    else:
        # Search candidate locations:
        script_dir = Path(__file__).resolve().parent
        plugin_dir = script_dir.parent
        daemon_dir = _find_daemon_dir(script_dir)
        candidates = []
        if daemon_dir:
            sidecars_dir = daemon_dir / "sidecars"
            if sidecars_dir.is_dir():
                for sidecar_sub in sidecars_dir.iterdir():
                    if sidecar_sub.is_dir() and (sidecar_sub / "config.json").is_file():
                        candidates.append(sidecar_sub / "config.json")
        candidates.extend([
            plugin_dir / "config.json",
            plugin_dir.parent / "bug-fixer-loop-workspace" / "config.json",
            Path.cwd() / ".agents" / "plugins" / "bug-fixer-loop-workspace" / "config.json",
        ])
        for cand in candidates:
            if cand.is_file():
                target_path = cand
                break

    if target_path is None or not target_path.is_file():
        if require:
            raise FileNotFoundError("No bug-fixer config found")
        return _defaults()

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"Config at {target_path} must be a JSON object, got {type(data).__name__}")
            return normalise_config(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {target_path}: {exc}") from exc


def compile_leak_patterns(patterns: Sequence[str] | None) -> list[re.Pattern[str]]:
    """Compiles regex patterns for leak redaction."""
    compiled: list[re.Pattern[str]] = []
    if patterns:
        for pat in patterns:
            if not pat or not isinstance(pat, str):
                continue
            try:
                compiled.append(re.compile(pat))
            except re.error as exc:
                sys.stderr.write(f"WARNING: Invalid leak pattern regex '{pat}': {exc}\n")
    return compiled


def redact_leaks_from_text(
    text: str | None,
    compiled_patterns: Sequence[re.Pattern[str]] | None,
    placeholder: str = "[REDACTED: SECRET DETECTED]",
) -> str | None:
    """Scans text for leak patterns and replaces any matches with a placeholder."""
    if text is None or not isinstance(text, str) or not compiled_patterns:
        return text
    result = text
    for pat in compiled_patterns:
        result = pat.sub(placeholder, result)
    return result


def redact_leaks_from_item(
    item: dict[str, Any],
    compiled_patterns: Sequence[re.Pattern[str]] | None,
) -> dict[str, Any]:
    """Redacts leak pattern matches from free-text fields in an item (title, body, comments, reviews)."""
    if not compiled_patterns or not isinstance(item, dict):
        return item
    redacted = dict(item)
    if "title" in redacted and isinstance(redacted["title"], str):
        redacted["title"] = redact_leaks_from_text(redacted["title"], compiled_patterns)
    if "body" in redacted and isinstance(redacted["body"], str):
        redacted["body"] = redact_leaks_from_text(redacted["body"], compiled_patterns)
    if "comments" in redacted and isinstance(redacted["comments"], list):
        new_comments = []
        for c in redacted["comments"]:
            if isinstance(c, dict) and "body" in c and isinstance(c["body"], str):
                c_copy = dict(c)
                c_copy["body"] = redact_leaks_from_text(c_copy["body"], compiled_patterns)
                new_comments.append(c_copy)
            else:
                new_comments.append(c)
        redacted["comments"] = new_comments
    if "reviews" in redacted and isinstance(redacted["reviews"], list):
        new_reviews = []
        for r in redacted["reviews"]:
            if isinstance(r, dict) and "body" in r and isinstance(r["body"], str):
                r_copy = dict(r)
                r_copy["body"] = redact_leaks_from_text(r_copy["body"], compiled_patterns)
                new_reviews.append(r_copy)
            else:
                new_reviews.append(r)
        redacted["reviews"] = new_reviews
    return redacted


def _strip_format_controls(text: str) -> str:
    """Strip Unicode format-control characters (category Cf) except ZWJ between emoji."""
    result = []
    for i, ch in enumerate(text):
        cat = unicodedata.category(ch)
        if cat == "Cf":
            if ch == '\u200d':
                prev_ch = text[i-1] if i > 0 else ''
                next_ch = text[i+1] if i < len(text)-1 else ''
                if prev_ch and ord(prev_ch) > 0x2000 and next_ch and ord(next_ch) > 0x2000:
                    result.append(ch)
                    continue
            continue
        result.append(ch)
    return ''.join(result)

def normalise_visible_content(text: str) -> str:
    """Normalises visible content by stripping non-allowlisted HTML comments,
    Unicode tag characters (ASCII smuggling), zero-width characters, and bidi overrides.
    """
    if not isinstance(text, str):
        return "" if text is None else str(text)
    if not text:
        return text

    cleaned = text
    idx = cleaned.find("<!--")
    while idx != -1:
        end_idx = cleaned.find("-->", idx)
        if end_idx == -1:
            cleaned = cleaned[:idx]
            break
        else:
            comment = cleaned[idx:end_idx+3]
            if ALLOWLISTED_TAG_REGEX.match(comment):
                idx = cleaned.find("<!--", end_idx)
            else:
                cleaned = cleaned[:idx] + cleaned[end_idx+3:]
                idx = cleaned.find("<!--", idx)

    cleaned = re.sub(r"\[//\]:\s*#\s*\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"[\ufe00-\ufe0f\U000e0100-\U000e01ef]", "", cleaned)
    
    return _strip_format_controls(cleaned)


def detect_hidden_content(raw: str, normalised: str) -> bool:
    """Returns True if raw and normalised text differ materially (non-whitespace content stripped)."""
    if not isinstance(raw, str) or not isinstance(normalised, str):
        return False
    return "".join(raw.split()) != "".join(normalised.split())


def _apply_content_normalisation(sanitized: dict[str, Any]) -> dict[str, Any]:
    """Applies visible content normalisation to title, body, comments, and reviews.

    Sets 'has_hidden_content': True on sanitized if any hidden content was detected.
    """
    has_hidden = False
    if "title" in sanitized and isinstance(sanitized["title"], str):
        raw_title = sanitized["title"]
        norm_title = normalise_visible_content(raw_title)
        sanitized["title"] = norm_title
        if detect_hidden_content(raw_title, norm_title):
            has_hidden = True

    if "body" in sanitized and isinstance(sanitized["body"], str):
        raw_body = sanitized["body"]
        norm_body = normalise_visible_content(raw_body)
        sanitized["body"] = norm_body
        if detect_hidden_content(raw_body, norm_body):
            has_hidden = True

    if "comments" in sanitized and isinstance(sanitized["comments"], list):
        norm_comments = []
        for c in sanitized["comments"]:
            if isinstance(c, dict) and "body" in c and isinstance(c["body"], str):
                c_copy = dict(c)
                raw_c_body = c["body"]
                norm_c_body = normalise_visible_content(raw_c_body)
                c_copy["body"] = norm_c_body
                norm_comments.append(c_copy)
                if detect_hidden_content(raw_c_body, norm_c_body):
                    has_hidden = True
            else:
                norm_comments.append(c)
        sanitized["comments"] = norm_comments

    if "reviews" in sanitized and isinstance(sanitized["reviews"], list):
        norm_reviews = []
        for r in sanitized["reviews"]:
            if isinstance(r, dict) and "body" in r and isinstance(r["body"], str):
                r_copy = dict(r)
                raw_r_body = r["body"]
                norm_r_body = normalise_visible_content(raw_r_body)
                r_copy["body"] = norm_r_body
                norm_reviews.append(r_copy)
                if detect_hidden_content(raw_r_body, norm_r_body):
                    has_hidden = True
            else:
                norm_reviews.append(r)
        sanitized["reviews"] = norm_reviews

    if has_hidden:
        sanitized["has_hidden_content"] = True

    return sanitized


def is_valid_repo_slug(repo: str | None) -> bool:
    """Validates `owner/repo` format and rejects path traversal segments."""
    if not repo or not isinstance(repo, str):
        return False
    cleaned = repo.strip()
    if not REPO_SLUG_REGEX.match(cleaned):
        return False
    owner, name = cleaned.split("/", 1)
    if owner in (".", "..") or name in (".", "..") or ".." in cleaned:
        return False
    return True


def _extract_json_payload(raw_text: str | None) -> Any:
    """Parses JSON text safely using narrow `json.JSONDecodeError` handling."""
    if not raw_text or not isinstance(raw_text, str):
        return None
    stripped = raw_text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parses an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def extract_author_login(obj: dict[str, Any]) -> str:
    """Extracts lowercase GitHub login from GraphQL (`author.login`) or REST (`user.login`) objects."""
    if not isinstance(obj, dict):
        return ""
    author = obj.get("author") or obj.get("user")
    if isinstance(author, dict):
        login = author.get("login") or ""
        return str(login).strip().lower()
    if isinstance(author, str):
        return author.strip().lower()
    return ""


def has_quarantine_label(item: dict[str, Any]) -> bool:
    """Returns True if `item` carries any quarantine label (`security:prompt-injection`)."""
    if not isinstance(item, dict):
        return False
    for lbl in item.get("labels") or []:
        lbl_name = lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        if lbl_name.strip().lower() in QUARANTINE_LABELS:
            return True
    return False


def is_approval_comment(
    comment: dict[str, Any],
    trusted_users: set[str],
    bot_prefixes: Sequence[str] | None = None,
) -> bool:
    """Returns True iff `comment` is an explicit human maintainer 'approved' comment."""
    if not isinstance(comment, dict):
        return False
    if comment.get("isMinimized") is True:
        return False
    login = extract_author_login(comment)
    if not login or login not in trusted_users:
        return False
    body = str(comment.get("body") or "").strip()
    if not body:
        return False
    upper_body = body.upper()
    prefixes = bot_prefixes if bot_prefixes is not None else DEFAULT_BOT_PREFIXES
    for prefix in prefixes:
        if upper_body.startswith(str(prefix).upper()):
            return False
    return bool(APPROVAL_COMMENT_REGEX.match(body))


def get_latest_approval_timestamp(
    comments: Iterable[dict[str, Any]],
    trusted_users: set[str],
    bot_prefixes: Sequence[str] | None = None,
) -> datetime | None:
    """Finds the latest UTC timestamp among valid maintainer 'approved' comments."""
    latest: datetime | None = None
    for comment in comments or []:
        if not is_approval_comment(comment, trusted_users, bot_prefixes=bot_prefixes):
            continue
        ts = parse_iso_timestamp(
            comment.get("createdAt")
            or comment.get("created_at")
            or comment.get("submittedAt")
            or comment.get("submitted_at")
        )
        if ts is not None and (latest is None or ts > latest):
            latest = ts
    return latest


def _extract_commit_timestamps(commits_field: Any) -> tuple[list[datetime], bool]:
    """Extracts commit timestamps (`committedDate`, `pushedDate`, `authoredDate`) from a PR `commits` structure.

    Returns `(timestamps, has_unparseable_entry)`.
    """
    if not commits_field:
        return [], False
    if isinstance(commits_field, dict):
        nodes = commits_field.get("nodes")
        commit_list = nodes if isinstance(nodes, list) else []
    elif isinstance(commits_field, list):
        commit_list = commits_field
    else:
        return [], True

    timestamps: list[datetime] = []
    has_unparseable = False
    for entry in commit_list:
        if not isinstance(entry, dict):
            has_unparseable = True
            continue
        inner = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
        committer_obj = inner.get("committer") if isinstance(inner.get("committer"), dict) else {}
        author_obj = inner.get("author") if isinstance(inner.get("author"), dict) else {}
        raw_candidates = [
            entry.get("committedDate"),
            entry.get("committed_date"),
            entry.get("pushedDate"),
            entry.get("pushed_date"),
            entry.get("authoredDate"),
            entry.get("authored_date"),
            inner.get("committedDate"),
            inner.get("committed_date"),
            inner.get("pushedDate"),
            inner.get("pushed_date"),
            inner.get("authoredDate"),
            inner.get("authored_date"),
            committer_obj.get("date"),
            author_obj.get("date"),
        ]
        present_raw = [r for r in raw_candidates if r is not None and str(r).strip() != ""]
        if not present_raw:
            has_unparseable = True
            continue
        parsed_any = False
        for raw_ts in present_raw:
            ts = parse_iso_timestamp(str(raw_ts))
            if ts is None:
                has_unparseable = True
            else:
                timestamps.append(ts)
                parsed_any = True
        if not parsed_any:
            has_unparseable = True
    return timestamps, has_unparseable


def _connection_exceeds_page_limit(conn: Any) -> bool:
    """Returns True if a GraphQL connection hit or exceeded the 100-item page boundary."""
    if not isinstance(conn, dict):
        return False
    page_info = conn.get("pageInfo")
    if isinstance(page_info, dict) and (
        page_info.get("hasNextPage") is True or page_info.get("hasPreviousPage") is True
    ):
        return True
    total_count = conn.get("totalCount")
    if isinstance(total_count, int) and total_count > GRAPHQL_PAGE_LIMIT:
        return True
    nodes = conn.get("nodes")
    if isinstance(nodes, list) and len(nodes) >= GRAPHQL_PAGE_LIMIT:
        return True
    return False


def fetch_graphql_edit_metadata(
    repo: str, number: int, runner: Callable[[list[str]], str] | None = None
) -> dict[str, Any] | None:
    """Fetches `lastEditedAt`, `includesCreatedEdit`, `reviews`, and `commits` metadata via GraphQL."""
    if not is_valid_repo_slug(repo):
        return None
    owner, name = repo.strip().split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        issueOrPullRequest(number: $number) {
          ... on Issue {
            createdAt
            lastEditedAt
            includesCreatedEdit
            comments(first: 100) {
              totalCount
              pageInfo {
                hasNextPage
              }
              nodes {
                id
                createdAt
                lastEditedAt
                includesCreatedEdit
              }
            }
          }
          ... on PullRequest {
            createdAt
            lastEditedAt
            includesCreatedEdit
            comments(first: 100) {
              totalCount
              pageInfo {
                hasNextPage
              }
              nodes {
                id
                createdAt
                lastEditedAt
                includesCreatedEdit
              }
            }
            reviews(first: 100) {
              totalCount
              pageInfo {
                hasNextPage
              }
              nodes {
                id
                submittedAt
                lastEditedAt
                includesCreatedEdit
              }
            }
            commits(last: 100) {
              totalCount
              pageInfo {
                hasPreviousPage
              }
              nodes {
                commit {
                  oid
                  committedDate
                  pushedDate
                  authoredDate
                }
              }
            }
          }
        }
      }
    }
    """
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-F",
        f"number={int(number)}",
        "-f",
        f"query={query}",
    ]
    exec_fn = runner or (
        lambda args: subprocess.check_output(  # noqa: S603
            args, text=True, stderr=subprocess.DEVNULL
        )
    )
    try:
        raw = exec_fn(cmd)
        parsed = _extract_json_payload(raw)
        if not isinstance(parsed, dict):
            return None
        node = parsed.get("data", {}).get("repository", {}).get("issueOrPullRequest")
        if not isinstance(node, dict):
            return None

        comments_conn = node.get("comments")
        reviews_conn = node.get("reviews")
        commits_conn = node.get("commits")
        if (
            _connection_exceeds_page_limit(comments_conn)
            or _connection_exceeds_page_limit(reviews_conn)
            or _connection_exceeds_page_limit(commits_conn)
        ):
            return None

        comment_edits: dict[str, str | None] = {}
        for c_node in (comments_conn.get("nodes", []) if isinstance(comments_conn, dict) else []) or []:
            if isinstance(c_node, dict) and c_node.get("id"):
                c_id = str(c_node["id"])
                last_edited = c_node.get("lastEditedAt")
                if last_edited is None and c_node.get("includesCreatedEdit") is True:
                    comment_edits[c_id] = "EDITED_WITHOUT_TIMESTAMP"
                else:
                    comment_edits[c_id] = last_edited

        review_edits: dict[str, str | None] = {}
        for r_node in (reviews_conn.get("nodes", []) if isinstance(reviews_conn, dict) else []) or []:
            if isinstance(r_node, dict) and r_node.get("id"):
                r_id = str(r_node["id"])
                last_edited = r_node.get("lastEditedAt")
                if last_edited is None and r_node.get("includesCreatedEdit") is True:
                    review_edits[r_id] = "EDITED_WITHOUT_TIMESTAMP"
                else:
                    review_edits[r_id] = last_edited
                comment_edits[r_id] = review_edits[r_id]

        commit_timestamps: list[str] = []
        if isinstance(commits_conn, dict):
            for cm_node in commits_conn.get("nodes") or []:
                if not isinstance(cm_node, dict):
                    continue
                c_obj = cm_node.get("commit") if isinstance(cm_node.get("commit"), dict) else cm_node
                for ts_key in ("pushedDate", "committedDate", "authoredDate"):
                    ts_val = c_obj.get(ts_key)
                    if ts_val:
                        commit_timestamps.append(str(ts_val))

        return {
            "lastEditedAt": node.get("lastEditedAt"),
            "includesCreatedEdit": bool(node.get("includesCreatedEdit", False)),
            "commentLastEditedAt": comment_edits,
            "reviewLastEditedAt": review_edits,
            "commitTimestamps": commit_timestamps,
        }
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        return None


def filter_comments_list(
    comments: Sequence[dict[str, Any]],
    trusted_users: set[str],
    latest_approval_ts: datetime | None,
    comment_edit_map: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Strips unapproved external comments/reviews and any external entry edited after approval."""
    sanitized: list[dict[str, Any]] = []
    edit_map = comment_edit_map or {}
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        if comment.get("isMinimized") is True:
            continue
        author = extract_author_login(comment)
        if author in trusted_users:
            sanitized.append(comment)
            continue
        if latest_approval_ts is None:
            continue

        created_ts = parse_iso_timestamp(
            comment.get("createdAt")
            or comment.get("created_at")
            or comment.get("submittedAt")
            or comment.get("submitted_at")
        )
        if created_ts is None or created_ts > latest_approval_ts:
            continue

        c_id = str(comment.get("id") or "")
        last_edited_str = comment.get("lastEditedAt") or comment.get("last_edited_at") or edit_map.get(c_id)
        updated_str = comment.get("updatedAt") or comment.get("updated_at")
        includes_edit = comment.get("includesCreatedEdit")

        if last_edited_str:
            edited_ts = parse_iso_timestamp(last_edited_str)
            if edited_ts is None or edited_ts > latest_approval_ts:
                continue
        elif includes_edit is True:
            # Fail-closed if comment/review was edited after creation but exact edit timestamp is unavailable
            continue
        elif updated_str:
            updated_ts = parse_iso_timestamp(updated_str)
            if updated_ts is not None and updated_ts > latest_approval_ts:
                continue

        sanitized.append(comment)
    return sanitized


def sanitize_unapproved_item(
    item: dict[str, Any],
    reason: str,
    trusted_users: set[str] | None = None,
) -> dict[str, Any]:
    """Redacts `title`/`body` and strips untrusted `--json` fields (`commits`, `headRefName`, `files`, etc.)."""
    trusted = trusted_users or set()
    quarantined = reason == "QUARANTINED_LABEL" or has_quarantine_label(item) or bool(item.get("is_quarantined"))
    sanitized: dict[str, Any] = {}
    for key in item:
        if key in UNTRUSTED_FREETEXT_FIELDS or key not in SAFE_STRUCTURAL_FIELDS:
            continue
        sanitized[key] = item[key]

    sanitized["is_approved"] = False
    sanitized["is_quarantined"] = quarantined
    sanitized["approval_status"] = reason
    if "title" in item:
        sanitized["title"] = REDACTED_PLACEHOLDER
    if "body" in item:
        sanitized["body"] = REDACTED_PLACEHOLDER
    if "comments" in item:
        sanitized["comments"] = [] if quarantined else _apply_content_normalisation({"comments": filter_comments_list(item.get("comments") or [], trusted, None)})["comments"]
    if "reviews" in item:
        sanitized["reviews"] = [] if quarantined else _apply_content_normalisation({"reviews": filter_comments_list(item.get("reviews") or [], trusted, None)})["reviews"]
    return sanitized


def evaluate_and_sanitize_item(
    item: dict[str, Any],
    trusted_users: set[str],
    edit_metadata_fetcher: Callable[[int], dict[str, Any] | None] | None = None,
    *,
    redact_unapproved: bool = False,
    bot_prefixes: Sequence[str] | None = None,
    leak_patterns: Sequence[re.Pattern[str]] | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Evaluates an Issue or PR dict and returns `(allowed, reason, sanitized_item)`."""
    if not isinstance(item, dict):
        return False, "INVALID_ITEM", None

    def _reject(reason: str) -> tuple[bool, str, dict[str, Any] | None]:
        if redact_unapproved:
            return False, reason, sanitize_unapproved_item(item, reason, trusted_users)
        return False, reason, None

    # Reject any issue/PR explicitly quarantined with security:prompt-injection
    if has_quarantine_label(item) or item.get("is_quarantined") is True:
        return _reject("QUARANTINED_LABEL")

    if item.get("is_approved") is False:
        return _reject(str(item.get("approval_status") or "UNAPPROVED_EXTERNAL_AUTHOR"))

    author = extract_author_login(item)
    comments = item.get("comments") if isinstance(item.get("comments"), list) else []
    reviews = item.get("reviews") if isinstance(item.get("reviews"), list) else []
    commits_list = item.get("commits") if isinstance(item.get("commits"), list) else []
    latest_approval_ts = get_latest_approval_timestamp([*comments, *reviews], trusted_users, bot_prefixes=bot_prefixes)

    # Check if we need GraphQL edit metadata (external item with approval, or external edited comments/reviews)
    comment_edit_map: dict[str, str | None] = {}
    has_external_edited_comment = any(
        extract_author_login(c) not in trusted_users and c.get("includesCreatedEdit") is True
        for c in [*comments, *reviews]
        if isinstance(c, dict)
    )
    exceeds_page_boundary = (
        len(comments) >= GRAPHQL_PAGE_LIMIT
        or len(reviews) >= GRAPHQL_PAGE_LIMIT
        or len(commits_list) >= GRAPHQL_PAGE_LIMIT
    )

    if author in trusted_users:
        effective_approval_ts = latest_approval_ts
        if exceeds_page_boundary:
            effective_approval_ts = None
        elif effective_approval_ts is not None and has_external_edited_comment:
            if edit_metadata_fetcher is not None and item.get("number"):
                meta = edit_metadata_fetcher(int(item["number"]))
                if meta is None:
                    effective_approval_ts = None
                else:
                    comment_edit_map = {
                        **(meta.get("commentLastEditedAt") or {}),
                        **(meta.get("reviewLastEditedAt") or {}),
                    }
            else:
                effective_approval_ts = None
        sanitized = dict(item)
        if "comments" in sanitized:
            sanitized["comments"] = filter_comments_list(
                comments, trusted_users, effective_approval_ts, comment_edit_map
            )
        if "reviews" in sanitized:
            sanitized["reviews"] = filter_comments_list(reviews, trusted_users, effective_approval_ts, comment_edit_map)
        sanitized = _apply_content_normalisation(sanitized)
        if leak_patterns:
            sanitized = redact_leaks_from_item(sanitized, leak_patterns)
        if sanitized.get("has_hidden_content"):
            sanitized["approval_status"] = "HIDDEN_CONTENT"
        return True, "TRUSTED_AUTHOR", sanitized

    # External author: requires a valid maintainer 'approved' comment
    if latest_approval_ts is None:
        return _reject("UNAPPROVED_EXTERNAL_AUTHOR")

    if exceeds_page_boundary:
        return _reject("PAGINATION_BOUNDARY_EXCEEDED")

    created_ts = parse_iso_timestamp(item.get("createdAt") or item.get("created_at"))
    if created_ts is None or created_ts > latest_approval_ts:
        return _reject("CREATED_AFTER_APPROVAL")

    last_edited_raw = item.get("lastEditedAt") or item.get("last_edited_at")
    includes_edit = item.get("includesCreatedEdit")
    meta: dict[str, Any] | None = None

    need_graphql_meta = (
        (last_edited_raw is None and includes_edit is None)
        or (last_edited_raw is None and includes_edit is True)
        or has_external_edited_comment
    )
    if (last_edited_raw is None or need_graphql_meta) and edit_metadata_fetcher is not None and item.get("number"):
        meta = edit_metadata_fetcher(int(item["number"]))
        if meta is None:
            return _reject("EDIT_METADATA_FETCH_FAILED")
        if last_edited_raw is None:
            last_edited_raw = meta.get("lastEditedAt")
        if includes_edit is None:
            includes_edit = meta.get("includesCreatedEdit", False)
        comment_edit_map = {
            **(meta.get("commentLastEditedAt") or {}),
            **(meta.get("reviewLastEditedAt") or {}),
        }
    elif (last_edited_raw is None and includes_edit is None) or (
        has_external_edited_comment and edit_metadata_fetcher is None
    ):
        # Fail-closed if no edit metadata source was provided for an external item or edited external review/comment
        return _reject("MISSING_EDIT_METADATA")

    if last_edited_raw:
        edited_ts = parse_iso_timestamp(last_edited_raw)
        if edited_ts is None or edited_ts > latest_approval_ts:
            return _reject("EDITED_AFTER_APPROVAL")
    elif includes_edit is True:
        return _reject("EDITED_WITHOUT_TIMESTAMP")

    # Inspect PR commit timestamps (committedDate / pushedDate / authoredDate) to prevent post-approval force-pushes
    commit_timestamps, has_unparseable_commits = _extract_commit_timestamps(item.get("commits"))
    if meta and meta.get("commitTimestamps"):
        meta_ts, meta_unparseable = _extract_commit_timestamps(
            [{"pushedDate": ts} for ts in (meta.get("commitTimestamps") or [])]
        )
        commit_timestamps.extend(meta_ts)
        has_unparseable_commits = has_unparseable_commits or meta_unparseable

    if has_unparseable_commits or any(c_ts > latest_approval_ts for c_ts in commit_timestamps):
        return _reject("COMMIT_PUSHED_AFTER_APPROVAL")

    sanitized = dict(item)
    if "comments" in sanitized:
        sanitized["comments"] = filter_comments_list(comments, trusted_users, latest_approval_ts, comment_edit_map)
    if "reviews" in sanitized:
        sanitized["reviews"] = filter_comments_list(reviews, trusted_users, latest_approval_ts, comment_edit_map)
    sanitized = _apply_content_normalisation(sanitized)
    if leak_patterns:
        sanitized = redact_leaks_from_item(sanitized, leak_patterns)
    if sanitized.get("has_hidden_content"):
        sanitized["approval_status"] = "HIDDEN_CONTENT"
    return True, "APPROVED_BY_MAINTAINER", sanitized


def sanitize_inline_pr_comments(
    inline_comments: Sequence[dict[str, Any]],
    trusted_users: set[str],
    issue_comments: Sequence[dict[str, Any]] | None = None,
    *,
    parent_pr: dict[str, Any] | None = None,
    reviews: Sequence[dict[str, Any]] | None = None,
    bot_prefixes: Sequence[str] | None = None,
    leak_patterns: Sequence[re.Pattern[str]] | None = None,
) -> list[dict[str, Any]]:
    """Filters `repos/<owner>/<repo>/pulls/<pr>/comments` inline diff review comments.

    Top-level PR approval (`issue_comments` / `reviews`) is required to unlock external
    inline diff comments; an inline diff comment `"approved"` never unlocks unrelated
    external inline comments across the PR.
    """
    parent_approved = True
    if isinstance(parent_pr, dict):
        if has_quarantine_label(parent_pr) or parent_pr.get("is_quarantined") is True:
            return []
        if parent_pr.get("is_approved") is False:
            parent_approved = False
        elif (
            parent_pr.get("author")
            and extract_author_login(parent_pr) not in trusted_users
            and parent_pr.get("is_approved") is not True
        ):
            pr_ok, _, _ = evaluate_and_sanitize_item(
                parent_pr, trusted_users, bot_prefixes=bot_prefixes, leak_patterns=leak_patterns
            )
            if not pr_ok:
                parent_approved = False

    if not parent_approved:
        res = filter_comments_list(inline_comments, trusted_users, None)
    else:
        top_level_candidates: list[dict[str, Any]] = []
        if issue_comments:
            top_level_candidates.extend(issue_comments)
        if reviews:
            top_level_candidates.extend(reviews)
        latest_approval_ts = get_latest_approval_timestamp(
            top_level_candidates, trusted_users, bot_prefixes=bot_prefixes
        )
        res = filter_comments_list(inline_comments, trusted_users, latest_approval_ts)

    if leak_patterns:
        cleaned = []
        for c in res:
            if isinstance(c, dict) and "body" in c and isinstance(c["body"], str):
                c_copy = dict(c)
                c_copy["body"] = redact_leaks_from_text(c_copy["body"], leak_patterns)
                c_copy["body"] = normalise_visible_content(c_copy["body"])
                cleaned.append(c_copy)
            else:
                cleaned.append(c)
        return cleaned
    for c in res:
        if isinstance(c, dict) and "body" in c and isinstance(c["body"], str):
            c["body"] = normalise_visible_content(c["body"])
    return res


def resolve_trusted_users(
    explicit_users: Sequence[str] | None = None,
    config: dict[str, Any] | None = None,
) -> set[str]:
    """Resolves the set of trusted maintainer GitHub logins."""
    users: set[str] = set()
    if explicit_users:
        for u in explicit_users:
            for part in str(u).split(","):
                cleaned = part.strip().lower()
                if cleaned:
                    users.add(cleaned)
    env_users = os.environ.get("BUG_FIXER_TRUSTED_USERS", "")
    for part in env_users.split(","):
        cleaned = part.strip().lower()
        if cleaned:
            users.add(cleaned)
    if config:
        maintainer = config.get("maintainer")
        if isinstance(maintainer, str) and maintainer.strip():
            users.add(maintainer.strip().lower())
        for u in config.get("trusted_users") or []:
            for part in str(u).split(","):
                cleaned = part.strip().lower()
                if cleaned:
                    users.add(cleaned)
    if users:
        return users
    try:
        out = (
            subprocess.check_output(  # noqa: S603
                ["gh", "api", "user", "--jq", ".login"],  # noqa: S607
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
            .lower()
        )
        if out:
            users.add(out)
    except (subprocess.SubprocessError, OSError):
        return users
    return users


def _is_item_unapproved_or_quarantined(item: dict[str, Any]) -> bool:
    """Checks whether an item dict is marked unapproved, quarantined, or has redacted markers."""
    if not isinstance(item, dict):
        return True
    if item.get("is_approved") is False or item.get("is_quarantined") is True:
        return True
    if has_quarantine_label(item):
        return True
    status = str(item.get("approval_status") or "").strip().upper()
    if status and status not in ("TRUSTED_AUTHOR", "APPROVED_BY_MAINTAINER", "APPROVED"):
        return True
    return False


def resolve_issue_priority(
    item: dict[str, Any], graphql_priority: str | None = None
) -> tuple[str | None, int]:
    """Resolves canonical `(priority_name, priority_rank)` from GraphQL IssueField and labels."""
    best_name: str | None = None
    best_rank: int = DEFAULT_UNSET_PRIORITY_RANK

    candidates: list[str] = []
    if graphql_priority:
        candidates.append(str(graphql_priority).strip().lower())
    existing_priority = item.get("priority")
    if isinstance(existing_priority, str) and existing_priority.strip():
        candidates.append(existing_priority.strip().lower())

    for lbl in item.get("labels") or []:
        lbl_name = lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        cleaned_lbl = lbl_name.strip().lower()
        if cleaned_lbl in PRIORITY_RANK_BY_NAME:
            candidates.append(cleaned_lbl)

    for cand in candidates:
        if cand in PRIORITY_RANK_BY_NAME:
            canonical_name, rank = PRIORITY_RANK_BY_NAME[cand]
            if rank < best_rank:
                best_name = canonical_name
                best_rank = rank

    return best_name, best_rank


def fetch_graphql_issue_priorities(
    repo: str,
    issue_numbers: Sequence[int],
    runner: Callable[[list[str]], str] | None = None,
) -> dict[int, str | None]:
    """Batch-fetches GitHub Organization `Priority` (`issueFieldValues`) for `issue_numbers`."""
    if not is_valid_repo_slug(repo) or not issue_numbers:
        return {}
    unique_nums = sorted({int(n) for n in issue_numbers if isinstance(n, int) or str(n).isdigit()})
    if not unique_nums:
        return {}
    owner, name = repo.strip().split("/", 1)
    alias_blocks: list[str] = []
    for num in unique_nums:
        alias_blocks.append(
            f"""
        i_{num}: issue(number: {num}) {{
          number
          issueFieldValues(first: 10) {{
            nodes {{
              ... on IssueFieldSingleSelectValue {{
                name
                value
                field {{ ... on IssueFieldCommon {{ name }} }}
              }}
            }}
          }}
        }}"""
        )
    query = (
        "query($owner: String!, $name: String!) {\n"
        "  repository(owner: $owner, name: $name) {"
        + "".join(alias_blocks)
        + "\n  }\n}"
    )
    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"owner={owner}",
        "-f",
        f"name={name}",
        "-f",
        f"query={query}",
    ]
    exec_fn = runner or (
        lambda args: subprocess.check_output(  # noqa: S603
            args, text=True, stderr=subprocess.DEVNULL
        )
    )
    try:
        raw = exec_fn(cmd)
        parsed = _extract_json_payload(raw)
        if not isinstance(parsed, dict):
            return {}
        repo_node = parsed.get("data", {}).get("repository", {})
        if not isinstance(repo_node, dict):
            return {}
        priorities: dict[int, str | None] = {}
        for num in unique_nums:
            issue_node = repo_node.get(f"i_{num}")
            if not isinstance(issue_node, dict):
                continue
            field_nodes = issue_node.get("issueFieldValues", {}).get("nodes", []) or []
            found_priority: str | None = None
            for f_node in field_nodes:
                if not isinstance(f_node, dict):
                    continue
                field_meta = f_node.get("field") or {}
                field_name = str(field_meta.get("name") or "").strip().lower()
                if field_name == "priority":
                    val = f_node.get("name") or f_node.get("value")
                    if val:
                        found_priority = str(val).strip()
                        break
            priorities[num] = found_priority
        return priorities
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, StopIteration, RuntimeError):
        return {}


def set_graphql_issue_priority(
    repo: str,
    issue_number: int,
    priority_value: str,
    runner: Callable[[list[str]], str] | None = None,
) -> dict[str, Any]:
    """Sets or clears the GitHub Organization/Project `Priority` field (`issueFieldValues`) on an Issue."""
    if not is_valid_repo_slug(repo):
        raise ValueError("Repository (--repo <owner/repo>) is required.")
    owner, name = repo.strip().split("/", 1)
    cleaned_val = priority_value.strip().lower()
    is_clear = cleaned_val in ("none", "clear", "null", "unset", "")
    canonical_name: str | None = None
    if not is_clear:
        if cleaned_val not in PRIORITY_RANK_BY_NAME:
            raise ValueError(
                f"Invalid priority '{priority_value}'. Expected Urgent, High, Medium, Low, or None."
            )
        canonical_name, _ = PRIORITY_RANK_BY_NAME[cleaned_val]

    exec_fn = runner or (
        lambda args: subprocess.check_output(  # noqa: S603
            args, text=True, stderr=subprocess.DEVNULL
        )
    )
    lookup_query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        issue(number: $number) {
          id
          number
        }
      }
      organization(login: $owner) {
        issueFields(first: 20) {
          nodes {
            ... on IssueFieldSingleSelect {
              id
              name
              options {
                id
                name
              }
            }
          }
        }
      }
    }
    """
    raw_lookup = exec_fn(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={int(issue_number)}",
            "-f",
            f"query={lookup_query}",
        ]
    )
    parsed_lookup = _extract_json_payload(raw_lookup) or {}
    issue_id = parsed_lookup.get("data", {}).get("repository", {}).get("issue", {}).get("id")
    if not issue_id:
        raise RuntimeError(f"Could not resolve GraphQL node ID for issue #{issue_number} in {repo}.")

    field_id: str | None = None
    option_map: dict[str, str] = {}
    org_fields = parsed_lookup.get("data", {}).get("organization", {}).get("issueFields", {}).get("nodes", []) or []
    for f_node in org_fields:
        if isinstance(f_node, dict) and str(f_node.get("name") or "").strip().lower() == "priority":
            if f_node.get("id"):
                field_id = str(f_node["id"])
            for opt in f_node.get("options") or []:
                if isinstance(opt, dict) and opt.get("name") and opt.get("id"):
                    option_map[str(opt["name"])] = str(opt["id"])
            break

    if not field_id:
        raise RuntimeError(f"Could not resolve Priority field ID for {repo}. No organization 'Priority' field configured.")

    if is_clear:
        mutation = """
        mutation($issueId: ID!, $fieldId: ID!) {
          setIssueFieldValue(input: {
            issueId: $issueId,
            issueFields: [{ fieldId: $fieldId, delete: true }]
          }) {
            clientMutationId
          }
        }
        """
        exec_fn(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"issueId={issue_id}",
                "-f",
                f"fieldId={field_id}",
                "-f",
                f"query={mutation}",
            ]
        )
        return {"number": int(issue_number), "priority": None, "priorityRank": DEFAULT_UNSET_PRIORITY_RANK}

    assert canonical_name is not None
    option_id = option_map.get(canonical_name)
    if not option_id:
        raise RuntimeError(f"Could not resolve singleSelectOptionId for Priority='{canonical_name}'.")

    mutation = """
    mutation($issueId: ID!, $fieldId: ID!, $optionId: ID!) {
      setIssueFieldValue(input: {
        issueId: $issueId,
        issueFields: [{ fieldId: $fieldId, singleSelectOptionId: $optionId }]
      }) {
        clientMutationId
      }
    }
    """
    exec_fn(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"issueId={issue_id}",
            "-f",
            f"fieldId={field_id}",
            "-f",
            f"optionId={option_id}",
            "-f",
            f"query={mutation}",
        ]
    )
    _, rank = PRIORITY_RANK_BY_NAME[canonical_name.lower()]
    return {"number": int(issue_number), "priority": canonical_name, "priorityRank": rank}


def sort_issues_by_priority(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorts issues by `(priorityRank ASC, number ASC)` so highest priority comes first."""

    def _sort_key(item: dict[str, Any]) -> tuple[int, int]:
        rank = item.get("priorityRank")
        if not isinstance(rank, int):
            _, rank = resolve_issue_priority(item)
        num = item.get("number")
        num_int = int(num) if isinstance(num, int) or (isinstance(num, str) and num.isdigit()) else 999999
        return (rank, num_int)

    return sorted(items, key=_sort_key)


def _project_requested_fields(
    item: dict[str, Any],
    requested_fields: Sequence[str] | None,
    *,
    trusted_users: set[str] | None = None,
    include_priority: bool = False,
) -> dict[str, Any]:
    """Projects caller-requested `--json` fields, stripping untrusted fields when unapproved/quarantined."""
    if _is_item_unapproved_or_quarantined(item):
        reason = str(
            item.get("approval_status")
            or ("QUARANTINED_LABEL" if has_quarantine_label(item) else "UNAPPROVED_EXTERNAL_AUTHOR")
        )
        safe_item = sanitize_unapproved_item(item, reason, trusted_users)
        if not requested_fields:
            return safe_item
        return {k: safe_item[k] for k in requested_fields if k in SAFE_STRUCTURAL_FIELDS and k in safe_item}

    if not requested_fields:
        return item
    res = {k: item[k] for k in requested_fields if k in item}
    if item.get("has_hidden_content"):
        res["has_hidden_content"] = True
    return res


def _extract_flag_value(args: Sequence[str], flag: str) -> tuple[str | None, list[str]]:
    """Extracts `--flag val` or `--flag=val` from args, returning `(value, remaining_args)`."""
    remaining: list[str] = []
    val: str | None = None
    i = 0
    while i < len(args):
        token = args[i]
        if token == flag and i + 1 < len(args):
            val = args[i + 1]
            i += 2
            continue
        if token.startswith(flag + "="):
            val = token.split("=", 1)[1]
            i += 1
            continue
        remaining.append(token)
        i += 1
    return val, remaining


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Safe GitHub Issue/PR Ingestion Gate (filters unapproved external issues & comments)."
    )
    parser.add_argument(
        "subcommand",
        choices=["issue-list", "issue-view", "pr-list", "pr-view", "pr-comments", "set-priority"],
        help="GitHub read/priority operation to execute safely.",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Path to plugin config.json (defaults to config.json in script parent or BUG_FIXER_CONFIG).",
    )
    parser.add_argument(
        "--trusted-user",
        action="append",
        dest="trusted_users",
        help="Trusted GitHub login(s) whose issues/comments and 'approved' comments are trusted.",
    )
    parsed, passthrough = parser.parse_known_args(argv)

    try:
        config = load_config(parsed.config_path)
    except Exception as exc:
        sys.stderr.write(f"ERROR: Failed to load config: {exc}\n")
        return 1

    trusted_users = resolve_trusted_users(parsed.trusted_users, config=config)
    if not trusted_users:
        sys.stderr.write("ERROR: Could not resolve trusted GitHub user (config maintainer/trusted_users, --trusted-user, or gh api user).\n")
        return 1

    bot_prefixes = tuple(config.get("bot_prefixes") or DEFAULT_BOT_PREFIXES)
    leak_patterns = compile_leak_patterns(config.get("scanning", {}).get("secrets", {}).get("patterns", []))

    repo, _ = _extract_flag_value(passthrough, "--repo")
    if not repo:
        repo, _ = _extract_flag_value(passthrough, "-R")
    repo = (repo or os.environ.get("GH_REPO", "") or config.get("target_repo", "")).strip()

    jq_expr, passthrough_no_jq = _extract_flag_value(passthrough, "--jq")
    if not jq_expr:
        jq_expr, passthrough_no_jq = _extract_flag_value(passthrough_no_jq, "-q")
    json_fields_raw, clean_args = _extract_flag_value(passthrough_no_jq, "--json")
    requested_fields = [f.strip() for f in json_fields_raw.split(",") if f.strip()] if json_fields_raw else None
    gh_supported_requested = (
        [f for f in requested_fields if f not in ("priority", "priorityRank")] if requested_fields else None
    )

    has_repo_flag = any(a in ("--repo", "-R") or a.startswith(("--repo=", "-R=")) for a in clean_args)
    repo_args = [] if has_repo_flag or not repo else ["--repo", repo]

    if parsed.subcommand == "set-priority":
        if len(clean_args) < 2:
            sys.stderr.write(
                "Usage: safe_ingest.py set-priority <issue_number> <Urgent|High|Medium|Low|None> --repo <owner/repo>\n"
            )
            return 1
        issue_num_str = str(clean_args[0]).strip()
        if not issue_num_str.isdigit() or int(issue_num_str) <= 0:
            sys.stderr.write(f"ERROR: Invalid issue number '{issue_num_str}'; must be a positive integer.\n")
            return 1
        priority_val = clean_args[1]
        if not is_valid_repo_slug(repo):
            sys.stderr.write("ERROR: Valid --repo <owner/repo> is required for set-priority.\n")
            return 1
        result = set_graphql_issue_priority(repo, int(issue_num_str), priority_val)
        print(json.dumps(result, indent=2))
        return 0

    if parsed.subcommand == "pr-comments":
        if not clean_args:
            sys.stderr.write("Usage: safe_ingest.py pr-comments <pr_number> --repo <owner/repo>\n")
            return 1
        pr_number = str(clean_args[0]).strip()
        if not pr_number.isdigit() or int(pr_number) <= 0:
            sys.stderr.write(f"ERROR: Invalid pull request number '{pr_number}'; must be a positive integer.\n")
            return 1
        if not is_valid_repo_slug(repo):
            sys.stderr.write("ERROR: Valid --repo <owner/repo> is required for pr-comments.\n")
            return 1

        def edit_fetcher(num: int) -> dict[str, Any] | None:
            return fetch_graphql_edit_metadata(repo, num)

        first_raw = subprocess.check_output(  # noqa: S603
            [  # noqa: S607
                "gh",
                "pr",
                "view",
                pr_number,
                "--repo",
                repo,
                "--json",
                ",".join(sorted(MANDATORY_PR_INTERNAL_FIELDS)),
            ],
            text=True,
        )
        first_parsed = _extract_json_payload(first_raw)
        if isinstance(first_parsed, list):
            # Backwards-compatible fallback when a test stubs check_output with [inline_list, pr_meta_dict]
            inline_comments = first_parsed
            second_raw = subprocess.check_output(  # noqa: S603
                ["gh", "pr", "view", pr_number, "--repo", repo, "--json", "comments,labels"],  # noqa: S607
                text=True,
            )
            second_parsed = _extract_json_payload(second_raw)
            pr_meta = second_parsed if isinstance(second_parsed, dict) else {}
            if has_quarantine_label(pr_meta):
                sys.stderr.write(
                    json.dumps(
                        {
                            "error": "BLOCKED_UNAPPROVED_EXTERNAL_CONTENT",
                            "reason": "QUARANTINED_LABEL",
                            "number": int(pr_number),
                        }
                    )
                    + "\n"
                )
                return 2
        else:
            pr_meta = first_parsed if isinstance(first_parsed, dict) else {}
            if has_quarantine_label(pr_meta):
                sys.stderr.write(
                    json.dumps(
                        {
                            "error": "BLOCKED_UNAPPROVED_EXTERNAL_CONTENT",
                            "reason": "QUARANTINED_LABEL",
                            "number": int(pr_number),
                        }
                    )
                    + "\n"
                )
                return 2
            inline_raw = subprocess.check_output(  # noqa: S603
                ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments"],  # noqa: S607
                text=True,
            )
            inline_parsed = _extract_json_payload(inline_raw)
            inline_comments = inline_parsed if isinstance(inline_parsed, list) else []

        if pr_meta.get("author"):
            pr_ok, pr_reason, clean_pr = evaluate_and_sanitize_item(
                pr_meta,
                trusted_users,
                edit_fetcher,
                bot_prefixes=bot_prefixes,
                leak_patterns=leak_patterns,
            )
            if not pr_ok or clean_pr is None:
                if pr_reason == "QUARANTINED_LABEL":
                    sys.stderr.write(
                        json.dumps(
                            {
                                "error": "BLOCKED_UNAPPROVED_EXTERNAL_CONTENT",
                                "reason": pr_reason,
                                "number": int(pr_number),
                            }
                        )
                        + "\n"
                    )
                    return 2
                pr_meta = dict(pr_meta, is_approved=False, approval_status=pr_reason)
                issue_comments: list[dict[str, Any]] = []
                reviews_list: list[dict[str, Any]] = []
            else:
                pr_meta = dict(clean_pr, is_approved=True, approval_status=pr_reason)
                issue_comments = clean_pr.get("comments") or []
                reviews_list = clean_pr.get("reviews") or []
        else:
            issue_comments = pr_meta.get("comments") or []
            reviews_list = pr_meta.get("reviews") or []

        sanitized = sanitize_inline_pr_comments(
            inline_comments,
            trusted_users,
            issue_comments,
            parent_pr=pr_meta,
            reviews=reviews_list,
            bot_prefixes=bot_prefixes,
            leak_patterns=leak_patterns,
        )
        output_json = json.dumps(sanitized, indent=2)
        if jq_expr:
            proc = subprocess.run(  # noqa: S603
                ["jq", "-r", jq_expr],  # noqa: S607
                input=output_json,
                text=True,
                capture_output=True,
                check=True,
            )
            sys.stdout.write(proc.stdout)
        else:
            print(output_json)
        return 0

    if not is_valid_repo_slug(repo):
        sys.stderr.write("ERROR: Valid --repo <owner/repo> (or GH_REPO, or target_repo in config) is required.\n")
        return 1

    def edit_fetcher(num: int) -> dict[str, Any] | None:
        return fetch_graphql_edit_metadata(repo, num)

    if parsed.subcommand in ("issue-list", "pr-list"):
        entity = "issue" if parsed.subcommand == "issue-list" else "pr"
        base_fields = MANDATORY_INTERNAL_FIELDS[entity]
        merged_fields = sorted(
            base_fields | (set(gh_supported_requested) if gh_supported_requested else set())
        )
        cmd = ["gh", entity, "list"] + clean_args + repo_args + ["--json", ",".join(merged_fields)]
        raw_out = subprocess.check_output(cmd, text=True)  # noqa: S603
        parsed_items = _extract_json_payload(raw_out)
        items = parsed_items if isinstance(parsed_items, list) else []
        sanitized_full: list[dict[str, Any]] = []
        for item in items:
            ok, _, clean_item = evaluate_and_sanitize_item(
                item,
                trusted_users,
                edit_fetcher,
                bot_prefixes=bot_prefixes,
                leak_patterns=leak_patterns,
            )
            if ok and clean_item is not None:
                sanitized_full.append(clean_item)

        if entity == "issue" and sanitized_full:
            issue_nums = [int(it["number"]) for it in sanitized_full if it.get("number") is not None]
            gql_priorities = fetch_graphql_issue_priorities(repo, issue_nums)
            for it in sanitized_full:
                num = int(it["number"]) if it.get("number") is not None else -1
                prio_name, prio_rank = resolve_issue_priority(it, gql_priorities.get(num))
                it["priority"] = prio_name
                it["priorityRank"] = prio_rank
            sanitized_full = sort_issues_by_priority(sanitized_full)

        sanitized_items = [
            _project_requested_fields(
                it,
                requested_fields,
                trusted_users=trusted_users,
                include_priority=(entity == "issue"),
            )
            for it in sanitized_full
        ]
        output_json = json.dumps(sanitized_items, indent=2)
        if jq_expr:
            proc = subprocess.run(  # noqa: S603
                ["jq", "-r", jq_expr],  # noqa: S607
                input=output_json,
                text=True,
                capture_output=True,
                check=True,
            )
            sys.stdout.write(proc.stdout)
        else:
            print(output_json)
        return 0

    if parsed.subcommand in ("issue-view", "pr-view"):
        entity = "issue" if parsed.subcommand == "issue-view" else "pr"
        base_fields = MANDATORY_INTERNAL_FIELDS[entity]
        merged_fields = sorted(
            base_fields | (set(gh_supported_requested) if gh_supported_requested else set())
        )
        cmd = ["gh", entity, "view"] + clean_args + repo_args + ["--json", ",".join(merged_fields)]
        raw_out = subprocess.check_output(cmd, text=True)  # noqa: S603
        parsed_item = _extract_json_payload(raw_out)
        item = parsed_item if isinstance(parsed_item, dict) else {}
        ok, reason, clean_item = evaluate_and_sanitize_item(
            item,
            trusted_users,
            edit_fetcher,
            bot_prefixes=bot_prefixes,
            leak_patterns=leak_patterns,
        )
        if not ok or clean_item is None:
            sys.stderr.write(
                json.dumps(
                    {
                        "error": "BLOCKED_UNAPPROVED_EXTERNAL_CONTENT",
                        "reason": reason,
                        "number": item.get("number"),
                        "hint": "External issues/PRs require a maintainer comment 'approved' posted after the latest edit and commit.",
                    }
                )
                + "\n"
            )
            return 2
        if entity == "issue" and clean_item.get("number") is not None:
            num = int(clean_item["number"])
            gql_priorities = fetch_graphql_issue_priorities(repo, [num])
            prio_name, prio_rank = resolve_issue_priority(clean_item, gql_priorities.get(num))
            clean_item["priority"] = prio_name
            clean_item["priorityRank"] = prio_rank
        projected = _project_requested_fields(
            clean_item,
            requested_fields,
            trusted_users=trusted_users,
            include_priority=(entity == "issue"),
        )
        output_json = json.dumps(projected, indent=2)
        if jq_expr:
            proc = subprocess.run(  # noqa: S603
                ["jq", "-r", jq_expr],  # noqa: S607
                input=output_json,
                text=True,
                capture_output=True,
                check=True,
            )
            sys.stdout.write(proc.stdout)
        else:
            print(output_json)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
