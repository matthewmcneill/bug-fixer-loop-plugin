#!/usr/bin/env python3
"""PreToolUse Gate & Security Boundary Enforcement for Bug Fixer Loop.

Enforces safety, boundary, and immutability invariants:
1. Specification File Immutability:
   - Modifying the configured spec file (e.g., docs/plugin-architecture.md)
     via file editing tools or shell commands is strictly prohibited.
2. Protected Files Enforcement:
   - Files declared in `protected_files` cannot be modified by autonomous agents.
3. Host Security Boundary Protection:
   - Security scripts (HOST_SECURITY_SCRIPTS) cannot be modified.
4. Forbidden Command Blocking:
   - Blocks automated merge (`gh pr merge`, `gh stack merge`).
   - Blocks self-approval (`gh pr review --approve`).
   - Blocks issue reopening (`gh issue reopen`).
   - Blocks posting fake maintainer approval comments (`approved`, `/approve`).
   - Blocks raw GitHub reads (`gh issue/pr list|view|status`, `gh search`, `gh api` GETs of
     issues/pulls/comments, GraphQL reads) — reads must go through safe_ingest.py.
   - Blocks shell commands that modify (or reference in inline code) protected files.
   - Scans outbound GitHub content (bodies, titles, fields, files, stdin) for leaks and
     refuses content it cannot inspect.
   Commands are parsed structurally (shell_analysis.py) and judged per simple command, so
   wrappers such as `bash -c`, `eval`, `$(...)`, `env -S`, `xargs` and `find -exec` are
   analyzed rather than pattern-matched.
5. CLI Utility Modes:
   - `--scan-only`: Scans tracked files against configured leak patterns.
   - `--stamp-receipt` / `--verify-receipt`: Validates and records leak audit receipts in .git.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Protocol, runtime_checkable
import unicodedata

from bugfixer_api import BugFixer
from shell_analysis import DYN, Segment, ShellParseError, Word, expand_braces
from shell_analysis import parse as parse_shell

try:
    from security_audit import log_security_event
except ImportError:
    def log_security_event(*args, **kwargs): pass

# Gate Denial Reasons
REASON_SPEC_IMMUTABLE = (
    "Specification file is immutable: Direct modification of the specification "
    "file is strictly prohibited. Submit an issue or follow the change procedure."
)
REASON_PROTECTED_FILE = "Protected file cannot be modified: Target file is in protected_files list."
REASON_HOST_SCRIPT = (
    "Host security script cannot be modified: Autonomous modification of "
    "host security scripts is prohibited."
)
REASON_AUTO_MERGE = (
    "Auto-merge is prohibited: Autonomous merging of PRs or stacks via "
    "`gh pr merge` or `gh stack merge` is forbidden. "
    "Merging requires maintainer action."
)
REASON_SELF_APPROVAL = (
    "Self-approval is prohibited: PR self-approval via `gh pr review --approve` is forbidden. "
    "To post a non-approval review, use: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_post.py pr-review <number> --body \"...\"`"
)
REASON_ISSUE_REOPEN = (
    "Issue reopening is prohibited: Autonomous agents are prohibited from running "
    "`gh issue reopen`. Reopening requires maintainer action."
)
REASON_APPROVAL_INJECTION = (
    "Self-approval injection blocked: Posting maintainer approval comments "
    "(`approved`, `/approve`) is forbidden."
)
REASON_RAW_GH = (
    "Use safe_ingest.py instead of raw gh commands: Direct `gh issue/pr list` "
    "and `gh issue/pr view` commands are prohibited (as well as `gh pr diff` and `gh run view`). Use "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_ingest.py` "
    "(including `pr-diff`) instead, run from the repository root."
)
REASON_ARCHITECT_SPOOF = (
    "Architect review spoofing blocked: Posting [ARCHITECT] comments is restricted to architect_review sessions."
)
REASON_SHELL_MUTATION = (
    "Protected file shell mutation blocked: Shell mutation targeting spec or "
    "protected files is prohibited."
)
REASON_EGRESS_LEAK = (
    "Egress leak blocked: Outbound GitHub command body contains content "
    "matching configured leak patterns. Content not sent. "
    "Route all GitHub writes through: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_post.py` which scans content before posting."
)
REASON_UNSCANNABLE = (
    "Egress blocked: outbound content comes from a source the gate cannot inspect "
    "(stdin, a shell variable, a command substitution or a glob). "
    "Route GitHub writes through: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_post.py` which accepts body text as a CLI argument."
)
REASON_UNSCANNABLE_STDIN = REASON_UNSCANNABLE
REASON_DYNAMIC_EXEC = (
    "Dynamic command execution blocked: the gate cannot inspect commands assembled at "
    'runtime (bash -c "$VAR", eval, piping into a shell or interpreter, xargs into gh, '
    "gh aliases/extensions, git exec-config). Run the command directly. "
    "For GitHub reads use safe_ingest.py; for GitHub writes use: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_post.py`"
)
REASON_DYNAMIC_WRITE = (
    "Write target must be a literal path: the gate cannot verify that a runtime-built "
    "redirection target is not a protected file."
)
REASON_INLINE_GH = (
    "Inline code must not invoke gh: run gh commands directly so the gate can inspect them. "
    "For GitHub reads use safe_ingest.py; for GitHub writes use: "
    "`python3 ~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer/scripts/safe_post.py`"
)
# REASON_PROXY removed — safe_post.py is now allowed for all agents.
REASON_SECRET_DISCLOSURE = (
    "Credential disclosure blocked: printing the GitHub token "
    "(gh auth token / --show-token) is prohibited."
)
REASON_RAW_NETWORK = (
    "Raw network redirection (/dev/tcp, /dev/udp) is blocked: its payload cannot be inspected."
)
REASON_PUSH_DEFAULT_BRANCH = (
    "Pushing to the default branch is prohibited: changes reach main only through a "
    "maintainer-merged pull request."
)
REASON_ACCOUNT_CHANGE = (
    "Repository/account configuration changes (visibility, secrets, keys, credentials) "
    "are reserved for the maintainer."
)
REASON_PARSE = (
    "Command could not be parsed safely (unbalanced quotes, substitution or heredoc). "
    "Fix the quoting and retry."
)
REASON_LABEL_ESCALATION = (
    "Protected label blocked: adding trust-escalating labels (e.g. "
    "bug-fixer:architect:approved) is restricted to dispatcher sidecars. "
    "Signal approval via a structured [ARCHITECT] comment; the dispatcher "
    "applies the label on its next tick."
)
REASON_SAFE_INGEST_OVERRIDE = (
    "Overriding safe_ingest.py trust root (--trusted-user or --config) "
    "or trust environment variables is prohibited."
)


# Leak patterns that are always enforced on egress, in addition to config.
# Config can add patterns but never remove these.
BUILTIN_LEAK_PATTERNS = (
    r"ya29\.[A-Za-z0-9_-]+",
    r"AIza[0-9A-Za-z_-]{35}",
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    r"xox[abpors]-[0-9A-Za-z-]{10,}",
    r"https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+",
)

# Labels that only trusted dispatcher code (outside agent conversations) may
# add.  Agents signal these transitions indirectly (e.g. via structured
# comments); the sidecar dispatcher applies the label on its next tick.
PROTECTED_ADD_LABELS = frozenset({
    "bug-fixer:architect:approved",
})

HOST_SECURITY_SCRIPTS = frozenset({
    "pre_tool_gate.py", "safe_ingest.py", "safe_post.py",
    "injection_scanner.py", "shell_analysis.py", "github_url_gate.py",
    "integrity_monitor.py",
})
# Scripts in the daemon scripts/ dir that agents may execute (but not write).
DAEMON_TRUSTED_SCRIPTS = frozenset({
    "manage.py", "tick.py",
})
# Protected workspace paths are derived at runtime from the config that
# was actually loaded (which lives in the TARGET repo's workspace, not
# necessarily the plugin repo). Static scanner files are always protected.
_STATIC_PROTECTED = frozenset(
    {
        ".betterleaks.toml",
        ".betterleaksignore",
    }
)

def _find_daemon_dir(script_dir: Path) -> Path | None:
    """Find the daemon plugin root by matching name == 'bug-fixer-daemon'.

    Must check the manifest name, not just existence, because the workspace
    plugin also has a plugin.json (name: 'bug-fixer').
    """
    for ancestor in [script_dir.parent, script_dir.parent.parent,
                     script_dir.parent.parent.parent]:
        manifest = ancestor / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text())
                if data.get("name") == "bug-fixer-daemon":
                    return ancestor
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _derive_workspace_paths(loaded_config_path: Path | None = None) -> frozenset[str]:
    """Build the full SCANNER_PROTECTED_FILES set.

    Derives workspace paths from the config that was loaded, so they
    always protect the target repo's config regardless of install layout.
    Falls back to cwd-based derivation.
    """
    protected = set(_STATIC_PROTECTED)

    # Protect the loaded config's directory
    if loaded_config_path:
        ws_dir = loaded_config_path.resolve().parent
        protected.add(str(ws_dir / "config.json"))
        protected.add(str(ws_dir / "injection_cache.json"))
        protected.add(str(ws_dir / ".integrity_snapshot.json"))
        protected.add(str(ws_dir / "sidecar.json"))

    # Legacy relative paths
    protected.add(".agents/plugins/bug-fixer-loop-workspace/config.json")
    protected.add(".agents/plugins/bug-fixer-loop-workspace/injection_cache.json")

    # Protect .agents/plugins.json (both absolute and relative)
    protected.add(".agents/plugins.json")
    if loaded_config_path:
        repo_dir_str = None
        try:
            data = json.loads(loaded_config_path.read_text())
            repo_dir_str = data.get("repo_dir")
        except (json.JSONDecodeError, OSError):
            pass
        if repo_dir_str:
            protected.add(str(Path(repo_dir_str) / ".agents" / "plugins.json"))

    # Prefix-protect the entire daemon install directory
    daemon_dir = _find_daemon_dir(Path(__file__).resolve().parent)
    if daemon_dir:
        protected.add(f"PREFIX:{daemon_dir}/")  # trailing slash prevents matching bug-fixer-daemon2/

    return frozenset(protected)


# Initial value — updated after config is loaded in evaluate_pretool_call
SCANNER_PROTECTED_FILES = _derive_workspace_paths()


def normalise_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalises config dictionary so callers can always access scanning namespace.

    Supports both v1 (flat leak_patterns/scan_exclude_paths) and v2 (scanning namespace) schemas.
    """
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
                "backend": "regex",
                "patterns": list(res.get("leak_patterns", [])),
                "exclude_paths": list(res.get("scan_exclude_paths", [])),
            }
        }

    # Ensure backwards compatibility for legacy callers expecting top-level fields
    secrets = res.get("scanning", {}).get("secrets", {})
    if isinstance(secrets, dict):
        if "leak_patterns" not in res and "patterns" in secrets:
            res["leak_patterns"] = secrets["patterns"]
        if "scan_exclude_paths" not in res and "exclude_paths" in secrets:
            res["scan_exclude_paths"] = secrets["exclude_paths"]

    return res


def _sidecar_matches_workspaces(sidecar_sub: Path, workspace_paths: list[str] | None) -> bool:
    """Return True if a sidecar directory with a malformed config.json belongs to any path in workspace_paths."""
    if not workspace_paths:
        return False
    cand_conf = sidecar_sub / "config.json"
    if cand_conf.is_file():
        try:
            raw_text = cand_conf.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'"repo_dir"\s*:\s*"([^"]+)"', raw_text)
            if m:
                r_path = Path(m.group(1)).resolve()
                return any(Path(wp).resolve().is_relative_to(r_path) for wp in workspace_paths)
        except OSError:
            pass
    sc_manifest = sidecar_sub / "sidecar.json"
    if sc_manifest.is_file():
        try:
            sc_data = json.loads(sc_manifest.read_text(encoding="utf-8"))
            uris = sc_data.get("agent_permissions", {}).get("workspace_uris", [])
            if isinstance(uris, list) and uris:
                for uri in uris:
                    if isinstance(uri, str) and uri:
                        uri_path = Path(uri.removeprefix("file://")).resolve()
                        for wp in workspace_paths:
                            if Path(wp).resolve().is_relative_to(uri_path):
                                return True
                return False
        except (json.JSONDecodeError, OSError):
            pass
    repo_slug = re.split(r"--|__", sidecar_sub.name, maxsplit=1)[-1].lower()
    if repo_slug:
        for wp in workspace_paths:
            wp_resolved = Path(wp).resolve()
            if wp_resolved.name.lower() == repo_slug or any(
                p.name.lower() == repo_slug for p in wp_resolved.parents
            ):
                return True
        return False
    return True


def load_config(
    config_path: str | Path | None = None,
    workspace_paths: list[str] | None = None,
) -> dict[str, Any]:
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
        daemon_dir = _find_daemon_dir(script_dir)
        
        cands = []
        if daemon_dir and workspace_paths:
            sidecars_dir = daemon_dir / "sidecars"
            if sidecars_dir.is_dir():
                for sidecar_sub in sorted(sidecars_dir.iterdir()):
                    if not sidecar_sub.is_dir():
                        continue
                    cand_conf = sidecar_sub / "config.json"
                    if cand_conf.is_file():
                        try:
                            c_data = json.loads(cand_conf.read_text())
                            r_dir = c_data.get("repo_dir")
                            if r_dir:
                                r_path = Path(r_dir).resolve()
                                for wp in workspace_paths:
                                    if Path(wp).resolve().is_relative_to(r_path):
                                        cands.append(cand_conf)
                                        break
                        except json.JSONDecodeError as parse_err:
                            # Malformed config for a matching workspace → fail closed
                            if _sidecar_matches_workspaces(sidecar_sub, workspace_paths):
                                raise ValueError(
                                    f"Malformed JSON in sidecar config {cand_conf}: "
                                    f"{parse_err}. Fix or delete this file to restore "
                                    "normal gate behaviour."
                                )
                            sys.stderr.write(
                                f"WARNING: Ignoring malformed sidecar config for non-matching workspace {cand_conf}: {parse_err}\n"
                            )
                        except OSError:
                            pass
        
        # Legacy fallback
        for wp in (workspace_paths or []):
            legacy = Path(wp) / ".agents" / "plugins" / "bug-fixer-loop-workspace" / "config.json"
            cands.append(legacy)
            
        cands.append(Path.cwd() / ".agents" / "plugins" / "bug-fixer-loop-workspace" / "config.json")
        
        for cand in cands:
            if cand.is_file():
                target_path = cand
                break

    if target_path is None or not target_path.is_file():
        return normalise_config(
            {
                "_enrolled": False,
                "spec_file_path": "",
                "protected_files": [],
                "leak_patterns": [],
                "scan_exclude_paths": ["tests/"],
            }
        )

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"Config at {target_path} must be a JSON object")
            data["_loaded_from"] = str(target_path)
            return normalise_config(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in config file {target_path}: {exc}") from exc


def is_path_match(target_path_str: str, rule_path_str: str, repo_dir: str | None = None) -> bool:
    """Checks whether target_path_str matches rule_path_str."""
    if not target_path_str or not rule_path_str:
        return False
    target_norm = os.path.normpath(target_path_str.strip())
    rule_norm = os.path.normpath(rule_path_str.strip())

    if (
        target_norm == rule_norm
        or target_norm.endswith("/" + rule_norm)
        or rule_norm.endswith("/" + target_norm)
    ):
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
    return any(
        norm.endswith("scripts/" + s) or norm.endswith("/" + s) for s in HOST_SECURITY_SCRIPTS
    )


def evaluate_gate_a(
    tool_name: str,
    args: dict[str, Any],
    spec_file_path: str,
    protected_files: list[str],
    repo_dir: str | None = None,
    workspace_paths: list[str] | None = None,
) -> dict[str, str] | None:
    """Gate A: File Write Protection."""
    file_mutation_tools = {"write_to_file", "replace_file_content", "multi_replace_file_content"}
    target_file = str(args.get("TargetFile") or args.get("target_file") or "").strip()
    if not target_file:
        return {"decision": "allow"} if tool_name in file_mutation_tools else None

    if not os.path.isabs(target_file) and workspace_paths and workspace_paths[0]:
        target_file = os.path.join(workspace_paths[0], target_file)

    target_norm = os.path.normpath(target_file.strip())
    if os.path.basename(target_norm).startswith(".integrity_snapshot") or any(
        part.startswith(".integrity_snapshot") for part in target_norm.split("/")
    ):
        return {"decision": "deny", "reason": REASON_PROTECTED_FILE}

    if is_host_script_target(target_file):
        return {"decision": "deny", "reason": REASON_HOST_SCRIPT}
    if spec_file_path and is_path_match(target_file, spec_file_path, repo_dir):
        return {"decision": "deny", "reason": REASON_SPEC_IMMUTABLE}
    all_protected = list(protected_files) + sorted(SCANNER_PROTECTED_FILES)
    for pf in all_protected:
        if pf.startswith("PREFIX:"):
            prefix = pf[7:]
            target_res = os.path.normpath(os.path.realpath(os.path.expanduser(target_file)))
            if target_res.startswith(prefix):
                rel = target_res[len(prefix):]
                parts = rel.split("/")
                if len(parts) >= 3 and parts[0] == "sidecars" and parts[2] == "state":
                    basename = os.path.basename(target_res)
                    if (
                        basename == "injection_cache.json"
                        or basename.startswith(".integrity_snapshot")
                        or any(p.startswith(".integrity_snapshot") for p in parts)
                    ):
                        return {"decision": "deny", "reason": REASON_PROTECTED_FILE}
                    continue
                return {"decision": "deny", "reason": REASON_PROTECTED_FILE}
        elif pf and is_path_match(target_file, pf, repo_dir):
            return {"decision": "deny", "reason": REASON_PROTECTED_FILE}

    return {"decision": "allow"}


# ---------------------------------------------------------------------------
# Gate B: command analysis
#
# Commands are parsed into simple-command segments (see shell_analysis.py) and
# each segment is judged by what it executes, not by how the text looks.
# Wrappers (`bash -c`, `eval`, `env -S`, `xargs`, `find -exec`, substitutions)
# are analyzed recursively; content the gate cannot see at hook time
# (variables, substitutions, stdin from unknown producers) is refused
# wherever it would reach GitHub or a protected file.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent
_MAX_RECURSION = 8
_MAX_SCANNED_FILE_BYTES = 5 * 1024 * 1024
_STDIN_PATHS = re.compile(r"^/(dev/(stdin|fd/\d+|tty)|proc/[^/]+/fd/\d+)$")

# Generic basenames are only treated as protected together with a protected
# or unknown directory (writing src/app/config.json is legitimate).
_GENERIC_PROTECTED_BASENAMES = frozenset({"config.json"})

# Note: PreToolUse and safe_post.py only intercept agent tool calls
# (run_command / safe_post.py), never human comments posted through the GitHub
# web UI or a maintainer's normal terminal outside an enrolled agent session.
APPROVAL_TEXT_REGEX = re.compile(
    r"^\s*/?(?:approved?|approve-body|lgtm)\s*[.!]*\s*$", re.IGNORECASE
)


def _looks_like_approval(text: str) -> bool:
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return bool(APPROVAL_TEXT_REGEX.match(folded))


_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish", "busybox"})
_INTERPRETER_CODE_FLAGS: dict[str, tuple[str, ...]] = {
    "python": ("-c",),
    "node": ("-e", "--eval", "-p", "--print"),
    "nodejs": ("-e", "--eval", "-p", "--print"),
    "deno": ("eval",),
    "bun": ("-e", "--eval", "-p", "--print"),
    "perl": ("-e", "-E"),
    "ruby": ("-e",),
    "php": ("-r",),
    "lua": ("-e",),
    "osascript": ("-e",),
    "Rscript": ("-e",),
    "pwsh": ("-c", "-Command"),
    "awk": (),
    "gawk": (),
    "mawk": (),
    "nawk": (),
}
_AWKS = frozenset({"awk", "gawk", "mawk", "nawk"})
_READ_ONLY_PROGRAMS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "wc",
        "diff",
        "cmp",
        "comm",
        "ls",
        "stat",
        "file",
        "du",
        "df",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "shasum",
        "md5",
        "md5sum",
        "cksum",
        "jq",
        "tree",
        "echo",
        "printf",
        "true",
        "false",
        "test",
        "[",
        "pwd",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "which",
        "type",
        "nl",
        "cut",
        "tr",
        "column",
        "strings",
        "hexdump",
        "od",
        "sleep",
        "date",
        "whoami",
        "id",
        "uname",
        "hostname",
        "ps",
        "pgrep",
        "bat",
        "find",
        "sort",
        "mkdir",
    }
)
_DELETERS = frozenset({"rm", "rmdir", "unlink", "trash", "shred"})
_COPIERS = frozenset({"cp", "mv", "install", "rsync", "ditto"})
_SED_WRITES_REGEX = re.compile(
    r"(^|[;\n{}])\s*[0-9,$/!]*\s*[wWe](\s|$)|s(.).*\2.*\2[gpiIm0-9]*[we]"
)
_FIND_WRITE_FLAGS = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_EGRESS_PROGRAMS = frozenset(
    {
        "curl",
        "wget",
        "http",
        "https",
        "xh",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "telnet",
        "ssh",
        "scp",
        "sftp",
        "rsync",
        "ftp",
    }
)
_GIT_PATH_WRITERS = frozenset(
    {
        "checkout",
        "restore",
        "rm",
        "mv",
        "apply",
        "am",
        "stash",
        "reset",
        "clean",
        "update-index",
        "checkout-index",
        "read-tree",
        "switch",
        "worktree",
        "filter-branch",
        "filter-repo",
    }
)
_DEFAULT_BRANCHES = frozenset({"main", "master"})
_GIT_KNOWN_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "annotate",
        "apply",
        "archive",
        "bisect",
        "blame",
        "branch",
        "bundle",
        "cat-file",
        "check-attr",
        "check-ignore",
        "check-ref-format",
        "checkout",
        "checkout-index",
        "cherry",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "commit-tree",
        "config",
        "count-objects",
        "describe",
        "diff",
        "diff-files",
        "diff-index",
        "diff-tree",
        "difftool",
        "fetch",
        "filter-branch",
        "format-patch",
        "fsck",
        "gc",
        "grep",
        "hash-object",
        "help",
        "init",
        "interpret-trailers",
        "lfs",
        "log",
        "ls-files",
        "ls-remote",
        "ls-tree",
        "maintenance",
        "merge",
        "merge-base",
        "merge-file",
        "mergetool",
        "mktag",
        "mktree",
        "mv",
        "name-rev",
        "notes",
        "pull",
        "push",
        "range-diff",
        "read-tree",
        "rebase",
        "reflog",
        "remote",
        "repack",
        "replace",
        "request-pull",
        "reset",
        "restore",
        "rev-list",
        "rev-parse",
        "revert",
        "rm",
        "send-email",
        "shortlog",
        "show",
        "show-branch",
        "show-ref",
        "sparse-checkout",
        "stash",
        "status",
        "submodule",
        "switch",
        "symbolic-ref",
        "tag",
        "update-index",
        "update-ref",
        "var",
        "verify-commit",
        "verify-tag",
        "version",
        "whatchanged",
        "worktree",
        "write-tree",
    }
)
_GIT_EGRESS = frozenset({"commit", "tag", "notes", "push", "send-email", "request-pull"})
_GIT_EXEC_CONFIG = re.compile(
    r"^(alias\.|core\.(pager|editor|sshcommand|hookspath|fsmonitor|askpass)|"
    r".*\.(pager|editor|command|cmd|helper|program|textconv|driver|clean|smudge|process))",
    re.IGNORECASE,
)
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-P"}),
    "sudo": frozenset(
        {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group"}
    ),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-p", "-t"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "caffeinate": frozenset({"-w", "-t"}),
    "time": frozenset({"-f", "-o", "--format", "--output"}),
    "exec": frozenset({"-a"}),
    "command": frozenset(),
    "builtin": frozenset(),
    "nohup": frozenset(),
    "setsid": frozenset(),
    "chronic": frozenset(),
    "unbuffer": frozenset(),
}

_SPAWN_CODE_REGEX = re.compile(
    r"subprocess|os\.(?:system|popen|exec\w*|spawn\w*|posix_spawn)|pty\.spawn|commands\.getoutput"
    r"|child_process|\bexec(?:Sync|File|FileSync)?\s*\(|\bspawn(?:Sync)?\s*\(|\bsystem\s*\("
    r"|\bpopen\s*\(|IO\.popen|Open3|Kernel\.(?:system|exec|spawn)|%x|\bqx\b|`"
    r"|Runtime\.getRuntime|ProcessBuilder|Deno\.(?:run|Command)|Bun\.spawn|do shell script"
)
_GH_WORD_REGEX = re.compile(r"(?:^|[^\w.\-])gh(?![\w.\-])")
_QUOTED_GH_REGEX = re.compile(
    r"""['"](?:[^'"\s]*/)?gh(?:['"]|\s+(?:pr|issue|api|repo|release|gist|label|alias|auth|extension|search|config)\b)"""
)

# gh flags that take a value. Unknown flags are treated as booleans: if one
# actually takes a value, that value is seen as a positional (and, for
# commands whose positionals are files, read and scanned) — the safe side.
_GH_VALUE_LONG = frozenset(
    {
        "--body",
        "--body-file",
        "--title",
        "--notes",
        "--notes-file",
        "--comment",
        "--repo",
        "--base",
        "--head",
        "--label",
        "--add-label",
        "--remove-label",
        "--milestone",
        "--assignee",
        "--add-assignee",
        "--remove-assignee",
        "--reviewer",
        "--add-reviewer",
        "--remove-reviewer",
        "--project",
        "--add-project",
        "--remove-project",
        "--template",
        "--jq",
        "--method",
        "--header",
        "--field",
        "--raw-field",
        "--input",
        "--hostname",
        "--cache",
        "--preview",
        "--filename",
        "--desc",
        "--description",
        "--target",
        "--discussion-category",
        "--color",
        "--reason",
        "--state",
        "--limit",
        "--search",
        "--json",
        "--branch",
        "--name",
        "--tag",
        "--notes-start-tag",
        "--ref",
        "--message",
        "--subject",
    }
)
_GH_VALUE_SHORT = frozenset(
    {
        "-R",
        "-b",
        "-t",
        "-F",
        "-f",
        "-l",
        "-a",
        "-m",
        "-B",
        "-H",
        "-r",
        "-p",
        "-T",
        "-q",
        "-X",
        "-n",
        "-c",
        "-d",
        "-L",
        "-S",
        "-s",
    }
)
_GH_BOOLEAN_OVERRIDES: dict[tuple[str, str], frozenset[str]] = {
    ("pr", "review"): frozenset({"-a", "-r", "-c", "--comment"}),
    ("pr", "create"): frozenset({"-d", "-f", "-w", "-e"}),
    ("pr", "close"): frozenset({"-d"}),
    ("pr", "merge"): frozenset({"-s", "-r", "-m", "-d"}),
    ("issue", "create"): frozenset({"-w", "-e"}),
    ("issue", "comment"): frozenset({"-w", "-e"}),
    ("pr", "comment"): frozenset({"-w", "-e"}),
    ("gist", "create"): frozenset({"-p", "-w"}),
    ("release", "create"): frozenset({"-d", "-p"}),
    ("label", "create"): frozenset({"-f"}),
    ("api", ""): frozenset({"-i"}),
}
_GH_KNOWN_GROUPS = frozenset(
    {
        "issue",
        "pr",
        "api",
        "repo",
        "release",
        "gist",
        "label",
        "search",
        "run",
        "workflow",
        "auth",
        "status",
        "browse",
        "codespace",
        "secret",
        "variable",
        "ssh-key",
        "gpg-key",
        "project",
        "ruleset",
        "cache",
        "attestation",
        "completion",
        "help",
        "org",
        "stack",
        "extension",
        "ext",
        "alias",
        "config",
        "--version",
        "--help",
        "version",
    }
)
_GH_ACCOUNT_CHANGES = frozenset(
    {
        ("auth", "login"),
        ("auth", "logout"),
        ("auth", "refresh"),
        ("auth", "setup-git"),
        ("auth", "switch"),
        ("repo", "delete"),
        ("repo", "archive"),
        ("repo", "rename"),
        ("repo", "set-default"),
        ("ruleset", "create"),
        ("ruleset", "delete"),
    }
)
_GH_BODY_FLAGS = frozenset({"--body", "-b", "--comment", "-c"})
_GH_TEXT_FLAGS = frozenset(
    {"--title", "-t", "--notes", "-n", "--description", "-d", "--desc", "--message", "--subject"}
)
_GH_COMMENTING = frozenset(
    {
        ("issue", "comment"),
        ("pr", "comment"),
        ("pr", "review"),
        ("issue", "close"),
        ("pr", "close"),
        ("issue", "create"),
        ("pr", "create"),
        ("issue", "edit"),
        ("pr", "edit"),
    }
)


@dataclass
class _ProtectedPaths:
    """Paths an agent's shell commands must not modify or reference in code."""

    abs_paths: frozenset[str]
    names: frozenset[str]
    protected_dirs: frozenset[str]
    host_scripts: frozenset[str]
    regex: re.Pattern[str] | None
    protected_prefixes: list[str] = field(default_factory=list)

    @classmethod
    def build(
        cls, spec_file_path: str, protected_files: list[str], repo_root: str
    ) -> _ProtectedPaths:
        abs_paths: set[str] = set()
        names: set[str] = set()  # matched as substrings with path boundaries

        def add(path_str: str, *, distinctive_name: bool) -> None:
            if not path_str:
                return
            p = Path(path_str).expanduser()
            absolute = p if p.is_absolute() else Path(repo_root) / p
            abs_paths.add(os.path.normpath(str(absolute)))
            if not p.is_absolute():
                names.add(os.path.normpath(path_str))
            if distinctive_name and p.name not in _GENERIC_PROTECTED_BASENAMES:
                names.add(p.name)

        protected_prefixes = []
        for entry in SCANNER_PROTECTED_FILES:
            if entry.startswith("PREFIX:"):
                protected_prefixes.append(entry[7:])
            else:
                add(entry, distinctive_name=True)
        for name in _STATIC_PROTECTED:
            add(name, distinctive_name=True)
        add(spec_file_path, distinctive_name=True)
        for pf in protected_files:
            add(str(pf), distinctive_name=True)
        host_scripts: set[str] = set()
        for script in HOST_SECURITY_SCRIPTS:
            real = os.path.normpath(str(_SCRIPTS_DIR / script))
            abs_paths.add(real)
            host_scripts.add(real)
            names.add(script)

        alternatives = sorted(names | abs_paths, key=len, reverse=True)
        regex = None
        if alternatives:
            regex = re.compile(
                r"(?<![A-Za-z0-9_\-])(?:"
                + "|".join(re.escape(a) for a in alternatives)
                + r")(?![A-Za-z0-9_\-])"
            )
        return cls(
            abs_paths=frozenset(abs_paths),
            names=frozenset(names),
            protected_dirs=frozenset(os.path.dirname(p) for p in abs_paths),
            host_scripts=frozenset(host_scripts),
            regex=regex,
            protected_prefixes=protected_prefixes,
        )

    def _is_prefix_protected(self, path: str) -> bool:
        """Check if path falls under a prefix-protected directory."""
        norm = os.path.normpath(os.path.realpath(path))
        for prefix in self.protected_prefixes:
            if norm.startswith(prefix):
                rel = norm[len(prefix):]
                parts = rel.split("/")
                # Allow writes under sidecars/<repo>/state/ (but not trust-sensitive files)
                if len(parts) >= 3 and parts[0] == "sidecars" and parts[2] == "state":
                    basename = os.path.basename(norm)
                    if (
                        basename == "injection_cache.json"
                        or basename.startswith(".integrity_snapshot")
                        or any(p.startswith(".integrity_snapshot") for p in parts)
                    ):
                        return True   # still protected
                    return False      # allow
                # Allow execution of trusted daemon scripts (manage.py, tick.py)
                if len(parts) == 2 and parts[0] == "scripts" and parts[1] in DAEMON_TRUSTED_SCRIPTS:
                    return False      # allow execution
                return True           # protected
        return False

    def _basename_protected(self, base: str) -> bool:
        return (
            base.startswith(".integrity_snapshot")
            or base in self.names
            or base in _GENERIC_PROTECTED_BASENAMES
            or any(os.path.basename(p) == base for p in self.abs_paths)
        )

    def resolve(self, token: str, cwd: str | None) -> str | None:
        if not token or DYN in token:
            return None
        p = os.path.expanduser(token)
        if not os.path.isabs(p):
            if cwd is None:
                return None
            p = os.path.join(cwd, p)
        p = os.path.normpath(p)
        try:
            parent = os.path.dirname(p)
            if os.path.isdir(parent):
                p = os.path.join(os.path.realpath(parent), os.path.basename(p))
            if os.path.islink(p):
                p = os.path.realpath(p)
        except OSError:
            pass
        return p

    def mentioned_in(self, text: str, cwd: str | None) -> bool:
        """True when `text` names, resolves to, or globs onto a protected path."""
        if not text:
            return False
        if self.regex and self.regex.search(text):
            return True
        tokens = [
            t for variant in expand_braces(text) for t in re.split(r"[\s'\"=,;:()<>|&]+", variant)
        ]
        for token in tokens:
            if not token:
                continue
            if any(part.startswith(".integrity_snapshot") for part in token.split("/")):
                return True
            if DYN in token:
                head, _, base = token.rpartition("/")
                if base and DYN not in base:
                    if self._basename_protected(base):
                        return True
                elif head and DYN not in head:
                    directory = self.resolve(head, cwd)
                    if directory and directory in self.protected_dirs:
                        return True
                continue
            if any(ch in token for ch in "*?[{"):
                for candidate in expand_braces(token):
                    base = os.path.basename(candidate)
                    if any(fnmatch.fnmatchcase(os.path.basename(p), base) for p in self.abs_paths):
                        return True
                    if any(fnmatch.fnmatchcase(n, candidate) for n in self.names):
                        return True
                continue
            resolved = self.resolve(token, cwd)
            if resolved:
                if resolved in self.abs_paths:
                    return True
                if self._is_prefix_protected(resolved):
                    return True
            if (
                cwd is None
                and not os.path.isabs(token)
                and self._basename_protected(os.path.basename(token))
            ):
                return True
        return False


@dataclass
class _GateContext:
    protected: _ProtectedPaths
    patterns: list[re.Pattern[str]]
    enrolled: bool = True
    conversation_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    # Files written earlier in the command being checked: path -> known content (None = unknown).
    written: dict[str, str | None] = field(default_factory=dict)
    # Set once an earlier segment could have modified arbitrary files.
    dirty: bool = False


def _deny(reason: str) -> dict[str, str]:
    return {"decision": "deny", "reason": reason}


def compile_leak_patterns(
    patterns: list[str] | None, *, include_builtin: bool = True
) -> list[re.Pattern[str]]:
    """Compiles leak patterns; the built-in floor is always included for egress checks."""
    sources = list(BUILTIN_LEAK_PATTERNS) if include_builtin else []
    for pat in patterns or []:
        if isinstance(pat, str) and pat and pat not in sources:
            sources.append(pat)
    compiled: list[re.Pattern[str]] = []
    for pat in sources:
        try:
            compiled.append(re.compile(pat))
        except re.error as exc:
            sys.stderr.write(f"WARNING: Invalid leak pattern regex '{pat}': {exc}\n")
    return compiled


def _has_leak(texts: list[str], patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(t) for t in texts if t for p in patterns)


def _read_text_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read(_MAX_SCANNED_FILE_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return None
    return None


def _unwrap(words: list[Word]) -> tuple[Word | None, list[Word], str | None]:
    """Strips wrapper programs (env, sudo, nohup, timeout, …).

    Returns (program, args, split_string) where split_string is set for
    `env -S STRING`, whose argument is itself a command line.
    """
    i = 0
    while i < len(words):
        word = words[i]
        if word.dynamic:
            return word, words[i + 1 :], None
        name = os.path.basename(word.text)
        if name not in _WRAPPER_VALUE_FLAGS:
            return word, words[i + 1 :], None
        if name == "command" and i + 1 < len(words) and words[i + 1].text in ("-v", "-V"):
            return None, [], None
        value_flags = _WRAPPER_VALUE_FLAGS[name]
        i += 1
        took_duration = False
        while i < len(words):
            t = words[i].text
            if name == "env":
                if t in ("-S", "--split-string") and i + 1 < len(words):
                    return None, [], words[i + 1].text if words[i + 1].literal else DYN
                if t.startswith("-S") and len(t) > 2:
                    return None, [], t[2:] if words[i].literal else DYN
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                    i += 1
                    continue
            if t == "--":
                i += 1
                break
            if t in value_flags:
                i += 2
                continue
            if t.startswith("-") and len(t) > 1:
                i += 1
                continue
            if name == "timeout" and not took_duration:
                took_duration = True
                i += 1
                continue
            break
    return None, [], None


def _program_name(word: Word) -> str:
    name = os.path.basename(word.text)
    if re.fullmatch(r"python[0-9.]*", name):
        return "python"
    if re.fullmatch(r"(perl|ruby|php|node|lua)[0-9.]*", name):
        return re.sub(r"[0-9.]+$", "", name)
    return name


def _known_output(
    seg: Segment | None, cwd: str | None, ctx: _GateContext | None = None
) -> str | None:
    """Best-effort stdout of a segment that only echoes literal data."""
    if seg is None:
        return None
    prog, args, _ = _unwrap(seg.argv)
    if prog is None or prog.dynamic:
        return None
    name = _program_name(prog)
    if name in ("echo", "printf"):
        if any(w.dynamic for w in args):
            return None
        return " ".join(w.text for w in args if not re.fullmatch(r"-[neE]+", w.text))
    if name == "cat":
        if not args:
            if seg.stdin_texts and not seg.stdin_dynamic:
                return "\n".join(seg.stdin_texts)
            return None
        texts = []
        for w in args:
            status, content = _file_content(w, ctx, cwd)
            if status != "ok":
                return None
            texts.append(content or "")
        return "\n".join(texts)
    return None


def _file_content(word: Word, ctx: _GateContext | None, cwd: str | None) -> tuple[str, str | None]:
    """What `word` (a file operand) will contain when the command runs.

    Returns ("stdin", None) for stdin aliases, ("ok", text) when the content is
    pinned down, and ("unknown", None) when it is not: dynamic or globbed
    paths, missing files, FIFOs/devices, files rewritten with unknown content
    earlier in the same command, or any file once an earlier step of the
    command could have modified arbitrary files.
    """
    if word.dynamic or word.globbed:
        return "unknown", None
    if word.text == "-" or _STDIN_PATHS.match(word.text):
        return "stdin", None
    path = _resolve_path(word.text, cwd)
    if path is None:
        return "unknown", None
    real = os.path.realpath(path)
    if _STDIN_PATHS.match(real):
        return "stdin", None
    if ctx is not None:
        for key in (real, path):
            if key in ctx.written:
                content = ctx.written[key]
                return ("ok", content) if content is not None else ("unknown", None)
        if ctx.dirty:
            return "unknown", None
    if not os.path.isfile(real):
        return "unknown", None
    content = _read_text_file(real)
    return ("ok", content) if content is not None else ("unknown", None)


def _resolve_path(token: str, cwd: str | None) -> str | None:
    if not token or DYN in token:
        return None
    p = os.path.expanduser(token)
    if os.path.isabs(p):
        return os.path.normpath(p)
    if cwd is None:
        return None
    return os.path.normpath(os.path.join(cwd, p))


def _segment_stdin(
    seg: Segment, cwd: str | None, ctx: _GateContext | None = None
) -> tuple[bool, str | None]:
    """Returns (known, text) for what the segment reads on stdin."""
    if seg.stdin_texts:
        if seg.stdin_dynamic:
            return False, None
        return True, "\n".join(seg.stdin_texts)
    for target in seg.redirects_in:
        status, content = _file_content(target, ctx, cwd)
        return status == "ok", content
    if seg.piped_from is not None:
        out = _known_output(seg.piped_from, cwd, ctx)
        return out is not None, out
    # Not redirected or piped: whatever the agent's shell provides is unknown.
    return False, None


def _is_real_host_script(word: Word, cwd: str | None, protected: _ProtectedPaths) -> bool:
    path = protected.resolve(word.text, cwd) if word.literal and not word.globbed else None
    return bool(path) and path in protected.host_scripts


def _canonical_safe_post_paths() -> set[Path]:
    paths = {(_SCRIPTS_DIR / "safe_post.py").resolve()}
    global_safe_post = (
        Path.home()
        / ".gemini"
        / "config"
        / "plugins"
        / "bug-fixer-daemon"
        / "workspace-plugin"
        / "bug-fixer"
        / "scripts"
        / "safe_post.py"
    )
    paths.add(global_safe_post.expanduser().resolve())
    return paths


def _targets_proxy(word: Word, cwd: str | None) -> bool:
    """True when `word` resolves to the canonical safe_post.py script."""
    if not word.literal or word.dynamic or word.globbed:
        return False
    if word.text in ("safe_post.py", "safe_post"):
        return False
    resolved = _resolve_path(word.text, cwd)
    if not resolved:
        return False
    try:
        resolved_path = Path(resolved).expanduser().resolve()
    except OSError:
        return False
    return resolved_path in _canonical_safe_post_paths()


def _check_safe_post_invocation(
    seg: Segment,
    prog: Word,
    args: list[Word],
    script_word: Word,
    ctx: _GateContext | None = None,
    cwd: str | None = None,
) -> dict[str, str] | None:
    """Validate that a canonical safe_post.py invocation has no env overrides or unsafe flags."""
    if seg.assignments:
        return _deny(
            "Unsafe safe_post.py invocation: environment variable overrides are not permitted."
        )
    for w in seg.argv:
        if w is prog:
            break
        if w.dynamic or w.globbed:
            return _deny(REASON_DYNAMIC_EXEC)
        if os.path.basename(w.text) == "env":
            continue
        return _deny(
            "Unsafe safe_post.py invocation: wrapper flags or environment overrides are not permitted."
        )
    if _program_name(prog) == "python":
        for w in args:
            if w is script_word:
                break
            if not w.literal or w.dynamic or w.globbed or w.text not in _SAFE_PYTHON_FLAGS:
                return _deny(
                    "Unsafe safe_post.py invocation: unsafe Python interpreter flags are not permitted."
                )
    if ctx is not None:
        bodies: list[str] = []
        i = 0
        while i < len(args):
            w = args[i]
            t = w.text
            if t == "--body" and i + 1 < len(args):
                if not args[i + 1].dynamic:
                    bodies.append(args[i + 1].text)
                i += 2
                continue
            if t.startswith("--body="):
                if not w.dynamic:
                    bodies.append(t.partition("=")[2])
                i += 1
                continue
            if t == "--body-file" and i + 1 < len(args):
                status, content = _file_content(args[i + 1], ctx, cwd)
                if status == "ok" and content:
                    bodies.append(content)
                i += 2
                continue
            if t.startswith("--body-file="):
                file_word = Word(t.partition("=")[2], w.dynamic, w.globbed)
                status, content = _file_content(file_word, ctx, cwd)
                if status == "ok" and content:
                    bodies.append(content)
                i += 1
                continue
            i += 1
        if any(_ARCHITECT_TAG_RE.search(b) for b in bodies):
            if _is_non_architect_autonomous(ctx.conversation_id):
                return _deny(REASON_ARCHITECT_SPOOF)
    return None


def _check_code(code: str, ctx: _GateContext, cwd: str | None) -> dict[str, str] | None:
    """Policy for inline program text (python -c, node -e, piped/heredoc code)."""
    if _has_leak([code], ctx.patterns):
        return _deny(REASON_EGRESS_LEAK)
    if ctx.protected.mentioned_in(code, cwd):
        return _deny(REASON_SHELL_MUTATION)
    if (_SPAWN_CODE_REGEX.search(code) and _GH_WORD_REGEX.search(code)) or _QUOTED_GH_REGEX.search(
        code
    ):
        return _deny(REASON_INLINE_GH)
    return None


def _check_writer_words(
    seg: Segment, words: list[Word], ctx: _GateContext, cwd: str | None, include_stdin: bool = True
) -> dict[str, str] | None:
    texts = [w.text for w in words]
    if include_stdin:
        texts.extend(seg.stdin_texts)
    if any(ctx.protected.mentioned_in(t, cwd) for t in texts):
        return _deny(REASON_SHELL_MUTATION)
    return None


def _check_redirects(seg: Segment, ctx: _GateContext, cwd: str | None) -> dict[str, str] | None:
    for target in seg.redirects_out + seg.redirects_in:
        if re.match(r"^/dev/(tcp|udp)/", target.text):
            return _deny(REASON_RAW_NETWORK)
    for target in seg.redirects_out:
        text = target.text
        if text.startswith("/dev/") and target.literal:
            continue
        head, _, base = text.rpartition("/")
        if target.dynamic and DYN in base and (not head or DYN in head):
            return _deny(REASON_DYNAMIC_WRITE)
        if ctx.protected.mentioned_in(text, cwd):
            return _deny(REASON_SHELL_MUTATION)
    return None


def _parse_gh_args(
    group: str, sub: str, words: list[Word]
) -> tuple[list[tuple[str, Word | None]], list[Word]]:
    booleans = _GH_BOOLEAN_OVERRIDES.get((group, sub), frozenset()) | _GH_BOOLEAN_OVERRIDES.get(
        (group, ""), frozenset()
    )
    flags: list[tuple[str, Word | None]] = []
    positionals: list[Word] = []
    i = 0
    while i < len(words):
        w = words[i]
        t = w.text
        if t == "--":
            positionals.extend(words[i + 1 :])
            break
        if t.startswith("--") and "=" in t:
            name, _, value = t.partition("=")
            flags.append((name, Word(value, w.dynamic, w.globbed)))
        elif t.startswith("-") and not t.startswith("--") and len(t) > 2:
            # Clustered shorthand: booleans may be combined, and a value flag
            # takes the rest of the token or the next word (`-ab x`, `-bvalue`).
            k = 1
            while k < len(t):
                short = "-" + t[k]
                if short in _GH_VALUE_SHORT and short not in booleans:
                    remainder = t[k + 1 :]
                    if remainder:
                        flags.append((short, Word(remainder.lstrip("="), w.dynamic, w.globbed)))
                    elif i + 1 < len(words):
                        flags.append((short, words[i + 1]))
                        i += 1
                    else:
                        flags.append((short, None))
                    break
                flags.append((short, None))
                k += 1
        elif t.startswith("-") and len(t) > 1:
            takes_value = (t in _GH_VALUE_LONG or t in _GH_VALUE_SHORT) and t not in booleans
            if takes_value and i + 1 < len(words):
                flags.append((t, words[i + 1]))
                i += 2
                continue
            flags.append((t, None))
        else:
            positionals.append(w)
        i += 1
    return flags, positionals


def _check_gh_api(
    flags: list[tuple[str, Word | None]],
    positionals: list[Word],
    field_values: dict[str, list[Word]],
    file_texts: list[str],
) -> dict[str, str] | None:
    method_word = next((v for f, v in flags if f in ("-X", "--method") and v is not None), None)
    has_body = bool(field_values) or any(f == "--input" for f, _ in flags)
    if method_word is not None and method_word.dynamic:
        return _deny(REASON_DYNAMIC_EXEC)
    method = method_word.text.upper() if method_word else ("POST" if has_body else "GET")
    if not positionals:
        return None
    endpoint = positionals[0]
    if endpoint.dynamic:
        return _deny(REASON_RAW_GH if method == "GET" else REASON_DYNAMIC_EXEC)
    path = re.sub(r"^https?://[^/]+/", "", endpoint.text).split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"/+", "/", path).strip("/").lower()

    def field(name: str) -> list[Word]:
        return field_values.get(name, [])

    if path == "graphql":
        queries = field("query")
        if any(q.dynamic for q in queries):
            return _deny(REASON_DYNAMIC_EXEC)
        all_fields = [v for values in field_values.values() for v in values]
        if any(v.dynamic for v in all_fields):
            return _deny(REASON_DYNAMIC_EXEC)
        ql = " ".join([v.text for v in all_fields] + file_texts).lower()
        if re.search(r"mergepullrequest|enablepullrequestautomerge|mergebranch", ql):
            return _deny(REASON_AUTO_MERGE)
        if re.search(r"(add|submit)pullrequestreview", ql) and "approve" in ql:
            return _deny(REASON_SELF_APPROVAL)
        if "reopenissue" in ql or ("updateissue" in ql and re.search(r"\bopen\b", ql)):
            return _deny(REASON_ISSUE_REOPEN)
        if "mutation" not in ql and re.search(
            r"\b(issues?|pullrequests?|comments|search|discussion)\b", ql
        ):
            return _deny(REASON_RAW_GH)
        return None

    if re.search(r"(^|/)pulls/[^/]+/merge$", path) and method in ("PUT", "POST"):
        return _deny(REASON_AUTO_MERGE)
    if re.search(r"(^|/)merges$", path) and method == "POST":
        return _deny(REASON_AUTO_MERGE)
    if re.search(r"(^|/)git/refs/heads/(main|master)$", path) and method in (
        "PATCH",
        "POST",
        "PUT",
    ):
        return _deny(REASON_PUSH_DEFAULT_BRANCH)
    input_json = " ".join(file_texts)
    if re.search(r"(^|/)pulls/[^/]+/reviews(/[^/]+/events)?$", path) and method == "POST":
        events = field("event")
        if any(e.dynamic or e.text.upper() == "APPROVE" for e in events) or re.search(
            r'"event"\s*:\s*"approve"', input_json, re.IGNORECASE
        ):
            return _deny(REASON_SELF_APPROVAL)
    if re.search(r"(^|/)issues/[^/]+$", path) and method == "PATCH":
        states = field("state")
        if any(s.dynamic or s.text.lower() == "open" for s in states) or re.search(
            r'"state"\s*:\s*"open"', input_json, re.IGNORECASE
        ):
            return _deny(REASON_ISSUE_REOPEN)
    # Block adding protected labels via REST labels endpoint
    if re.search(r"(^|/)labels$", path) and method in ("POST", "PUT", "PATCH"):
        label_fields = field("labels") + field("labels[]") + field("name")
        for lbl in label_fields:
            if not lbl.dynamic:
                for individual in lbl.text.split(","):
                    stripped = individual.strip()
                    if stripped in PROTECTED_ADD_LABELS:
                        return _deny(REASON_LABEL_ESCALATION)
        if any(protected in input_json for protected in PROTECTED_ADD_LABELS):
            return _deny(REASON_LABEL_ESCALATION)
    if method == "GET" and (
        path.startswith("search/")
        or re.search(
            r"(^|/)(issues|pulls|comments|reviews|discussions|notifications|timeline|events)(/|$)",
            path,
        )
    ):
        return _deny(REASON_RAW_GH)
    return None


def _check_gh(
    seg: Segment, args: list[Word], ctx: _GateContext, cwd: str | None, embedded: bool = False
) -> dict[str, str] | None:
    if any(w.dynamic or w.globbed for w in args[:2]):
        return _deny(REASON_DYNAMIC_EXEC)
    group = args[0].text if args else ""
    # Persistent flags (e.g. `gh pr -R o/r merge 1`) may precede the subcommand.
    j = 1
    while j < len(args) and args[j].text.startswith("-") and group != "api":
        t = args[j].text
        if "=" in t or (len(t) > 2 and not t.startswith("--")):
            j += 1
        elif t in _GH_VALUE_LONG or t in _GH_VALUE_SHORT:
            j += 2
        else:
            j += 1
    if j < len(args) and (args[j].dynamic or args[j].globbed):
        return _deny(REASON_DYNAMIC_EXEC)
    sub = args[j].text if j < len(args) and group != "api" else ""
    if group and group not in _GH_KNOWN_GROUPS:
        return None if embedded else _deny(REASON_DYNAMIC_EXEC)
    if group in ("alias", "extension", "ext") and sub not in ("list", "ls", ""):
        return _deny(REASON_DYNAMIC_EXEC)
    if group == "config" and sub == "set":
        return _deny(REASON_DYNAMIC_EXEC)
    if group == "auth" and (sub == "token" or any(w.text in ("-t", "--show-token") for w in args)):
        return _deny(REASON_SECRET_DISCLOSURE)
    if (group, sub) in _GH_ACCOUNT_CHANGES or (
        group in ("secret", "variable", "ssh-key", "gpg-key") and sub not in ("list", "ls", "")
    ):
        return _deny(REASON_ACCOUNT_CHANGE)
    if (group, sub) == ("repo", "edit") and any(
        w.text.startswith(("--visibility", "--default-branch", "--enable-auto-merge")) for w in args
    ):
        return _deny(REASON_ACCOUNT_CHANGE)
    if (group, sub) == ("repo", "create") and any(w.text == "--public" for w in args):
        return _deny(REASON_ACCOUNT_CHANGE)
    if group in ("issue", "pr") and sub in ("list", "view", "status", "diff"):
        return _deny(REASON_RAW_GH)
    if group == "run" and sub in ("view", "watch"):
        return _deny(REASON_RAW_GH)
    if group == "search":
        return _deny(REASON_RAW_GH)
    if (group, sub) in (("pr", "merge"), ("stack", "merge")):
        return _deny(REASON_AUTO_MERGE)
    if (group, sub) == ("issue", "reopen"):
        return _deny(REASON_ISSUE_REOPEN)

    rest = (args[1:j] + args[j + 1 :]) if group != "api" else args[1:]
    flags, positionals = _parse_gh_args(group, sub, rest)
    if (group, sub) == ("pr", "review") and any(f in ("--approve", "-a") for f, _ in flags):
        return _deny(REASON_SELF_APPROVAL)

    # Block adding trust-escalating labels (e.g. architect:approved)
    for flag, value in flags:
        if flag in ("--add-label", "--label", "-l") and value is not None and not value.dynamic:
            for individual in value.text.split(","):
                stripped = individual.strip()
                if stripped in PROTECTED_ADD_LABELS:
                    return _deny(REASON_LABEL_ESCALATION)

    bodies: list[str] = []  # text that is posted as a comment/body
    texts: list[str] = []  # every other outbound value
    file_texts: list[str] = []
    reads_stdin = False
    field_values: dict[str, list[Word]] = {}

    def read_file(word: Word, sink: list[str]) -> dict[str, str] | None:
        nonlocal reads_stdin
        status, content = _file_content(word, ctx, cwd)
        if status == "stdin":
            reads_stdin = True
            return None
        if status != "ok":
            return _deny(REASON_UNSCANNABLE)
        sink.append(content or "")
        file_texts.append(content or "")
        return None

    file_flags = {"--body-file", "--notes-file", "--input"}
    if group in ("issue", "pr", "release"):
        file_flags.add("-F")
    for flag, value in flags:
        if value is None:
            continue
        if flag in file_flags:
            denied = read_file(value, bodies if flag != "--input" else texts)
            if denied:
                return denied
        elif group == "api" and flag in ("-f", "-F", "--field", "--raw-field"):
            key, sep, val = value.text.partition("=")
            if not sep or DYN in key:
                return _deny(REASON_UNSCANNABLE)
            vword = Word(val, value.dynamic, value.globbed)
            field_values.setdefault(key, []).append(vword)
            if flag in ("-F", "--field") and val.startswith("@"):
                denied = read_file(Word(val[1:], value.dynamic, value.globbed), bodies)
                if denied:
                    return denied
            elif value.dynamic:
                return _deny(REASON_UNSCANNABLE)
            else:
                (bodies if key in ("body", "query") else texts).append(val)
        elif flag in _GH_BODY_FLAGS or flag in _GH_TEXT_FLAGS:
            if value.dynamic:
                return _deny(REASON_UNSCANNABLE)
            (bodies if flag in _GH_BODY_FLAGS else texts).append(value.text)

    if group == "gist" and sub in ("create", "new", "edit"):
        files = positionals if sub != "edit" else positionals[1:]
        if not files:
            reads_stdin = True
        for f in files:
            denied = read_file(f, bodies)
            if denied:
                return denied
    if group == "release" and sub in ("create", "upload"):
        for f in positionals[1:]:
            denied = read_file(Word(f.text.split("#", 1)[0], f.dynamic, f.globbed), texts)
            if denied:
                return denied

    known, stdin_text = _segment_stdin(seg, cwd, ctx)
    if reads_stdin:
        if not known:
            return _deny(REASON_UNSCANNABLE)
        bodies.append(stdin_text or "")
    elif seg.piped_from is not None and stdin_text:
        # gh falls back to stdin for the body when no body flag is given.
        bodies.append(stdin_text)

    literal_words = [w.text for w in args if w.literal]
    if _has_leak(literal_words + bodies + texts, ctx.patterns):
        return _deny(REASON_EGRESS_LEAK)

    commenting = (group, sub) in _GH_COMMENTING or (
        group == "api"
        and positionals
        and re.search(r"/(comments|reviews)(/|$)", positionals[0].text)
    )
    if commenting and any(_looks_like_approval(b) for b in bodies):
        return _deny(REASON_APPROVAL_INJECTION)
    if commenting and any(_ARCHITECT_TAG_RE.search(b) for b in bodies):
        if _is_non_architect_autonomous(ctx.conversation_id):
            return _deny(REASON_ARCHITECT_SPOOF)

    if group == "api":
        return _check_gh_api(flags, positionals, field_values, file_texts)
    return None


def _check_git(
    args: list[Word], ctx: _GateContext, cwd: str | None, depth: int = 0
) -> tuple[dict[str, str] | None, str | None]:
    i = 0
    while i < len(args):
        t = args[i].text
        if t in ("-C",) and i + 1 < len(args):
            cwd = _resolve_path(args[i + 1].text, cwd) if args[i + 1].literal else None
            i += 2
            continue
        if t == "-c" and i + 1 < len(args):
            if args[i + 1].dynamic or _GIT_EXEC_CONFIG.match(args[i + 1].text.split("=", 1)[0]):
                return _deny(REASON_DYNAMIC_EXEC), cwd
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break
    sub = args[i].text if i < len(args) else ""
    rest = args[i + 1 :]
    # Subcommands that run arbitrary commands: analyze what they run.
    exec_strings: list[Word] = []
    for j, w in enumerate(rest):
        t = w.text
        if (
            sub in ("rebase", "difftool", "mergetool")
            and t in ("-x", "--exec", "--extcmd", "--tool-cmd")
            and j + 1 < len(rest)
        ):
            exec_strings.append(rest[j + 1])
        elif sub in ("rebase", "difftool") and t.startswith(("--exec=", "--extcmd=")):
            exec_strings.append(Word(t.split("=", 1)[1], w.dynamic))
        elif sub == "filter-branch" and t.endswith("-filter") and j + 1 < len(rest):
            exec_strings.append(rest[j + 1])
    if sub == "submodule" and rest and rest[0].text == "foreach":
        tail = [w for w in rest[1:] if not w.text.startswith("--")]
        exec_strings.extend(
            tail[:1]
            if len(tail) == 1
            else [Word(" ".join(w.text for w in tail), any(w.dynamic for w in tail))]
        )
    if sub == "bisect" and rest and rest[0].text == "run" and len(rest) > 1:
        exec_strings.append(
            Word(" ".join(w.text for w in rest[1:]), any(w.dynamic for w in rest[1:]))
        )
    for cmd_word in exec_strings:
        if cmd_word.dynamic:
            return _deny(REASON_DYNAMIC_EXEC), cwd
        denied = _check_command_text(cmd_word.text, ctx, cwd, depth + 1)
        if denied:
            return denied, cwd
    if sub == "config":
        keys = [w for w in rest if not w.text.startswith("-")]
        if keys and (keys[0].dynamic or _GIT_EXEC_CONFIG.match(keys[0].text)) and len(keys) > 1:
            return _deny(REASON_DYNAMIC_EXEC), cwd
    if sub in ("commit", "push", "merge", "am", "cherry-pick"):
        for w in rest:
            if w.text == "--":
                break
            if w.text == "--no-verify":
                return _deny("Bypassing git hooks (--no-verify) is prohibited."), cwd
            if sub == "commit" and (
                w.text == "-n"
                or (
                    w.text.startswith("-")
                    and not w.text.startswith("--")
                    and re.fullmatch(r"-[asvqnem]+", w.text)
                    and "n" in w.text.split("m", 1)[0]
                )
            ):
                return _deny("Bypassing git hooks (--no-verify) is prohibited."), cwd
    if sub in _GIT_PATH_WRITERS and any(ctx.protected.mentioned_in(w.text, cwd) for w in rest):
        return _deny(REASON_SHELL_MUTATION), cwd
    if sub in _GIT_EGRESS:
        texts = [w.text for w in rest if w.literal]
        for j, w in enumerate(rest):
            if w.text in ("-F", "--file") and j + 1 < len(rest):
                status, content = _file_content(rest[j + 1], ctx, cwd)
                if status != "ok":
                    return _deny(REASON_UNSCANNABLE), cwd
                texts.append(content or "")
        if _has_leak(texts, ctx.patterns):
            return _deny(REASON_EGRESS_LEAK), cwd
    if sub == "push" and _pushes_default_branch(rest, cwd):
        return _deny(REASON_PUSH_DEFAULT_BRANCH), cwd
    if sub == "push" and ctx.enrolled:
        scan_dir = cwd or _repo_root(ctx.config, None)
        if scan_dir and os.path.exists(os.path.join(scan_dir, ".git")):
            raw_excludes = list((ctx.config or {}).get("scan_exclude_paths") or [])
            exclude_paths = list(
                dict.fromkeys([*raw_excludes, *RegexBackend.DEFAULT_REGEX_EXCLUDE_PATHS])
            )
            violations = _scan_with_builtin_floor(
                scan_dir,
                ScanScope.TREE,
                exclude_paths=exclude_paths,
                config=ctx.config,
            )
            if violations:
                return (
                    _deny(
                        f"{REASON_EGRESS_LEAK} (Detected in repository tree before git push: {violations[0]})"
                    ),
                    cwd,
                )
    if sub and sub not in _GIT_KNOWN_SUBCOMMANDS:
        return _deny(REASON_DYNAMIC_EXEC), cwd  # git aliases run arbitrary commands
    return None, cwd


def _current_branch(cwd: str | None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _pushes_default_branch(args: list[Word], cwd: str | None) -> bool:
    """True when a `git push` would update main/master on the remote."""
    if any(w.text in ("--all", "--mirror") for w in args):
        return True
    operands = [w for w in args if not w.text.startswith("-")]
    if any(w.dynamic for w in operands):
        return True
    refspecs = operands[1:]
    if not refspecs:
        branch = _current_branch(cwd)
        return branch is None or branch in _DEFAULT_BRANCHES
    for spec in refspecs:
        source, _, target = spec.text.lstrip("+").rpartition(":")
        target = target or source
        if target in ("HEAD", "@"):
            branch = _current_branch(cwd)
            if branch is None or branch in _DEFAULT_BRANCHES:
                return True
            continue
        if target.removeprefix("refs/heads/") in _DEFAULT_BRANCHES:
            return True
    return False


def _check_egress_program(
    name: str, seg: Segment, args: list[Word], ctx: _GateContext, cwd: str | None
) -> dict[str, str] | None:
    """Network tools: scan literal arguments, referenced files and stdin."""
    texts = [w.text for w in args if w.literal]
    operands: list[Word] = []
    for j, w in enumerate(args):
        if w.text.startswith("@"):
            operands.append(Word(w.text[1:], w.dynamic, w.globbed))
        elif "=@" in w.text or "=<" in w.text:
            operands.append(Word(re.split(r"=[@<]", w.text, maxsplit=1)[1], w.dynamic, w.globbed))
        elif w.text in ("-T", "--upload-file", "--post-file", "--body-file") and j + 1 < len(args):
            operands.append(args[j + 1])
        elif w.text.startswith(("--post-file=", "--body-file=", "--upload-file=")):
            operands.append(Word(w.text.split("=", 1)[1], w.dynamic, w.globbed))
    if name in ("scp", "rsync", "sftp"):
        positional = [w for w in args if not w.text.startswith("-")]
        if any(":" in w.text for w in positional[-1:]):
            # Uploading: every local source operand leaves the machine.
            operands.extend(w for w in positional[:-1] if ":" not in w.text)
    for operand in operands:
        if operand.literal and not operand.globbed:
            path = _resolve_path(operand.text, cwd)
            if path and os.path.isdir(path):
                return _deny(REASON_UNSCANNABLE)
        status, content = _file_content(operand, ctx, cwd)
        if status == "stdin":
            known, content = _segment_stdin(seg, cwd, ctx)
            if not known:
                return _deny(REASON_UNSCANNABLE)
        elif status != "ok":
            return _deny(REASON_UNSCANNABLE)
        texts.append(content or "")
    if name in ("nc", "ncat", "netcat", "socat", "telnet", "ssh") and (
        seg.piped_from is not None or seg.redirects_in or seg.stdin_texts
    ):
        known, content = _segment_stdin(seg, cwd, ctx)
        if not known:
            return _deny(REASON_UNSCANNABLE)
        texts.append(content or "")
    if _has_leak(texts, ctx.patterns):
        return _deny(REASON_EGRESS_LEAK)
    return None


def _check_interpreter(
    name: str,
    seg: Segment,
    prog: Word,
    args: list[Word],
    ctx: _GateContext,
    cwd: str | None,
    depth: int,
) -> dict[str, str] | None:
    code_flags = _INTERPRETER_CODE_FLAGS[name]
    codes: list[Word] = []
    script: Word | None = None
    module_mode = False
    explicit_stdin = False
    i = 0
    while i < len(args):
        t = args[i].text
        if name in _AWKS:
            if t == "-f" and i + 1 < len(args):
                script = args[i + 1]
                break
            if t in ("-v", "-F") and i + 1 < len(args):
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            codes.append(args[i])
            break
        if t in code_flags and i + 1 < len(args):
            codes.append(args[i + 1])
            i += 2
            continue
        long_flag, eq, long_value = t.partition("=")
        if eq and long_flag in code_flags and long_flag.startswith("--"):
            codes.append(Word(long_value, args[i].dynamic))
            i += 1
            continue
        letters = {f[1] for f in code_flags if len(f) == 2 and f.startswith("-")}
        if t.startswith("-") and not t.startswith("--") and len(t) > 2 and letters:
            pos = next((k for k in range(1, len(t)) if t[k] in letters), None)
            if pos is not None:
                if pos == len(t) - 1:
                    if i + 1 < len(args):
                        codes.append(args[i + 1])
                    i += 2
                else:
                    codes.append(Word(t[pos + 1 :], args[i].dynamic))
                    i += 1
                continue
        if name == "python" and t == "-m":
            module_mode = True
            break
        if name == "python" and t in ("-W", "-X") and i + 1 < len(args):
            i += 2
            continue
        if t == "-":
            explicit_stdin = True
            break
        if not t.startswith("-"):
            script = args[i]
            break
        i += 1

    for code in codes:
        if code.dynamic:
            return _deny(REASON_DYNAMIC_EXEC)
        denied = _check_code(code.text, ctx, cwd)
        if denied:
            return denied

    if script is not None and script.literal and _STDIN_PATHS.match(script.text):
        script, explicit_stdin = None, True
    if script is not None:
        if script.dynamic or script.globbed:
            return _deny(REASON_DYNAMIC_EXEC)
        if _targets_proxy(script, cwd):
            if codes or module_mode:
                return _deny(
                    "Unsafe safe_post.py invocation: inline code or module flags are not permitted."
                )
            denied = _check_safe_post_invocation(seg, prog, args, script, ctx, cwd)
            if denied:
                return denied
            return None  # allow safe_post.py — it scans content itself
        if _is_real_host_script(script, cwd, ctx.protected):
            return None  # trusted plugin entry point (safe_ingest.py, pre_tool_gate.py, …)
    elif (
        not codes
        and not module_mode
        and (explicit_stdin or not args or seg.piped_from or seg.stdin_texts)
    ):
        known, stdin_code = _segment_stdin(seg, cwd, ctx)
        if not known:
            return _deny(REASON_DYNAMIC_EXEC)
        if stdin_code:
            denied = _check_code(stdin_code, ctx, cwd)
            if denied:
                return denied

    if name in _AWKS:
        return None  # awk only writes via its program text, which was checked above
    remaining = [w for w in args if w not in codes]
    return _check_writer_words(seg, remaining, ctx, cwd, include_stdin=False)


_RUNNER_NAMES = (
    frozenset({"gh", "eval", "source", ".", "xargs"}) | _SHELLS | frozenset(_INTERPRETER_CODE_FLAGS)
)


def _check_embedded(
    args: list[Word], ctx: _GateContext, cwd: str | None, depth: int
) -> dict[str, str] | None:
    """Finds commands passed to wrapper programs the gate does not model.

    Programs such as `script`, `flock`, `arch`, `tmux`, `ssh`, `npx` or `uv run`
    take a command as trailing words (`flock /tmp/l gh pr merge 1`) or as one
    string (`tmux new -d 'gh pr merge 1'`). Any such gh / shell / interpreter /
    proxy invocation is analyzed as if it had been typed directly.
    """
    literal_texts = [w.text for w in args if w.literal]
    if _has_leak(literal_texts, ctx.patterns):
        return _deny(REASON_EGRESS_LEAK)
    for text in literal_texts:
        if (
            _SPAWN_CODE_REGEX.search(text) and _GH_WORD_REGEX.search(text)
        ) or _QUOTED_GH_REGEX.search(text):
            return _deny(REASON_INLINE_GH)
    for k, w in enumerate(args):
        if w.dynamic:
            continue
        if _targets_proxy(w, cwd):
            denied = _check_safe_post_invocation(
                Segment(words=args[k:]), w, args[k + 1 :], w, ctx, cwd
            )
            if denied:
                return denied
            return None  # allow safe_post.py — it scans content itself
        name = _program_name(w)
        if name == "gh":
            return _check_gh(Segment(words=args[k:]), args[k + 1 :], ctx, cwd, embedded=True)
        if name in _RUNNER_NAMES and name != "gh":
            tail = args[k + 1 :]
            code_flags = set(_INTERPRETER_CODE_FLAGS.get(name, ()))
            runs_code = name in ("eval", "xargs") or any(
                t.text == "-c"
                or t.text in code_flags
                or t.text.startswith("--eval")
                or (
                    t.text.startswith("-")
                    and not t.text.startswith("--")
                    and any(c in t.text[1:] for c in "ceEr")
                )
                for t in tail
            )
            if runs_code:
                denied, _ = _check_segment(Segment(words=args[k:]), ctx, cwd, depth + 1)
                if denied:
                    return denied
        if " " in w.text or "\n" in w.text:
            try:
                inner = parse_shell(w.text)
            except ShellParseError:
                continue
            first = inner.segments[0].argv if inner.segments else []
            prog, _, _ = _unwrap(first) if first else (None, [], None)
            if (
                prog is not None
                and prog.literal
                and (_program_name(prog) in _RUNNER_NAMES or _targets_proxy(prog, cwd))
            ):
                denied = _check_command_text(w.text, ctx, cwd, depth + 1)
                if denied:
                    return denied
    return None


_PYTHON_INTERP_RE = re.compile(r"^python3?(?:\.\d+)?$")
_SAFE_PYTHON_FLAGS = frozenset({"-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-I"})
_PROTECTED_TRUST_ENV_VARS = frozenset({"BUG_FIXER_TRUSTED_USERS", "BUG_FIXER_CONFIG"})
_MANAGE_READONLY_SUBCOMMANDS = frozenset({"status", "logs"})
_MANAGE_MUTABLE_SUBCOMMANDS = frozenset(
    {"enroll", "unenroll", "reset", "labels", "refresh-sidecars"}
)
_MANAGE_MUTABLE_CONFIG_KEYS = frozenset({
    "test_command",
    "lint_command",
    "triage_interval_min",
    "engineering_interval_min",
    "architect_interval_min",
    "trajectory_interval_min",
    "triage_model",
    "engineering_model",
    "architect_model",
    "trajectory_model",
    "max_concurrent_prs",
    "active_milestone",
    "milestone_priority_boost",
    "pr_prefix",
    "branch_prefix",
})


def _is_autonomous_session(conversation_id: str) -> bool:
    """Return True when executing inside an autonomous sidecar or dispatched session."""
    if "sidecar" in os.environ.get("ANTIGRAVITY_SOURCE_METADATA", "").lower():
        return True
    return BugFixer.get_session_context(conversation_id).is_autonomous


_ARCHITECT_TAG_RE = re.compile(r"(?m)^\s*\[ARCHITECT\]", re.IGNORECASE)


def _is_non_architect_autonomous(conversation_id: str) -> bool:
    """Return True when executing inside an autonomous session other than architect_review."""
    env_type = os.environ.get("BUG_FIXER_SESSION_TYPE", "").strip()
    if env_type:
        return env_type != "architect_review"
    sctx = BugFixer.get_session_context(conversation_id)
    if sctx.is_autonomous:
        return sctx.session_type != "architect_review"
    if "sidecar" in os.environ.get("ANTIGRAVITY_SOURCE_METADATA", "").lower():
        return True
    return False


def _canonical_manage_paths() -> set[Path]:
    daemon_dir = _find_daemon_dir(_SCRIPTS_DIR) or _SCRIPTS_DIR.parent.parent.parent
    paths = {(daemon_dir / "scripts" / "manage.py").resolve()}
    global_manage = (
        Path.home()
        / ".gemini"
        / "config"
        / "plugins"
        / "bug-fixer-daemon"
        / "scripts"
        / "manage.py"
    )
    paths.add(global_manage.expanduser().resolve())
    return paths


def _resolve_manage_candidate(token_str: str, cwd: str | None) -> Path | None:
    if not token_str or DYN in token_str:
        return None
    p = Path(token_str).expanduser()
    if not p.is_absolute():
        if not cwd:
            return None
        p = Path(cwd) / p
    try:
        return p.resolve()
    except OSError:
        return None


def _classify_manage_invocation(
    seg: Segment, cwd: str | None
) -> tuple[str, str] | None:
    """Classify a manage.py invocation into ('allow', ''), ('mutate', summary), or ('deny', reason).

    Returns None when the segment does not execute manage.py.
    """
    prog, args, split_string = _unwrap(seg.argv)
    if prog is None or prog.dynamic or split_string is not None:
        return None

    canonical_paths = _canonical_manage_paths()
    prog_resolved = _resolve_manage_candidate(prog.text, cwd)
    is_direct_manage = (
        Path(prog.text).name == "manage.py"
        or (prog_resolved is not None and prog_resolved in canonical_paths)
    )
    is_python_manage = False
    if not is_direct_manage and _program_name(prog) == "python":
        for w in args:
            if w.dynamic:
                continue
            if w.text.startswith("-"):
                continue
            w_res = _resolve_manage_candidate(w.text, cwd)
            if Path(w.text).name == "manage.py" or (
                w_res is not None and w_res in canonical_paths
            ):
                is_python_manage = True
            break

    if not is_direct_manage and not is_python_manage:
        return None

    if seg.assignments or seg.redirects_out or seg.piped_from is not None:
        return (
            "deny",
            "Unsafe manage.py invocation: environment overrides, unsafe interpreter flags, or output redirects are not permitted.",
        )

    argv = seg.argv
    if not argv or any(not w.literal or w.globbed for w in argv) or argv[0].text != prog.text:
        return (
            "deny",
            "Unsafe manage.py invocation: environment overrides, unsafe interpreter flags, or output redirects are not permitted.",
        )

    idx = 0
    if _PYTHON_INTERP_RE.match(Path(argv[idx].text).name):
        idx += 1
        while idx < len(argv) and argv[idx].text.startswith("-"):
            if argv[idx].text not in _SAFE_PYTHON_FLAGS:
                return (
                    "deny",
                    "Unsafe manage.py invocation: environment overrides, unsafe interpreter flags, or output redirects are not permitted.",
                )
            idx += 1

    if idx >= len(argv):
        return ("deny", "Unsafe manage.py invocation: missing script path.")

    resolved_script = _resolve_manage_candidate(argv[idx].text, cwd)
    if resolved_script is None or resolved_script not in canonical_paths:
        return ("deny", "Non-canonical manage.py path is not permitted.")
    idx += 1

    rest = [w.text for w in argv[idx:]]
    if not rest or rest[0] in ("-h", "--help"):
        return ("allow", "")

    sub = rest[0]
    sub_args = rest[1:]
    if sub in _MANAGE_READONLY_SUBCOMMANDS:
        return ("allow", "")
    if sub == "config":
        set_values: list[str] = []
        for j, a in enumerate(sub_args):
            if a == "--set":
                set_values.append(sub_args[j + 1] if j + 1 < len(sub_args) else "")
            elif a.startswith("--set="):
                set_values.append(a.partition("=")[2])
        if not set_values:
            return ("allow", "")
        for sv in set_values:
            key, sep, _ = sv.partition("=")
            if not key or not sep or key not in _MANAGE_MUTABLE_CONFIG_KEYS:
                return (
                    "deny",
                    f"Config key '{key}' cannot be modified via manage.py config --set.",
                )
        return ("mutate", f"config {' '.join(sub_args)}".strip())
    if sub in _MANAGE_MUTABLE_SUBCOMMANDS:
        return ("mutate", f"{sub} {' '.join(sub_args)}".strip())
    return ("deny", "Unknown manage.py subcommand.")


def _check_safe_ingest_and_trust_env(
    seg: Segment, cwd: str | None
) -> dict[str, str] | None:
    """Block overrides of safe_ingest.py trust roots or trust environment variables."""
    for assign in seg.assignments:
        var_name = assign.text.split("=", 1)[0].rstrip("+")
        if var_name in _PROTECTED_TRUST_ENV_VARS:
            return _deny(REASON_SAFE_INGEST_OVERRIDE)

    argv = seg.argv
    if argv:
        first_base = os.path.basename(argv[0].text)
        if first_base in ("env", "export", "declare", "typeset"):
            for w in argv[1:]:
                if "=" in w.text:
                    var_name = w.text.lstrip("-").split("=", 1)[0]
                    if var_name in _PROTECTED_TRUST_ENV_VARS:
                        return _deny(REASON_SAFE_INGEST_OVERRIDE)

    for idx, w in enumerate(argv):
        if w.dynamic:
            continue
        base = os.path.basename(w.text)
        is_safe_ingest = base == "safe_ingest.py"
        if not is_safe_ingest:
            resolved = _resolve_path(w.text, cwd)
            if resolved:
                try:
                    is_safe_ingest = os.path.basename(os.path.realpath(resolved)) == "safe_ingest.py"
                except OSError:
                    is_safe_ingest = False
        if is_safe_ingest:
            for arg in argv[idx + 1 :]:
                if arg.text in ("--trusted-user", "--config") or arg.text.startswith(
                    ("--trusted-user=", "--config=")
                ):
                    return _deny(REASON_SAFE_INGEST_OVERRIDE)
            break
    for idx, w in enumerate(argv):
        if w.dynamic:
            continue
        base = os.path.basename(w.text)
        is_gate = base == "pre_tool_gate.py"
        if not is_gate:
            resolved = _resolve_path(w.text, cwd)
            if resolved:
                try:
                    is_gate = os.path.basename(os.path.realpath(resolved)) == "pre_tool_gate.py"
                except OSError:
                    is_gate = False
        if is_gate:
            for arg in argv[idx + 1 :]:
                if arg.text in ("--stamp-receipt", "--stamp-pass") or arg.text.startswith(
                    ("--stamp-receipt=", "--stamp-pass=")
                ):
                    return _deny(
                        "Direct receipt stamping (--stamp-receipt / --stamp-pass) via run_command is restricted; "
                        "use --scan-only or let the gate scan on git push."
                    )
            break
    return None


def _check_segment(
    seg: Segment, ctx: _GateContext, cwd: str | None, depth: int
) -> tuple[dict[str, str] | None, str | None]:
    trust_denied = _check_safe_ingest_and_trust_env(seg, cwd)
    if trust_denied:
        return trust_denied, cwd

    manage_class = _classify_manage_invocation(seg, cwd)
    if manage_class is not None:
        kind, detail = manage_class
        if kind == "deny":
            return _deny(detail), cwd
        if kind == "allow":
            return None, cwd
        if kind == "mutate":
            if _is_autonomous_session(ctx.conversation_id):
                return (
                    _deny("Autonomous bug-fixer sessions cannot execute management mutations."),
                    cwd,
                )
            return (
                {
                    "decision": "force_ask",
                    "reason": f"Confirm Bug Fixer management change: {detail}",
                },
                cwd,
            )

    denied = _check_redirects(seg, ctx, cwd)
    if denied:
        return denied, cwd

    prog, args, split_string = _unwrap(seg.argv)
    if split_string is not None:
        if split_string == DYN:
            return _deny(REASON_DYNAMIC_EXEC), cwd
        return _check_command_text(split_string, ctx, cwd, depth + 1), cwd
    if prog is None:
        return None, cwd
    if prog.dynamic:
        return _deny(REASON_DYNAMIC_EXEC), cwd
    if _targets_proxy(prog, cwd):
        denied = _check_safe_post_invocation(seg, prog, args, prog, ctx, cwd)
        if denied:
            return denied, cwd
        return None, cwd  # allow safe_post.py — it scans content itself
    if prog.globbed:
        return _deny(REASON_DYNAMIC_EXEC), cwd
    if (
        os.path.basename(prog.text) in HOST_SECURITY_SCRIPTS
        or os.path.basename(prog.text) == "safe_post"
    ) and not _is_real_host_script(prog, cwd, ctx.protected):
        return _deny("Non-canonical host security script path is not permitted."), cwd

    name = _program_name(prog)
    has_output = bool(seg.redirects_out)

    if name in ("cd", "pushd"):
        if not args:
            return None, os.path.expanduser("~")
        target = args[-1]
        if target.dynamic or target.text == "-":
            return None, None
        return None, _resolve_path(target.text, cwd)

    if name == "gh":
        return _check_gh(seg, args, ctx, cwd), cwd

    if name == "git":
        denied, _ = _check_git(args, ctx, cwd, depth)
        return denied, cwd

    if name in _SHELLS:
        rest = args[1:] if name == "busybox" and args else args
        if name == "busybox" and args and args[0].text not in _SHELLS:
            sub_seg = Segment(
                words=args,
                redirects_out=seg.redirects_out,
                stdin_texts=seg.stdin_texts,
                stdin_dynamic=seg.stdin_dynamic,
                piped_from=seg.piped_from,
            )
            return _check_segment(sub_seg, ctx, cwd, depth + 1)
        for j, w in enumerate(rest):
            if w.text == "-c" or (
                w.text.startswith("-") and "c" in w.text[1:] and not w.text.startswith("--")
            ):
                if j + 1 >= len(rest) or rest[j + 1].dynamic:
                    return _deny(REASON_DYNAMIC_EXEC), cwd
                return _check_command_text(rest[j + 1].text, ctx, cwd, depth + 1), cwd
        scripts = [w for w in rest if not w.text.startswith("-")]
        if scripts and scripts[0].literal and _STDIN_PATHS.match(scripts[0].text):
            scripts = []
            rest = [*rest, Word("-s")]  # `bash /dev/stdin` reads its program from stdin
        if scripts:
            if scripts[0].dynamic or scripts[0].globbed:
                return _deny(REASON_DYNAMIC_EXEC), cwd
            if _targets_proxy(scripts[0], cwd):
                denied = _check_safe_post_invocation(seg, prog, args, scripts[0], ctx, cwd)
                if denied:
                    return denied, cwd
                return None, cwd  # allow safe_post.py — it scans content itself
            return _check_writer_words(seg, scripts[1:], ctx, cwd, include_stdin=False), cwd
        if (
            rest
            and not any(w.text in ("-s", "-") for w in rest)
            and not seg.piped_from
            and not seg.stdin_texts
        ):
            return None, cwd  # options only (e.g. --version): no code is executed
        known, stdin_code = _segment_stdin(seg, cwd, ctx)
        if not known:
            return _deny(REASON_DYNAMIC_EXEC), cwd
        if stdin_code:
            return _check_command_text(stdin_code, ctx, cwd, depth + 1), cwd
        return None, cwd

    if name in ("eval",):
        if any(w.dynamic for w in args):
            return _deny(REASON_DYNAMIC_EXEC), cwd
        return _check_command_text(" ".join(w.text for w in args), ctx, cwd, depth + 1), cwd

    if name in ("source", "."):
        if args and (args[0].dynamic or args[0].globbed):
            return _deny(REASON_DYNAMIC_EXEC), cwd
        if args and (args[0].text == "-" or _STDIN_PATHS.match(args[0].text)):
            known, stdin_code = _segment_stdin(seg, cwd, ctx)
            if not known:
                return _deny(REASON_DYNAMIC_EXEC), cwd
            return _check_command_text(stdin_code or "", ctx, cwd, depth + 1), cwd
        return None, cwd

    if name in ("watch",):
        cmd_words = [w for w in args if not w.text.startswith("-")]
        if any(w.dynamic for w in cmd_words):
            return _deny(REASON_DYNAMIC_EXEC), cwd
        return _check_command_text(" ".join(w.text for w in cmd_words), ctx, cwd, depth + 1), cwd

    if name in ("xargs", "parallel"):
        j = 0
        while j < len(args) and args[j].text.startswith("-"):
            j += 2 if args[j].text in ("-I", "-n", "-L", "-P", "-d", "-E", "-s", "-a") else 1
        template = args[j:]
        if template:
            t_prog, t_args, _ = _unwrap(template)
            t_name = _program_name(t_prog) if t_prog is not None and t_prog.literal else ""
            if (
                t_prog is None
                or t_prog.dynamic
                or t_name in _SHELLS
                or t_name in _INTERPRETER_CODE_FLAGS
                or t_name in ("gh", "eval", "source", ".")
                or _targets_proxy(t_prog, cwd)
            ):
                return _deny(REASON_DYNAMIC_EXEC), cwd
            return _check_segment(Segment(words=template), ctx, cwd, depth + 1)
        return None, cwd

    if name in _INTERPRETER_CODE_FLAGS:
        if _is_real_host_script(prog, cwd, ctx.protected):
            return None, cwd
        return _check_interpreter(name, seg, prog, args, ctx, cwd, depth), cwd

    if name in _EGRESS_PROGRAMS:
        denied = _check_egress_program(name, seg, args, ctx, cwd)
        if denied:
            return denied, cwd

    if name not in _READ_ONLY_PROGRAMS or has_output:
        denied = _check_embedded(args, ctx, cwd, depth)
        if denied:
            return denied, cwd

    if name == "find":
        for j, w in enumerate(args):
            if w.text in _FIND_EXEC_FLAGS:
                end = next(
                    (k for k in range(j + 1, len(args)) if args[k].text in (";", "+")), len(args)
                )
                sub_words = args[j + 1 : end]
                if sub_words:
                    denied, _ = _check_segment(Segment(words=sub_words), ctx, cwd, depth + 1)
                    if denied:
                        return denied, cwd
        writes = any(w.text in _FIND_WRITE_FLAGS or w.text in _FIND_EXEC_FLAGS for w in args)
        if writes:
            for j, w in enumerate(args[:-1]):
                if w.text in ("-name", "-iname", "-path", "-ipath", "-wholename"):
                    pattern = args[j + 1]
                    if pattern.dynamic:
                        return _deny(REASON_DYNAMIC_WRITE), cwd
                    if any(
                        fnmatch.fnmatch(p, pattern.text)
                        or fnmatch.fnmatch(os.path.basename(p), pattern.text)
                        for p in ctx.protected.abs_paths
                    ):
                        return _deny(REASON_SHELL_MUTATION), cwd
            return _check_writer_words(seg, args, ctx, cwd), cwd

    if name in _DELETERS or name in _COPIERS:
        operands = [w for w in args if not w.text.startswith("-")]
        sources = operands if name in _DELETERS else operands[:-1]
        if name in _DELETERS or name in ("mv", "rsync"):
            for w in sources:
                resolved = ctx.protected.resolve(w.text, cwd)
                if resolved and any(
                    p == resolved or p.startswith(resolved.rstrip("/") + "/")
                    for p in ctx.protected.abs_paths
                ):
                    return _deny(REASON_SHELL_MUTATION), cwd
        if name in _COPIERS and len(operands) >= 2:
            dest = ctx.protected.resolve(operands[-1].text, cwd)
            if dest and dest in ctx.protected.protected_dirs:
                recursive = any(
                    re.fullmatch(r"-[A-Za-z]*[rRa][A-Za-z]*|--recursive|--archive", w.text)
                    for w in args
                )
                for w in sources:
                    src = _resolve_path(w.text, cwd)
                    if (
                        recursive
                        or w.dynamic
                        or w.globbed
                        or (src and os.path.isdir(src))
                        or w.text.endswith(("/", "/."))
                        or ctx.protected._basename_protected(os.path.basename(w.text.rstrip("/")))
                    ):
                        return _deny(REASON_SHELL_MUTATION), cwd

    if name == "ln":
        for w in args:
            resolved = ctx.protected.resolve(w.text, cwd)
            if ctx.protected.mentioned_in(w.text, cwd) or (
                resolved and resolved in ctx.protected.protected_dirs
            ):
                return _deny(REASON_SHELL_MUTATION), cwd

    if _is_read_only(name, args) and not has_output:
        return None, cwd
    if _is_real_host_script(prog, cwd, ctx.protected):
        return None, cwd  # direct execution of a trusted plugin script (not the proxy)
    return _check_writer_words(seg, args, ctx, cwd), cwd


def _is_read_only(name: str, args: list[Word]) -> bool:
    """True when the program cannot modify files (ignoring shell redirections)."""
    if name == "mkdir":
        return True  # creates directories only; cannot overwrite a protected file
    if name == "sed":
        return not any(
            w.text in ("-i", "--in-place") or w.text.startswith(("-i", "--in-place=")) for w in args
        ) and not any(_SED_WRITES_REGEX.search(w.text) for w in args if w.literal)
    if name not in _READ_ONLY_PROGRAMS:
        return False
    if name == "sort" and any(
        w.text in ("-o", "--output") or w.text.startswith("--output=") for w in args
    ):
        return False
    if name == "rg" and any(w.text.startswith("--pre") for w in args):
        return False
    if name == "find" and any(
        w.text in _FIND_WRITE_FLAGS or w.text in _FIND_EXEC_FLAGS for w in args
    ):
        return False
    return True


_GIT_READ_ONLY = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "blame",
        "ls-files",
        "ls-tree",
        "grep",
        "rev-parse",
        "describe",
        "shortlog",
        "cat-file",
        "branch",
        "remote",
        "config",
        "add",
        "commit",
        "push",
        "fetch",
        "tag",
        "notes",
        "stash list",
    }
)


def _record_effects(seg: Segment, ctx: _GateContext, cwd: str | None) -> None:
    """Records what an allowed segment writes, for later segments of the same command."""
    prog, args, _ = _unwrap(seg.argv)
    name = _program_name(prog) if prog is not None and prog.literal else ""
    content = _known_output(seg, cwd, ctx)
    if (
        content is not None
        and name == "echo"
        and not any(re.fullmatch(r"-[eE]*n[eE]*", w.text) for w in args)
    ):
        content += "\n"
    for target, append in zip(seg.redirects_out, seg.redirect_appends):
        path = _resolve_path(target.text, cwd)
        if path is None:
            ctx.dirty = True
            continue
        if path.startswith("/dev/"):
            continue
        real = os.path.realpath(path)
        if append:
            prior = (
                ctx.written[real] if real in ctx.written else (None if os.path.exists(real) else "")
            )
        else:
            prior = ""
        ctx.written[real] = None if content is None or prior is None else prior + content
    if prog is None or name in ("cd", "pushd", "echo", "printf"):
        return
    if prog.dynamic:
        ctx.dirty = True
        return
    if name == "gh":
        if any(w.text in ("download", "clone", "checkout", "sync") for w in args[:3]):
            ctx.dirty = True
        return
    if name == "git":
        sub = next((w.text for w in args if not w.text.startswith("-")), "")
        if sub not in _GIT_READ_ONLY:
            ctx.dirty = True
        return
    if not _is_read_only(name, args):
        ctx.dirty = True


def _check_command_text(
    cmd: str, ctx: _GateContext, cwd: str | None, depth: int = 0
) -> dict[str, str] | None:
    if depth > _MAX_RECURSION:
        return _deny(REASON_PARSE)
    try:
        parsed = parse_shell(cmd)
    except ShellParseError:
        return _deny(REASON_PARSE)
    _workflow_reasons = {
        REASON_RAW_GH,
        REASON_AUTO_MERGE,
        REASON_SELF_APPROVAL,
        REASON_ISSUE_REOPEN,
        REASON_APPROVAL_INJECTION,
    }
    active_segments = [
        s for s in parsed.segments
        if s.words or s.redirects_out or s.redirects_in or s.assignments
    ]
    current = cwd
    pending_decision: dict[str, str] | None = None
    for seg in parsed.segments:
        before = current
        manage_class = _classify_manage_invocation(seg, current)
        if manage_class is not None and manage_class[0] == "mutate":
            if len(active_segments) > 1 or parsed.substitutions or depth > 0:
                return _deny(
                    "Management mutations must be run as a standalone command "
                    "without shell chaining or substitutions."
                )
        denied, current = _check_segment(seg, ctx, current, depth)
        if denied:
            # In unenrolled workspaces, skip workflow-specific denials but
            # continue checking remaining segments for security violations.
            if (
                not ctx.enrolled
                and denied.get("decision") == "deny"
                and denied.get("reason") in _workflow_reasons
            ):
                pass  # skip this denial, check next segment
            elif denied.get("decision") == "force_ask":
                pending_decision = denied
            else:
                return denied
        _record_effects(seg, ctx, before)
    for sub in parsed.substitutions:
        denied = _check_command_text(sub, ctx, cwd, depth + 1)
        if denied:
            if (
                not ctx.enrolled
                and denied.get("decision") == "deny"
                and denied.get("reason") in _workflow_reasons
            ):
                pass
            else:
                return denied
    return pending_decision


def _repo_root(config: dict[str, Any] | None, cwd: str | None) -> str:
    if config:
        if config.get("repo_dir"):
            return str(config["repo_dir"])
        loaded = config.get("_loaded_from")
        if loaded:
            ws_dir = Path(loaded).resolve().parent
            if ws_dir.name == "bug-fixer-loop-workspace" and ws_dir.parent.parent.name == ".agents":
                return str(ws_dir.parent.parent.parent)
    return cwd or os.getcwd()


def _build_context(
    config: dict[str, Any] | None,
    spec_file_path: str,
    protected_files: list[str],
    cwd: str | None,
    conversation_id: str = "",
) -> _GateContext:
    patterns = compile_leak_patterns((config or {}).get("leak_patterns"))
    enrolled = (config or {}).get("_enrolled", True) is not False
    return _GateContext(
        protected=_ProtectedPaths.build(
            spec_file_path, list(protected_files), _repo_root(config, cwd)
        ),
        patterns=patterns,
        enrolled=enrolled,
        conversation_id=conversation_id,
        config=config or {},
    )


def check_shell_mutation(
    cmd: str, spec_file_path: str, protected_files: list[str], cwd: str | None = None
) -> bool:
    """True when the command would modify (or reference in code) a protected file."""
    ctx = _build_context(None, spec_file_path, protected_files, cwd)
    res = _check_command_text(cmd, ctx, cwd or os.getcwd())
    return bool(res) and res["reason"] in (REASON_SHELL_MUTATION, REASON_DYNAMIC_WRITE)


def extract_outbound_body(cmd: str) -> str | None:
    """Returns the outbound text a gh command would post, or None when it posts nothing."""
    try:
        parsed = parse_shell(cmd)
    except ShellParseError:
        return None
    cwd = os.getcwd()
    for seg in parsed.segments:
        prog, args, _ = _unwrap(seg.argv)
        if prog is None or prog.dynamic or _program_name(prog) != "gh" or len(args) < 1:
            continue
        group = args[0].text
        sub = args[1].text if len(args) > 1 else ""
        flags, positionals = _parse_gh_args(
            group, sub if group != "api" else "", args[2:] if group != "api" else args[1:]
        )
        out: list[str] = []
        for flag, value in flags:
            if value is None or value.dynamic:
                continue
            if flag in ("--body-file", "-F") and group != "api":
                content = _read_text_file(_resolve_path(value.text, cwd))
                if content is not None:
                    out.append(content)
            elif group == "api" and flag in ("-f", "-F", "--field", "--raw-field"):
                key, _, val = value.text.partition("=")
                if key == "body":
                    out.append(val)
            elif flag in _GH_BODY_FLAGS or flag in _GH_TEXT_FLAGS:
                out.append(value.text)
        piped = _known_output(seg.piped_from, cwd)
        if piped:
            out.append(piped)
        if out:
            return "\n".join(out)
    return None


def check_egress_leaks(cmd: str, leak_patterns: list[str]) -> bool | str:
    """Egress-only view of Gate B: True on a leak, "UNSCANNABLE" for hidden content."""
    if not cmd or not leak_patterns:
        return False
    ctx = _GateContext(
        protected=_ProtectedPaths(frozenset(), frozenset(), frozenset(), frozenset(), None),
        patterns=compile_leak_patterns(leak_patterns, include_builtin=False),
    )
    res = _check_command_text(cmd, ctx, os.getcwd())
    if not res:
        return False
    if res["reason"] == REASON_UNSCANNABLE:
        return "UNSCANNABLE"
    return res["reason"] == REASON_EGRESS_LEAK


def evaluate_gate_b(
    tool_name: str,
    args: dict[str, Any],
    spec_file_path: str,
    protected_files: list[str],
    config: dict[str, Any] | None = None,
    cwd: str | None = None,
    conversation_id: str = "",
) -> dict[str, str] | None:
    """Gate B: Command Execution Protection."""
    if tool_name and tool_name != "run_command":
        return None

    cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip()
    if not cmd:
        return {"decision": "allow"}

    command_cwd = str(args.get("Cwd") or args.get("cwd") or cwd or os.getcwd())
    ctx = _build_context(
        config,
        spec_file_path,
        protected_files,
        command_cwd,
        conversation_id=conversation_id,
    )
    return _check_command_text(cmd, ctx, command_cwd) or {"decision": "allow"}


def evaluate_pretool_call(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """Evaluates PreToolUse payload against Gates A, B, and C."""
    if config and config.get("_config_error"):
        return {
            "decision": "deny",
            "reason": f"Configuration error (failing closed): {config['_config_error']}",
        }

    # Ensure SCANNER_PROTECTED_FILES matches the loaded config
    global SCANNER_PROTECTED_FILES
    loaded_from = config.get("_loaded_from") if config else None
    if loaded_from:
        SCANNER_PROTECTED_FILES = _derive_workspace_paths(Path(loaded_from))

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
    conversation_id = str(
        payload.get("conversationId") or payload.get("conversation_id") or ""
    ).strip()

    if tool_name in ("browser_subagent", "search_web"):
        if _is_autonomous_session(conversation_id):
            return {
                "decision": "deny",
                "reason": "External web browsing and search tools are disabled in autonomous bug-fixer sessions to prevent data exfiltration.",
            }
        return {"decision": "allow"}

    workspace_paths = (
        payload.get("workspacePaths") if isinstance(payload.get("workspacePaths"), list) else []
    )

    decision_a = evaluate_gate_a(tool_name, args, spec_file_path, protected_files, repo_dir, workspace_paths)
    if decision_a is not None:
        return decision_a
    command_cwd = str(
        args.get("Cwd")
        or args.get("cwd")
        or (workspace_paths[0] if workspace_paths else "")
        or repo_dir
        or _repo_root(config, None)
    )
    # Gate B always runs. Workflow-specific gh denials (raw reads,
    # merge, approve, reopen) are filtered inside _check_command_text
    # per-segment when unenrolled, so compound commands like
    # "gh issue view 1; echo > config.json" still block on the
    # dangerous segment.
    decision_b = evaluate_gate_b(
        tool_name,
        args,
        spec_file_path,
        protected_files,
        config,
        cwd=command_cwd,
        conversation_id=conversation_id,
    )
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


class ScanScope(Enum):
    STAGED = "staged"  # git diff --cached (pre-commit hook)
    TREE = "tree"  # git ls-files (full working tree)
    HISTORY = "history"  # git log (CI audit)


@dataclass(frozen=True)
class ScanViolation:
    path: str
    line: int
    rule: str
    redacted_match: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}] -> {self.redacted_match}"


@runtime_checkable
class ScanBackend(Protocol):
    name: str  # e.g. "regex", "betterleaks"
    version: str  # e.g. "builtin", "2.0.0"

    def scan(
        self,
        repo_dir: str,
        scope: ScanScope,
        exclude_paths: list[str] | None = None,
    ) -> list[ScanViolation]: ...

    def config_hash(self) -> str: ...  # SHA-256 of the active ruleset/config


class RegexBackend:
    name: str = "regex"
    version: str = "builtin"

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns: list[str] = list(patterns or [])
        self.scanned_count: int = 0
        self.excluded_count: int = 0

    def config_hash(self) -> str:
        valid_patterns = [p for p in self.patterns if isinstance(p, str)]
        content = "\n".join(sorted(valid_patterns))
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    DEFAULT_REGEX_EXCLUDE_PATHS = [
        "tests/",
        ".agents/skills/principal-architect-workspace/",
    ]

    def scan(
        self,
        repo_dir: str,
        scope: ScanScope,
        exclude_paths: list[str] | None = None,
    ) -> list[ScanViolation]:
        if not exclude_paths:
            exclude_paths = self.DEFAULT_REGEX_EXCLUDE_PATHS
        else:
            exclude_paths = [p for p in exclude_paths if p and p != "/"]
        if isinstance(scope, str):
            scope = ScanScope(scope)

        self.scanned_count = 0
        self.excluded_count = 0
        violations: list[ScanViolation] = []

        compiled: list[re.Pattern[str]] = []
        for pat in self.patterns:
            if pat and isinstance(pat, str):
                try:
                    compiled.append(re.compile(pat))
                except re.error as exc:
                    sys.stderr.write(f"WARNING: Invalid leak pattern regex '{pat}': {exc}\n")

        if scope == ScanScope.HISTORY:
            raise NotImplementedError("RegexBackend does not support history scanning")
        elif scope not in (ScanScope.TREE, ScanScope.STAGED):
            raise ValueError(f"Unknown scan scope: {scope}")

        if not compiled:
            return []

        if scope == ScanScope.TREE:
            raw_files = subprocess.check_output(
                ["git", "ls-files"], cwd=repo_dir, stderr=subprocess.DEVNULL, text=True
            ).splitlines()
        else:
            raw_files = subprocess.check_output(
                ["git", "diff", "--cached", "--name-only"],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
            ).splitlines()

        files = sorted(set(raw_files))

        for rel_path in files:
            if not rel_path or rel_path.startswith(".git/") or rel_path.startswith("_agents/"):
                continue

            if exclude_paths and any(rel_path.startswith(ep) for ep in exclude_paths):
                self.excluded_count += 1
                continue
            full_path = os.path.join(repo_dir, rel_path)
            if not os.path.isfile(full_path):
                continue
            self.scanned_count += 1
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, start=1):
                        for pat in compiled:
                            match = pat.search(line)
                            if match:
                                matched_text = match.group()
                                redacted = (
                                    matched_text[:4] + "…" if len(matched_text) > 4 else "****"
                                )
                                violations.append(
                                    ScanViolation(
                                        path=rel_path,
                                        line=lineno,
                                        rule=pat.pattern,
                                        redacted_match=redacted,
                                    )
                                )
            except Exception:
                continue

        return violations


class BetterleaksBackend:
    name: str = "betterleaks"

    def __init__(
        self,
        config_path: str | None = None,
        expected_version: str | None = None,
        expected_checksum: str | None = None,
    ) -> None:
        self.config_path = config_path
        self.expected_version = expected_version
        self.expected_checksum = expected_checksum
        self._version: str | None = None
        self.scanned_count: int = 0
        self.excluded_count: int = 0
        self._binary_path = shutil.which("betterleaks") or "betterleaks"
        self._verify_binary()

    def _verify_binary(self) -> None:
        """Verify betterleaks binary version and checksum if configured.

        Version pinning prevents silent upgrades that could change scanning
        behaviour. Checksum verification prevents supply-chain attacks where
        the binary is replaced with a trojan that always returns clean.
        """
        if self.expected_version:
            actual = self.version
            if actual == "unknown":
                raise RuntimeError(
                    "Betterleaks version check failed: could not determine "
                    "installed version. Binary may be missing or broken."
                )
            if actual != self.expected_version:
                raise RuntimeError(
                    f"Betterleaks version mismatch: expected {self.expected_version}, "
                    f"got {actual}. Update scanning.secrets.betterleaks.version in "
                    f"config to accept the new version after verification."
                )
        if self.expected_checksum:
            if self._binary_path == "betterleaks":
                raise RuntimeError("Betterleaks binary not found for checksum verification")
            actual_hash = hashlib.sha256(Path(self._binary_path).read_bytes()).hexdigest()
            if actual_hash != self.expected_checksum:
                raise RuntimeError(
                    f"Betterleaks checksum mismatch: expected {self.expected_checksum[:16]}…, "
                    f"got {actual_hash[:16]}…. Binary may have been tampered with. "
                    f"Update scanning.secrets.betterleaks.sha256 in config after "
                    f"verifying the new binary."
                )

    @property
    def version(self) -> str:
        if self._version is not None:
            return self._version
        try:
            out = subprocess.check_output(
                [self._binary_path, "version"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            self._version = out
        except Exception:
            self._version = "unknown"
        return self._version

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        keys_to_remove = [k for k in env if k.startswith("BETTERLEAKS_")]
        for k in keys_to_remove:
            env.pop(k)
        return env

    def _build_cmd(self, repo_dir: str, scope: ScanScope) -> list[str]:
        cmd = [self._binary_path]
        if scope == ScanScope.STAGED:
            cmd.extend(["git", "--staged", repo_dir])
        elif scope == ScanScope.TREE:
            cmd.extend(["dir", repo_dir])
        elif scope == ScanScope.HISTORY:
            cmd.extend(["git", repo_dir])
        else:
            raise ValueError(f"Unknown scope: {scope}")

        cmd.extend(
            [
                "--validation=false",
                "--redact",
                "--no-banner",
                "-f",
                "json",
                "-r",
                "-",
                "--exit-code",
                "1",
                "-i",
                "/dev/null",
            ]
        )
        if self.config_path:
            cmd.extend(["--config", self.config_path])
        return cmd

    def scan(
        self,
        repo_dir: str,
        scope: ScanScope,
        exclude_paths: list[str] | None = None,
    ) -> list[ScanViolation]:
        # Betterleaks has its own ignore mechanism and had 0 false positives
        # on this repo — do NOT apply DEFAULT_REGEX_EXCLUDE_PATHS here.
        if exclude_paths:
            exclude_paths = [p for p in exclude_paths if p and p != "/"]
        else:
            exclude_paths = []
        if isinstance(scope, str):
            scope = ScanScope(scope)

        self.scanned_count = 0
        self.excluded_count = 0
        cmd = self._build_cmd(repo_dir, scope)
        env = self._build_env()

        try:
            out = subprocess.check_output(cmd, env=env, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                out = e.stdout
            else:
                raise RuntimeError(f"Betterleaks failed: {e.stderr}")

        if not out.strip():
            # No output means clean scan — return empty violations
            self.last_config_hash = None
            return []

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            raise RuntimeError("Betterleaks produced unparseable output — failing closed")

        # Support both v1 (flat array) and v2 (envelope dict) output formats
        if isinstance(data, list):
            # v1 / gitleaks-compat format: flat list of findings
            findings = data
            self.last_config_hash = None
        elif isinstance(data, dict) and "findings" in data:
            # v2 envelope format: {"findings": [...], "scan": {...}}
            scan_state = data.get("scan", {}).get("state", "")
            if scan_state and scan_state != "complete":
                raise RuntimeError(f"Betterleaks scan did not complete (state={scan_state})")
            self.last_config_hash = data.get("scan", {}).get("config_hash")
            findings = data.get("findings", [])
        else:
            raise RuntimeError("Betterleaks output missing expected structure")

        if exclude_paths:
            exclude_paths = [p for p in exclude_paths if p and p != "/"]

        violations = []
        for finding in findings:
            # Support both v1 and v2 field names
            # v1: File, StartLine, RuleID, Match
            # v2: location.path, location.start_line, rule_id, match.value
            if "location" in finding:
                path = finding["location"].get("path", "")
                line = finding["location"].get("start_line", 0)
            else:
                path = finding.get("File", "")
                line = finding.get("StartLine", 0)

            rule = finding.get("rule_id") or finding.get("RuleID", "unknown")

            if isinstance(finding.get("match"), dict):
                redacted_match = finding["match"].get("value", "REDACTED")
            else:
                redacted_match = finding.get("Match", "REDACTED")

            if not path or path.startswith(".git/") or path.startswith("_agents/"):
                continue

            # Betterleaks dir scans return absolute paths; make them relative
            if os.path.isabs(path):
                try:
                    path = os.path.relpath(path, repo_dir)
                except ValueError:
                    pass

            if exclude_paths and any(path.startswith(ep) for ep in exclude_paths):
                self.excluded_count += 1
                continue

            violations.append(
                ScanViolation(
                    path=path,
                    line=line,
                    rule=rule,
                    redacted_match=redacted_match,
                )
            )

        return violations

    def config_hash(self) -> str:
        if getattr(self, "last_config_hash", None):
            return self.last_config_hash
        if self.config_path and os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "rb") as f:
                    return hashlib.sha256(f.read()).hexdigest()
            except Exception:
                pass
        try:
            out = subprocess.check_output(
                [self._binary_path, "config", "hash"], stderr=subprocess.DEVNULL, text=True
            ).strip()
            if out:
                return out
        except Exception:
            pass
        return hashlib.sha256(b"default-embedded-ruleset").hexdigest()


def resolve_backend(
    config: dict[str, Any] | None = None,
    patterns: list[str] | None = None,
) -> ScanBackend:
    """Resolves and instantiates the configured ScanBackend."""
    if config is None:
        config = {}
    # Normalise so v1 configs get scanning.secrets.backend defaulted to "regex"
    config = normalise_config(config)

    scanning_cfg = config.get("scanning") if isinstance(config.get("scanning"), dict) else {}
    secrets_cfg = (
        scanning_cfg.get("secrets") if isinstance(scanning_cfg.get("secrets"), dict) else {}
    )

    backend_name = secrets_cfg.get("backend", None)

    if patterns is None:
        patterns = secrets_cfg.get("patterns")
        if patterns is None:
            patterns = config.get("leak_patterns", [])
    patterns = list(patterns or [])

    if backend_name is None:
        try:
            subprocess.run(
                ["betterleaks", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            backend_name = "betterleaks"
        except (subprocess.CalledProcessError, FileNotFoundError):
            backend_name = "regex"

    if backend_name == "betterleaks":
        try:
            subprocess.run(
                ["betterleaks", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            betterleaks_cfg = (
                secrets_cfg.get("betterleaks")
                if isinstance(secrets_cfg.get("betterleaks"), dict)
                else {}
            )
            config_path = (
                betterleaks_cfg.get("config_path")
                or secrets_cfg.get("config_path")
                or secrets_cfg.get("config")
            )
            return BetterleaksBackend(
                config_path=config_path,
                expected_version=betterleaks_cfg.get("version"),
                expected_checksum=betterleaks_cfg.get("sha256"),
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            sys.stderr.write("WARNING: betterleaks not found, falling back to regex (degraded)\n")
            return RegexBackend(patterns)

    if backend_name == "regex":
        return RegexBackend(patterns)

    sys.stderr.write(
        f"WARNING: Unknown scan backend '{backend_name}', falling back to regex (degraded)\n"
    )
    return RegexBackend(patterns)


def scan_repository(
    repo_dir: str,
    patterns: list[str],
    exclude_paths: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[list[str], int, int]:
    """Scans repository tracked and staged files for leak pattern violations."""
    backend = resolve_backend(config, patterns=patterns)
    violations = backend.scan(repo_dir, ScanScope.TREE, exclude_paths=exclude_paths)
    scanned_count = getattr(backend, "scanned_count", 0)
    excluded_count = getattr(backend, "excluded_count", 0)
    return [str(v) for v in violations], scanned_count, excluded_count


def stamp_receipt(
    repo_dir: str,
    status: str = "PASS",
    backend: ScanBackend | None = None,
) -> int:
    """Stamps leak audit receipt into .git/leak_audit_receipt.json."""
    tree_sha = get_tree_sha(repo_dir)
    if not tree_sha:
        sys.stderr.write("ERROR: Could not compute git write-tree SHA.\n")
        return 1

    if backend is None:
        backend = resolve_backend()

    git_dir = get_git_dir(repo_dir)
    receipt_path = os.path.join(git_dir, "leak_audit_receipt.json")
    payload = {
        "status": status,
        "tree_sha": tree_sha,
        "timestamp": datetime.now(UTC).isoformat(),
        "backend": backend.name,
        "backend_version": backend.version,
        "config_hash": backend.config_hash(),
        "validate": False,
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
            sys.stderr.write(
                f"Receipt verification failed: status is '{status}', expected 'PASS'\n"
            )
            return 1
        current_sha = get_tree_sha(repo_dir)
        receipt_sha = data.get("tree_sha")
        if not current_sha:
            sys.stderr.write("ERROR: Could not compute current git write-tree SHA.\n")
            return 1
        if receipt_sha != current_sha:
            sys.stderr.write(
                f"Receipt verification failed: receipt tree_sha '{receipt_sha}' "
                f"!= current tree_sha '{current_sha}'\n"
            )
            return 1
        print(f"Receipt verified (status=PASS, tree_sha={current_sha})")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Receipt verification failed: {exc}\n")
        return 1


def _scan_with_builtin_floor(
    repo_dir: str,
    scope: ScanScope,
    exclude_paths: list[str] | None = None,
    backend: ScanBackend | None = None,
    config: dict[str, Any] | None = None,
    patterns: list[str] | None = None,
) -> list[ScanViolation]:
    """Run configured ScanBackend plus BUILTIN_LEAK_PATTERNS floor, deduplicating violations."""
    if backend is None:
        backend = resolve_backend(config, patterns=patterns)
    violations = list(backend.scan(repo_dir, scope, exclude_paths=exclude_paths))
    builtin_violations = RegexBackend(list(BUILTIN_LEAK_PATTERNS)).scan(
        repo_dir, scope, exclude_paths=exclude_paths
    )
    for v in builtin_violations:
        if v not in violations:
            violations.append(v)
    return violations


def run_git_hook(
    repo_dir: str,
    patterns: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    backend: ScanBackend | None = None,
    config: dict[str, Any] | None = None,
) -> int:
    """Executes pre-commit / pre-push git hook validation."""
    staged_files: list[str] = []
    try:
        staged_files = [
            line
            for line in subprocess.check_output(
                ["git", "diff", "--cached", "--name-only"],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
            ).splitlines()
            if line.strip()
        ]
    except Exception:
        staged_files = []
    scope = ScanScope.STAGED if staged_files else ScanScope.TREE
    violations = _scan_with_builtin_floor(
        repo_dir,
        scope,
        exclude_paths=exclude_paths,
        backend=backend,
        config=config,
        patterns=patterns,
    )
    if violations:
        sys.stderr.write(
            f"\n[Git Hook Blocked] Leak pattern violation(s) detected ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations[:20])
            + "\n"
        )
        return 1
    if not staged_files:
        return 0
    return verify_receipt(repo_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PreToolUse Gate & Security Boundary Enforcement for Bug Fixer Loop."
    )
    parser.add_argument("--config", type=str, help="Path to config.json")
    parser.add_argument("--repo", type=str, help="Path to target git repository directory")
    parser.add_argument(
        "--scan-only", nargs="?", const=".", type=str, help="Scan repository for leak patterns"
    )
    parser.add_argument(
        "--stamp-receipt", action="store_true", help="Stamp leak audit pass receipt into .git"
    )
    parser.add_argument("--stamp-pass", action="store_true", help="Alias for --stamp-receipt")
    parser.add_argument(
        "--verify-receipt",
        action="store_true",
        help="Verify leak audit receipt matches staged tree",
    )
    parser.add_argument(
        "--git-hook", action="store_true", help="Run as git pre-commit/pre-push hook"
    )
    parser.add_argument(
        "--exclude-pattern",
        action="append",
        default=[],
        help="Additional paths to exclude from leak scan",
    )

    args = parser.parse_args()

    stdin_data = {}
    workspace_paths = []
    _non_hook_modes = any([
        getattr(args, 'git_hook', False),
        getattr(args, 'scan_only', False),
        getattr(args, 'stamp_receipt', False),
        getattr(args, 'stamp_pass', False),
        getattr(args, 'verify_receipt', False),
    ])
    
    if not _non_hook_modes:
        if not sys.stdin.isatty():
            raw_str = sys.stdin.read()
            if not raw_str.strip():
                print(
                    json.dumps(
                        {
                            "decision": "deny",
                            "reason": "Empty hook payload on stdin — failing closed.",
                        }
                    )
                )
                return 0
            try:
                raw = json.loads(raw_str)
                if isinstance(raw, dict):
                    stdin_data = raw
            except (json.JSONDecodeError, ValueError):
                print(json.dumps({"decision": "deny", "reason": "Invalid JSON payload on stdin"}))
                return 0

    workspace_paths = stdin_data.get("workspacePaths", [])

    try:
        config = load_config(args.config, workspace_paths=workspace_paths)
        # Re-derive protected paths from the config that was loaded
        global SCANNER_PROTECTED_FILES
        config_loaded_from = config.get("_loaded_from")
        if config_loaded_from:
            SCANNER_PROTECTED_FILES = _derive_workspace_paths(Path(config_loaded_from))
    except Exception as exc:
        sys.stderr.write(f"Config loading error: {exc} — failing closed\n")
        config = {
            "_config_error": str(exc),
            "spec_file_path": "docs/plugin-architecture.md",
            "protected_files": ["docs/plugin-architecture.md"],
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
        backend = resolve_backend(config, patterns=patterns)
        violations = backend.scan(target_dir, ScanScope.TREE, exclude_paths=exclude_paths)
        scanned_count = getattr(backend, "scanned_count", 0)
        excluded_count = getattr(backend, "excluded_count", 0)
        if violations:
            print(f"LEAK SCAN FAILED ({len(violations)} violation(s)):")
            for v in violations:
                print(f"  - {v}")
            return 1
        if backend.name == "regex":
            backend_str = "regex (degraded)"
        else:
            ver = backend.version
            if ver.startswith(backend.name):
                backend_str = ver
            else:
                backend_str = (
                    f"{backend.name} {ver}" if ver.startswith("v") else f"{backend.name} v{ver}"
                )
        print(
            f"LEAK SCAN CLEAN (repo={target_dir}, backend={backend_str}, "
            f"{scanned_count} files scanned, {excluded_count} excluded)"
        )
        return 0

    if args.stamp_receipt or args.stamp_pass:
        backend = resolve_backend(config)
        return stamp_receipt(repo_dir, "PASS", backend=backend)

    if args.verify_receipt:
        return verify_receipt(repo_dir)

    if args.git_hook:
        patterns = list(config.get("leak_patterns", []))
        exclude_paths = list(config.get("scan_exclude_paths", ["tests/"]))
        if args.exclude_pattern:
            exclude_paths.extend(args.exclude_pattern)
        backend = resolve_backend(config, patterns=patterns)
        return run_git_hook(
            repo_dir, patterns=patterns, exclude_paths=exclude_paths, backend=backend, config=config
        )

    if not stdin_data:
        if sys.stdin.isatty():
            parser.print_help()
            return 0
        else:
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": "Empty hook payload on stdin — failing closed.",
                    }
                )
            )
            return 0

    result = evaluate_pretool_call(stdin_data, config)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
