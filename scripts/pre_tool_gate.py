#!/usr/bin/env python3
"""PreToolUse Gate & Security Boundary Enforcement for Bug Fixer Loop.

Enforces safety, boundary, and immutability invariants:
1. Specification File Immutability:
   - Modifying the configured spec file (e.g., docs/design_specification.md)
     via file editing tools or shell commands is strictly prohibited.
2. Protected Files Enforcement:
   - Files declared in `protected_files` cannot be modified by autonomous agents.
3. Host Security Boundary Protection:
   - Security scripts (pre_tool_gate.py, safe_ingest.py) cannot be modified.
4. Forbidden Command Blocking:
   - Blocks automated merge (`gh pr merge`, `gh stack merge`).
   - Blocks self-approval (`gh pr review --approve`).
   - Blocks issue reopening (`gh issue reopen`).
   - Blocks posting fake maintainer approval comments (`approved`, `/approve`).
   - Blocks raw `gh issue/pr list` and `gh issue/pr view` (requires safe_ingest.py).
   - Blocks shell mutations (`sed -i`, `>`, `tee`, `rm`, `cp`, `mv`) targeting protected files.
5. CLI Utility Modes:
   - `--scan-only`: Scans tracked files against configured leak patterns.
   - `--stamp-receipt` / `--verify-receipt`: Validates and records leak audit receipts in .git.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

# Gate Denial Reasons
REASON_SPEC_IMMUTABLE = (
    "Specification file is immutable: Direct modification of the specification "
    "file is strictly prohibited. Submit an issue or follow the change procedure."
)
REASON_PROTECTED_FILE = (
    "Protected file cannot be modified: Target file is in protected_files list."
)
REASON_HOST_SCRIPT = (
    "Host security script cannot be modified: Autonomous modification of "
    "host security scripts is prohibited."
)
REASON_AUTO_MERGE = (
    "Auto-merge is prohibited: Autonomous merging of PRs or stacks via "
    "`gh pr merge` or `gh stack merge` is forbidden."
)
REASON_SELF_APPROVAL = (
    "Self-approval is prohibited: PR self-approval via `gh pr review --approve` is forbidden."
)
REASON_ISSUE_REOPEN = (
    "Issue reopening is prohibited: Autonomous agents are prohibited from running `gh issue reopen`."
)
REASON_APPROVAL_INJECTION = (
    "Self-approval injection blocked: Posting maintainer approval comments "
    "(`approved`, `/approve`) is forbidden."
)
REASON_RAW_GH = (
    "Use safe_ingest.py instead of raw gh commands: Direct `gh issue/pr list` "
    "and `gh issue/pr view` commands are prohibited. Use `python3 scripts/safe_ingest.py` instead."
)
REASON_SHELL_MUTATION = (
    "Protected file shell mutation blocked: Shell mutation targeting spec or "
    "protected files is prohibited."
)
REASON_EGRESS_LEAK = (
    "Egress leak blocked: Outbound GitHub command body contains content "
    "matching configured leak patterns. Content not sent."
)

# Command Protection Regexes
AUTO_MERGE_REGEX = re.compile(r"(?:^|[;&|]\s*|\s)gh\s+(?:pr\s+merge|stack\s+merge)\b")
SELF_APPROVAL_REGEX = re.compile(r"(?:^|[;&|]\s*|\s)gh\s+pr\s+review\b[^\n;]*--approve\b")
ISSUE_REOPEN_REGEX = re.compile(r"(?:^|[;&|]\s*|\s)gh\s+issue\s+reopen\b")
APPROVAL_COMMENT_CMD_REGEX = re.compile(
    r"(?:^|[;&|]\s*|\s)gh\s+(?:issue\s+comment|pr\s+(?:comment|review)|api\b[^\n]*(?:comments|reviews))\b"
)
APPROVAL_COMMENT_BODY_REGEX = re.compile(
    r"""(?:--body|-b|-f\s+body=|--field\s+body=)\s*['"]?\s*/?approved?\s*[.!]*\s*['"]?(?:\s|$|[;&|])""",
    re.IGNORECASE,
)
APPROVAL_ECHO_PIPE_REGEX = re.compile(
    r"""echo\s+['"]\s*/?approved?\s*[.!]*\s*['"]\s*\|\s*gh\s+""",
    re.IGNORECASE,
)
RAW_GH_READ_REGEX = re.compile(r"(?:^|[;&|]\s*|\$\(\s*|\s)gh\s+(?:issue|pr)\s+(?:list|view)\b")
OUTBOUND_GH_CMD_REGEX = re.compile(
    r"(?:^|[;&|]\s*|\s)gh\s+(?:issue\s+comment|pr\s+(?:create|edit|comment)|api)\b"
)

HOST_SECURITY_SCRIPTS = frozenset({"pre_tool_gate.py", "safe_ingest.py"})


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Loads plugin config from explicit path, BUG_FIXER_CONFIG env var, or candidates."""
    target_path: Path | None = None
    if config_path:
        target_path = Path(config_path).expanduser().resolve()
        if not target_path.is_file():
            raise FileNotFoundError(f"Configuration file not found at: {target_path}")
    elif os.environ.get("BUG_FIXER_CONFIG"):
        env_path = Path(os.environ["BUG_FIXER_CONFIG"]).expanduser().resolve()
        if not env_path.is_file():
            raise FileNotFoundError(f"Configuration file not found at BUG_FIXER_CONFIG: {env_path}")
        target_path = env_path
    else:
        script_dir = Path(__file__).resolve().parent
        plugin_dir = script_dir.parent
        for cand in [
            plugin_dir.parent / "bug-fixer-loop-workspace" / "config.json",
            Path.cwd() / ".agents" / "plugins" / "bug-fixer-loop-workspace" / "config.json",
        ]:
            if cand.is_file():
                target_path = cand
                break

    if target_path is None or not target_path.is_file():
        return {
            "spec_file_path": "docs/design_specification.md",
            "protected_files": ["docs/design_specification.md"],
            "leak_patterns": [],
            "scan_exclude_paths": ["tests/"],
        }

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"Config at {target_path} must be a JSON object")
            return data
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {target_path}: {exc}") from exc


def is_path_match(target_path_str: str, rule_path_str: str, repo_dir: str | None = None) -> bool:
    """Checks whether target_path_str matches rule_path_str."""
    if not target_path_str or not rule_path_str:
        return False
    target_norm = os.path.normpath(target_path_str.strip())
    rule_norm = os.path.normpath(rule_path_str.strip())

    if target_norm == rule_norm or target_norm.endswith("/" + rule_norm) or rule_norm.endswith("/" + target_norm):
        return True

    if repo_dir:
        try:
            repo_path = Path(repo_dir).resolve()
            t_path = Path(target_path_str).expanduser()
            r_path = Path(rule_path_str).expanduser()
            t_res = (repo_path / t_path).resolve() if not t_path.is_absolute() else t_path.resolve()
            r_res = (repo_path / r_path).resolve() if not r_path.is_absolute() else r_path.resolve()
            if t_res == r_res:
                return True
        except Exception:
            pass
    return False


def is_host_script_target(target_path_str: str) -> bool:
    """Checks whether target is one of the protected host security scripts."""
    if not target_path_str:
        return False
    norm = os.path.normpath(target_path_str.strip())
    if os.path.basename(norm) in HOST_SECURITY_SCRIPTS:
        return True
    return any(norm.endswith("scripts/" + s) or norm.endswith("/" + s) for s in HOST_SECURITY_SCRIPTS)


def check_shell_mutation(cmd: str, spec_file_path: str, protected_files: list[str]) -> bool:
    """Detects shell commands attempting to mutate spec, protected files, or security scripts."""
    watched: set[str] = set(HOST_SECURITY_SCRIPTS)
    if spec_file_path:
        watched.add(spec_file_path)
        watched.add(Path(spec_file_path).name)
    for pf in protected_files:
        if pf:
            watched.add(pf)
            watched.add(Path(pf).name)

    escaped = [re.escape(w) for w in watched if w]
    if not escaped:
        return False
    targets_pattern = "|".join(escaped)
    mutation_regex = re.compile(
        rf"(?:(?:\b(?:sed\s+-i|tee|rm|cp|mv)\b|(?:>>|>)\s*)[^\n;|]*\b(?:{targets_pattern})\b)"
    )
    return bool(mutation_regex.search(cmd))


def evaluate_gate_a(
    tool_name: str,
    args: dict[str, Any],
    spec_file_path: str,
    protected_files: list[str],
    repo_dir: str | None = None,
) -> dict[str, str] | None:
    """Gate A: File Write Protection."""
    file_mutation_tools = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
    target_file = str(args.get("TargetFile") or args.get("target_file") or "").strip()
    if not target_file:
        return {"decision": "allow"} if tool_name in file_mutation_tools else None

    if is_host_script_target(target_file):
        return {"decision": "deny", "reason": REASON_HOST_SCRIPT}
    if spec_file_path and is_path_match(target_file, spec_file_path, repo_dir):
        return {"decision": "deny", "reason": REASON_SPEC_IMMUTABLE}
    for pf in protected_files:
        if pf and is_path_match(target_file, pf, repo_dir):
            return {"decision": "deny", "reason": REASON_PROTECTED_FILE}

    return {"decision": "allow"}


def _strip_quotes(s: str) -> str:
    """Strips matched outer single or double quotes."""
    s = s.strip()
    if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        return s[1:-1]
    return s


def extract_outbound_body(cmd: str) -> str | None:
    """Extracts outbound body content from gh commands."""
    if not cmd:
        return None

    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = None

    if tokens is not None:
        if "|" in tokens:
            pipe_idx = tokens.index("|")
            left = tokens[:pipe_idx]
            right = tokens[pipe_idx + 1:]
            if left and left[0] == "echo" and "gh" in right:
                idx = 1
                while idx < len(left) and left[idx].startswith("-") and left[idx] != "-":
                    idx += 1
                return " ".join(left[idx:])

        for i, tok in enumerate(tokens):
            if tok in ("--body", "-b") and i + 1 < len(tokens):
                return tokens[i + 1]
            if tok.startswith(("--body=", "-b=")):
                return tok.split("=", 1)[1]
            if tok in ("-f", "--field") and i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if nxt.startswith("body="):
                    return _strip_quotes(nxt[len("body="):])
            if tok.startswith("-fbody="):
                return _strip_quotes(tok[len("-fbody="):])
            if tok.startswith(("-f body=", "--field body=")):
                return _strip_quotes(tok.split("body=", 1)[1])
            if tok.startswith(("--field=body=", "-f=body=")):
                return _strip_quotes(tok.split("body=", 1)[1])
            if tok == "--body-file" and i + 1 < len(tokens):
                fpath = Path(tokens[i + 1]).expanduser()
                if not fpath.is_absolute():
                    fpath = Path.cwd() / fpath
                try:
                    if fpath.is_file():
                        return fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
                return None
            if tok.startswith("--body-file="):
                fpath = Path(tok.split("=", 1)[1]).expanduser()
                if not fpath.is_absolute():
                    fpath = Path.cwd() / fpath
                try:
                    if fpath.is_file():
                        return fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
                return None

    m_pipe = re.search(r"""(?:^|[;&|]\s*)echo\s+(?:-[a-zA-Z]+\s+)*(?:(["'])(.*?)(?:\1|$)|(.*?))\s*\|\s*gh\b""", cmd, re.DOTALL)
    if m_pipe:
        val = m_pipe.group(2) if m_pipe.group(2) is not None else m_pipe.group(3)
        return _strip_quotes(val)

    m_body = re.search(r"""(?:--body|-b)(?:\s+|=)(?:(["'])(.*?)(?:\1|$)|(\S+))""", cmd, re.DOTALL)
    if m_body:
        val = m_body.group(2) if m_body.group(2) is not None else m_body.group(3)
        return _strip_quotes(val)

    m_field = re.search(r"""(?:-f|--field)(?:\s+|=)?["']?body=(?:(["'])(.*?)(?:\1|$)|([^"'\s]+))""", cmd, re.DOTALL)
    if m_field:
        val = m_field.group(2) if m_field.group(2) is not None else m_field.group(3)
        return _strip_quotes(val)

    m_file = re.search(r"""--body-file(?:\s+|=)(?:(["'])(.*?)(?:\1|$)|(\S+))""", cmd)
    if m_file:
        val = m_file.group(2) if m_file.group(2) is not None else m_file.group(3)
        if val:
            fpath = Path(_strip_quotes(val)).expanduser()
            if not fpath.is_absolute():
                fpath = Path.cwd() / fpath
            try:
                if fpath.is_file():
                    return fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        return None

    return None


def check_egress_leaks(cmd: str, leak_patterns: list[str]) -> bool:
    """Scans outbound GitHub commands for leak patterns."""
    if not cmd or not leak_patterns:
        return False

    if not OUTBOUND_GH_CMD_REGEX.search(cmd):
        return False

    body = extract_outbound_body(cmd)
    if body is None:
        return False

    compiled: list[re.Pattern[str]] = []
    for pat in leak_patterns:
        if pat and isinstance(pat, str):
            try:
                compiled.append(re.compile(pat))
            except re.error as exc:
                sys.stderr.write(f"WARNING: Invalid leak pattern regex '{pat}': {exc}\n")

    if not compiled:
        return False

    for pat in compiled:
        if pat.search(body):
            return True

    return False


def evaluate_gate_b(
    tool_name: str,
    args: dict[str, Any],
    spec_file_path: str,
    protected_files: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Gate B: Command Execution Protection."""
    if tool_name and tool_name != "run_command":
        return None

    cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip()
    if not cmd:
        return {"decision": "allow"}

    if AUTO_MERGE_REGEX.search(cmd):
        return {"decision": "deny", "reason": REASON_AUTO_MERGE}
    if SELF_APPROVAL_REGEX.search(cmd):
        return {"decision": "deny", "reason": REASON_SELF_APPROVAL}
    if ISSUE_REOPEN_REGEX.search(cmd):
        return {"decision": "deny", "reason": REASON_ISSUE_REOPEN}
    if (
        (APPROVAL_COMMENT_CMD_REGEX.search(cmd) and APPROVAL_COMMENT_BODY_REGEX.search(cmd))
        or APPROVAL_ECHO_PIPE_REGEX.search(cmd)
    ):
        return {"decision": "deny", "reason": REASON_APPROVAL_INJECTION}
    if RAW_GH_READ_REGEX.search(cmd):
        return {"decision": "deny", "reason": REASON_RAW_GH}
    if check_shell_mutation(cmd, spec_file_path, protected_files):
        return {"decision": "deny", "reason": REASON_SHELL_MUTATION}

    leak_patterns = list(config.get("leak_patterns", [])) if config else []
    if check_egress_leaks(cmd, leak_patterns):
        return {"decision": "deny", "reason": REASON_EGRESS_LEAK}

    return {"decision": "allow"}


def evaluate_pretool_call(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """Evaluates PreToolUse payload against Gates A, B, and C."""
    tool_name = ""
    args: dict[str, Any] = {}

    if "toolCall" in payload and isinstance(payload["toolCall"], dict):
        tool_name = str(payload["toolCall"].get("name", "")).strip()
        args = payload["toolCall"].get("args", {}) or {}
    elif "tool_name" in payload:
        tool_name = str(payload.get("tool_name", "")).strip()
        args = payload.get("tool_input") or payload.get("args") or {}
    elif "name" in payload and ("args" in payload or "tool_input" in payload):
        tool_name = str(payload.get("name", "")).strip()
        args = payload.get("args") or payload.get("tool_input") or {}
    elif "tool_input" in payload:
        args = payload.get("tool_input", {}) or {}

    spec_file_path = str(config.get("spec_file_path", "")).strip()
    protected_files = list(config.get("protected_files", []))
    repo_dir = str(config.get("repo_dir", "")).strip() or None

    decision_a = evaluate_gate_a(tool_name, args, spec_file_path, protected_files, repo_dir)
    if decision_a is not None:
        return decision_a

    decision_b = evaluate_gate_b(tool_name, args, spec_file_path, protected_files, config)
    if decision_b is not None:
        return decision_b

    return {"decision": "allow"}


def get_git_dir(repo_dir: str) -> str:
    """Returns absolute path to the .git directory (handles worktrees)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"], cwd=repo_dir, stderr=subprocess.DEVNULL, text=True
        ).strip()
        return out if os.path.isabs(out) else os.path.normpath(os.path.join(repo_dir, out))
    except Exception:
        return os.path.join(repo_dir, ".git")


def get_tree_sha(repo_dir: str) -> str:
    """Returns current git write-tree SHA."""
    try:
        return subprocess.check_output(
            ["git", "write-tree"], cwd=repo_dir, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return ""


def scan_repository(repo_dir: str, patterns: list[str], exclude_paths: list[str] | None = None) -> tuple[list[str], int, int]:
    """Scans repository tracked and staged files for leak pattern violations."""
    violations: list[str] = []
    compiled: list[re.Pattern[str]] = []
    for pat in patterns:
        if pat and isinstance(pat, str):
            try:
                compiled.append(re.compile(pat))
            except re.error as exc:
                sys.stderr.write(f"WARNING: Invalid leak pattern regex '{pat}': {exc}\n")

    if not compiled:
        return [], 0, 0

    try:
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=repo_dir, stderr=subprocess.DEVNULL, text=True
        ).splitlines()
        staged = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"], cwd=repo_dir, stderr=subprocess.DEVNULL, text=True
        ).splitlines()
        files = sorted(set(tracked + staged))
    except Exception as exc:
        return [f"Failed to list git files in {repo_dir}: {exc}"], 0, 0

    scanned_count = 0
    excluded_count = 0
    for rel_path in files:
        if not rel_path or rel_path.startswith(".git/") or rel_path.startswith("_agents/"):
            continue
        
        if exclude_paths and any(rel_path.startswith(ep) for ep in exclude_paths):
            excluded_count += 1
            continue
        full_path = os.path.join(repo_dir, rel_path)
        if not os.path.isfile(full_path):
            continue
        scanned_count += 1
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    for pat in compiled:
                        match = pat.search(line)
                        if match:
                            matched_text = match.group()
                            redacted = matched_text[:4] + "…" if len(matched_text) > 4 else "****"
                            violations.append(
                                f"{rel_path}:{lineno} [{pat.pattern}] -> {redacted}"
                            )
        except Exception:
            continue

    return violations, scanned_count, excluded_count


def stamp_receipt(repo_dir: str, status: str = "PASS") -> int:
    """Stamps leak audit receipt into .git/leak_audit_receipt.json."""
    tree_sha = get_tree_sha(repo_dir)
    if not tree_sha:
        sys.stderr.write("ERROR: Could not compute git write-tree SHA.\n")
        return 1

    git_dir = get_git_dir(repo_dir)
    receipt_path = os.path.join(git_dir, "leak_audit_receipt.json")
    payload = {
        "status": status,
        "tree_sha": tree_sha,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        os.makedirs(git_dir, exist_ok=True)
        with open(receipt_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"STAMPED {receipt_path} (status={status}, tree_sha={tree_sha})")
        return 0
    except Exception as exc:
        sys.stderr.write(f"ERROR: Failed to write receipt {receipt_path}: {exc}\n")
        return 1


def verify_receipt(repo_dir: str) -> int:
    """Verifies that .git/leak_audit_receipt.json matches current staged tree."""
    git_dir = get_git_dir(repo_dir)
    candidates = [
        os.path.join(git_dir, "leak_audit_receipt.json"),
        os.path.join(repo_dir, ".git", "leak_audit_receipt.json"),
    ]
    receipt_file = next((c for c in candidates if os.path.isfile(c)), None)
    if not receipt_file:
        sys.stderr.write("Receipt verification failed: missing .git/leak_audit_receipt.json\n")
        return 1

    try:
        with open(receipt_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        status = data.get("status")
        if status != "PASS":
            sys.stderr.write(f"Receipt verification failed: status is '{status}', expected 'PASS'\n")
            return 1
        current_sha = get_tree_sha(repo_dir)
        receipt_sha = data.get("tree_sha")
        if not current_sha:
            sys.stderr.write("ERROR: Could not compute current git write-tree SHA.\n")
            return 1
        if receipt_sha != current_sha:
            sys.stderr.write(
                f"Receipt verification failed: receipt tree_sha '{receipt_sha}' != current tree_sha '{current_sha}'\n"
            )
            return 1
        print(f"Receipt verified (status=PASS, tree_sha={current_sha})")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Receipt verification failed: {exc}\n")
        return 1


def run_git_hook(repo_dir: str, patterns: list[str], exclude_paths: list[str] | None = None) -> int:
    """Executes pre-commit / pre-push git hook validation."""
    violations, _, _ = scan_repository(repo_dir, patterns, exclude_paths)
    if violations:
        sys.stderr.write(
            f"\n[Git Hook Blocked] Leak pattern violation(s) detected ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations[:20])
            + "\n"
        )
        return 1
    return verify_receipt(repo_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PreToolUse Gate & Security Boundary Enforcement for Bug Fixer Loop."
    )
    parser.add_argument("--config", type=str, help="Path to config.json")
    parser.add_argument("--repo", type=str, help="Path to target git repository directory")
    parser.add_argument("--scan-only", nargs="?", const=".", type=str, help="Scan repository for leak patterns")
    parser.add_argument("--stamp-receipt", action="store_true", help="Stamp leak audit pass receipt into .git")
    parser.add_argument("--stamp-pass", action="store_true", help="Alias for --stamp-receipt")
    parser.add_argument("--verify-receipt", action="store_true", help="Verify leak audit receipt matches staged tree")
    parser.add_argument("--git-hook", action="store_true", help="Run as git pre-commit/pre-push hook")
    parser.add_argument("--exclude-pattern", action="append", default=[], help="Additional paths to exclude from leak scan")

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as exc:
        sys.stderr.write(f"Config loading error: {exc}\n")
        config = {
            "spec_file_path": "docs/design_specification.md",
            "protected_files": ["docs/design_specification.md"],
            "leak_patterns": [],
        }

    # Derive repo_dir: explicit arg > config field > config file location > cwd
    repo_dir = args.repo or config.get("repo_dir", "")
    if not repo_dir:
        # Try deriving from config file location (standard layout)
        try:
            cfg_path = Path(args.config or os.environ.get("BUG_FIXER_CONFIG", "")).resolve()
            if cfg_path.is_file() and "bug-fixer-loop-workspace" in str(cfg_path):
                repo_dir = str(cfg_path.parent.parent.parent.parent)
        except Exception:
            pass
    if not repo_dir:
        repo_dir = os.getcwd()
    if not os.path.isabs(repo_dir):
        repo_dir = os.path.abspath(repo_dir)

    if args.scan_only is not None:
        target_dir = args.scan_only if args.scan_only != "." else repo_dir
        if not os.path.isabs(target_dir):
            target_dir = os.path.abspath(target_dir)
        patterns = list(config.get("leak_patterns", []))
        exclude_paths = list(config.get("scan_exclude_paths", ["tests/"]))
        if args.exclude_pattern:
            exclude_paths.extend(args.exclude_pattern)
        violations, scanned_count, excluded_count = scan_repository(target_dir, patterns, exclude_paths)
        if violations:
            print(f"LEAK SCAN FAILED ({len(violations)} violation(s)):")
            for v in violations:
                print(f"  - {v}")
            return 1
        print(f"LEAK SCAN CLEAN (repo={target_dir}, {scanned_count} files scanned, {excluded_count} excluded by scan_exclude_paths)")
        return 0

    if args.stamp_receipt or args.stamp_pass:
        return stamp_receipt(repo_dir, "PASS")

    if args.verify_receipt:
        return verify_receipt(repo_dir)

    if args.git_hook:
        patterns = list(config.get("leak_patterns", []))
        exclude_paths = list(config.get("scan_exclude_paths", ["tests/"]))
        if args.exclude_pattern:
            exclude_paths.extend(args.exclude_pattern)
        return run_git_hook(repo_dir, patterns, exclude_paths)

    if sys.stdin.isatty():
        parser.print_help()
        return 0

    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow"}))
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"decision": "deny", "reason": f"Invalid JSON payload: {exc}"}))
        return 0

    result = evaluate_pretool_call(payload, config)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
