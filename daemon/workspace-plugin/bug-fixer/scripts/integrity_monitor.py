#!/usr/bin/env python3
"""PostToolUse integrity monitor for protected files.

After every run_command, verifies that protected files haven't been
modified. If tampering is detected, reverts the file from git and
logs the violation.

This closes the 'python3 /tmp/script.py' bypass where an external
script modifies protected files without the filename appearing in
the command text.

The PreToolUse gate checks command TEXT for protected filenames.
This PostToolUse hook checks actual FILE CHECKSUMS after execution.
Together they form defense in depth:
  - PreToolUse: blocks obvious direct writes (fast, cheap)
  - PostToolUse: catches indirect/evasion writes (always correct)

Usage:
  Pre-hook:   python3 integrity_monitor.py --pre
  Post-hook:  python3 integrity_monitor.py --post
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

try:
    import security_audit
    import shell_analysis
    from bugfixer_api import BugFixer
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import security_audit
    import shell_analysis
    from bugfixer_api import BugFixer


# Files that manage.py is allowed to mutate.  Security scripts, hooks,
# manifests are NEVER in this set — only operational config/sidecar files.
MANAGEMENT_MUTABLE_BASENAMES = frozenset({
    "sidecar.json",
    "config.json",
})

# Basename of the trusted management CLI.
_TRUSTED_MANAGER = "manage.py"

# Subcommands of manage.py that justify management-tier file changes.
_MUTABLE_SUBCOMMANDS = frozenset({
    "enroll", "unenroll", "config", "reset",
    "labels", "refresh-sidecars", "doctor",
})

# Subcommands of manage.py that justify staging/unstaging consumer plugin files.
_STAGED_MUTABLE_SUBCOMMANDS = frozenset({
    "enroll", "unenroll", "refresh-sidecars", "doctor",
})

# Lifecycle shell scripts under <plugin-repo>/scripts/ allowed to update
# daemon_root and consumer staged workspace plugins in interactive sessions.
_LIFECYCLE_SCRIPT_NAMES = frozenset({
    "install.sh", "uninstall.sh", "enroll.sh", "unenroll.sh",
})

_SHELL_RUNNERS = frozenset({"bash", "sh", "zsh"})

_HOST_SECURITY_SCRIPTS = (
    "pre_tool_gate.py",
    "safe_ingest.py",
    "safe_post.py",
    "injection_scanner.py",
    "integrity_monitor.py",
    "shell_analysis.py",
    "security_audit.py",
    "github_url_gate.py",
)

_PYTHON_INTERP_RE = re.compile(r"^python3?(?:\.\d+)?$")

# Interpreter flags that do not alter the executed script path or inject code.
_SAFE_PYTHON_FLAGS = frozenset({"-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-I"})


def _find_daemon_root() -> Path:
    """Locate the daemon plugin root containing scripts/manage.py."""
    script_dir = Path(__file__).resolve().parent
    candidate = script_dir.parent.parent.parent
    for d in (candidate, *script_dir.parents):
        if (d / "scripts" / "manage.py").is_file():
            return d
        manifest = d / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("name") == "bug-fixer-daemon":
                    return d
            except (json.JSONDecodeError, OSError):
                pass
    global_daemon = (
        Path.home() / ".gemini" / "config" / "plugins" / "bug-fixer-daemon"
    )
    if (global_daemon / "scripts" / "manage.py").is_file() or (
        global_daemon / "plugin.json"
    ).is_file():
        return global_daemon
    return candidate


def _canonical_manage_paths() -> set[Path]:
    """Return canonical resolved paths for the daemon's manage.py."""
    paths = {(_find_daemon_root() / "scripts" / "manage.py").resolve()}
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


def _is_autonomous_session(conversation_id: str) -> bool:
    """Return True when executing inside an autonomous sidecar or dispatched session."""
    if "sidecar" in os.environ.get("ANTIGRAVITY_SOURCE_METADATA", "").lower():
        return True
    if not conversation_id:
        return False
    return BugFixer.get_session_context(conversation_id).is_autonomous


def _command_cwd(stdin_data: dict) -> Path | None:
    tool_call = stdin_data.get("toolCall", {})
    args = (tool_call.get("args") or {}) if isinstance(tool_call, dict) else {}
    raw_cwd = str(args.get("Cwd") or args.get("cwd") or "").strip()
    if not raw_cwd:
        wps = stdin_data.get("workspacePaths")
        if isinstance(wps, list) and wps:
            raw_cwd = str(wps[0] or "").strip()
    if not raw_cwd:
        raw_cwd = os.getcwd()
    try:
        return Path(raw_cwd).expanduser().resolve()
    except OSError:
        return None


def _resolve_cli_path(raw: str, cmd_cwd: Path | None) -> Path | None:
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if cmd_cwd is None:
            return None
        p = cmd_cwd / p
    try:
        return p.resolve()
    except OSError:
        return None


def _is_canonical_plugin_repo_root(repo_root: Path) -> bool:
    """Return True if repo_root contains canonical bug-fixer daemon and workspace plugin."""
    manifest = repo_root / "daemon" / "plugin.json"
    manage_py = repo_root / "daemon" / "scripts" / "manage.py"
    ws_manifest = repo_root / "daemon" / "workspace-plugin" / "bug-fixer" / "plugin.json"
    if not (manifest.is_file() and manage_py.is_file() and ws_manifest.is_file()):
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data.get("name") == "bug-fixer-daemon"
    except (json.JSONDecodeError, OSError):
        return False


def _parse_single_literal_segment(stdin_data: dict) -> list[str] | None:
    """Parse stdin toolCall CommandLine if it is a single literal shell command."""
    tool_call = stdin_data.get("toolCall", {})
    if not isinstance(tool_call, dict) or tool_call.get("name") != "run_command":
        return None
    cmd = str((tool_call.get("args") or {}).get("CommandLine") or "").strip()
    if not cmd:
        return None
    try:
        parsed = shell_analysis.parse(cmd)
    except shell_analysis.ShellParseError:
        return None
    if parsed.substitutions:
        return None
    active_segments = [
        s for s in parsed.segments
        if s.words or s.redirects_out or s.redirects_in or s.assignments
    ]
    if len(active_segments) != 1:
        return None
    seg = active_segments[0]
    if seg.assignments or seg.redirects_out or seg.piped_from is not None:
        return None
    argv = seg.argv
    if not argv or any(not w.literal or w.globbed for w in argv):
        return None
    if argv[0].text == "env" or Path(argv[0].text).name == "env":
        return None
    return [w.text for w in argv]


def _trusted_management_subcommand(stdin_data: dict, conversation_id: str = "") -> str | None:
    """Return the trusted mutating manage.py subcommand name, or None."""
    if _is_autonomous_session(conversation_id):
        return None
    tool_call = stdin_data.get("toolCall", {})
    if not isinstance(tool_call, dict):
        return None
    cmd = str((tool_call.get("args") or {}).get("CommandLine") or "").strip()
    if _TRUSTED_MANAGER not in cmd:
        return None
    tokens = _parse_single_literal_segment(stdin_data)
    if not tokens:
        return None

    idx = 0
    if _PYTHON_INTERP_RE.match(Path(tokens[idx]).name):
        idx += 1
        while idx < len(tokens) and tokens[idx].startswith("-"):
            if tokens[idx] not in _SAFE_PYTHON_FLAGS:
                return None
            idx += 1
    if idx >= len(tokens):
        return None

    script_path = _resolve_cli_path(tokens[idx], _command_cwd(stdin_data))
    if script_path is None or script_path not in _canonical_manage_paths():
        return None
    idx += 1

    if idx >= len(tokens) or tokens[idx] not in _MUTABLE_SUBCOMMANDS:
        return None

    subcommand = tokens[idx]
    rest_args = tokens[idx + 1 :]
    if subcommand == "config" and not any(
        a == "--set" or a.startswith("--set=") for a in rest_args
    ):
        return None
    if subcommand == "doctor" and "--fix" not in rest_args:
        return None
    return subcommand


def _was_trusted_management_command(stdin_data: dict, conversation_id: str = "") -> bool:
    """True when the just-executed command was a single trusted manage.py mutation.

    Uses structural shell parsing (shell_analysis.parse) so comments, compound
    commands (&&, ;, |), command substitutions, environment variable overrides,
    non-canonical script paths, or inline interpreter flags (-c) cannot spoof
    a manage.py invocation.
    """
    return _trusted_management_subcommand(stdin_data, conversation_id) is not None


def _was_trusted_lifecycle_script(stdin_data: dict, conversation_id: str = "") -> bool:
    """True when the just-executed command was an interactive canonical lifecycle shell script."""
    if _is_autonomous_session(conversation_id):
        return False
    tokens = _parse_single_literal_segment(stdin_data)
    if not tokens:
        return False
    idx = 0
    if Path(tokens[idx]).name in _SHELL_RUNNERS:
        idx += 1
        if idx >= len(tokens) or tokens[idx].startswith("-"):
            return False
    script_path = _resolve_cli_path(tokens[idx], _command_cwd(stdin_data))
    if script_path is None or script_path.name not in _LIFECYCLE_SCRIPT_NAMES:
        return False
    if script_path.parent.name != "scripts" or not script_path.is_file():
        return False
    return _is_canonical_plugin_repo_root(script_path.parent.parent)


def _get_snapshot_file(conversation_id: str = "") -> Path:
    """Snapshot file lives alongside the config in the sidecar dir, scoped by conversation."""
    suffix = f".{conversation_id}" if conversation_id else ""
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / f".integrity_snapshot{suffix}.json"
    return Path(tempfile.gettempdir()) / f".bug_fixer_integrity_snapshot{suffix}.json"


def _get_snapshot_dir(conversation_id: str = "") -> Path:
    """Directory where copies of protected files are saved for reversion."""
    suffix = f".{conversation_id}" if conversation_id else ""
    return _get_snapshot_file(conversation_id).parent / f".integrity_snapshot{suffix}"


# Module-level alias for backward compatibility (no conversation scoping)
SNAPSHOT_FILE = _get_snapshot_file("")


def _add_if_exists(files: list[str], path: Path) -> None:
    if path.exists() and str(path) not in files:
        files.append(str(path))


def _get_protected_files() -> list[str]:
    """Return absolute paths of files to monitor for integrity."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent

    files: list[str] = []
    repo_dir: Path | None = None

    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        cfg_file = Path(config_env).resolve()
        sidecar_dir = cfg_file.parent
        for name in [
            "config.json",
            "injection_cache.json",
            "security_refusals.jsonl",
            "sidecar.json",
        ]:
            _add_if_exists(files, sidecar_dir / name)
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            if cfg.get("repo_dir"):
                repo_dir = Path(cfg["repo_dir"]).expanduser()
        except (json.JSONDecodeError, OSError):
            pass

    if repo_dir is None:
        repo_dir = Path.cwd()

    # Global daemon files and canonical workspace-plugin source files
    daemon_root = _find_daemon_root()
    for d in [plugin_dir, plugin_dir.parent, daemon_root]:
        for name in ["hooks.json", "plugin.json"]:
            _add_if_exists(files, d / name)
    for name in ["manage.py", "tick.py"]:
        _add_if_exists(files, daemon_root / "scripts" / name)
    ws_plugin = daemon_root / "workspace-plugin" / "bug-fixer"
    for name in ["hooks.json", "plugin.json"]:
        _add_if_exists(files, ws_plugin / name)
    for name in _HOST_SECURITY_SCRIPTS:
        _add_if_exists(files, ws_plugin / "scripts" / name)

    # Consumer .agents/plugins.json and staged .agents/plugins/bug-fixer/
    _add_if_exists(files, repo_dir / ".agents" / "plugins.json")
    staged_dir = repo_dir / ".agents" / "plugins" / "bug-fixer"
    for name in ["hooks.json", "plugin.json"]:
        _add_if_exists(files, staged_dir / name)
    for name in _HOST_SECURITY_SCRIPTS:
        _add_if_exists(files, staged_dir / "scripts" / name)

    # Scanner config files at repo root or plugin dir
    for d in [plugin_dir, plugin_dir.parent, repo_dir]:
        for name in [".betterleaks.toml", ".betterleaksignore"]:
            _add_if_exists(files, d / name)

    # Active host security scripts themselves
    for name in _HOST_SECURITY_SCRIPTS:
        _add_if_exists(files, script_dir / name)

    return files


def _sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return ""


def snapshot(conversation_id: str = "") -> None:
    """Take a snapshot of all protected file checksums (called by --pre)."""
    snapshot_file = _get_snapshot_file(conversation_id)
    snapshot_dir = _get_snapshot_dir(conversation_id)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    checksums = {}
    for path in _get_protected_files():
        h = _sha256(path)
        if h:
            checksums[path] = h
            backup_name = hashlib.sha256(path.encode("utf-8")).hexdigest()
            try:
                shutil.copy2(path, snapshot_dir / backup_name)
            except OSError:
                pass

    with open(snapshot_file, "w") as f:
        json.dump(checksums, f)


def _has_staged_components(path_obj: Path) -> bool:
    parts = path_obj.parts
    if len(parts) >= 2 and parts[-2:] == (".agents", "plugins.json"):
        return True
    for i in range(len(parts) - 2):
        if parts[i : i + 3] == (".agents", "plugins", "bug-fixer"):
            return True
    return False


def _is_staged_or_plugins_json(path_str: str) -> bool:
    abs_path = Path(os.path.abspath(path_str))
    if _has_staged_components(abs_path):
        return True
    try:
        return _has_staged_components(abs_path.resolve())
    except OSError:
        return False


def _is_under_daemon_roots(path_str: str) -> bool:
    roots = [
        _find_daemon_root(),
        Path.home() / ".gemini" / "config" / "plugins" / "bug-fixer-daemon",
    ]
    raw_target = Path(os.path.abspath(path_str))
    try:
        resolved_target = raw_target.resolve()
    except OSError:
        resolved_target = raw_target
    for root in roots:
        for candidate_root in (root, root.resolve()):
            if raw_target.is_relative_to(candidate_root) or resolved_target.is_relative_to(
                candidate_root
            ):
                return True
    return False


def _accept_modified_file(
    path: str, actual_hash: str, expected: dict, snapshot_dir: Path
) -> None:
    backup_name = hashlib.sha256(path.encode("utf-8")).hexdigest()
    backup_path = snapshot_dir / backup_name
    if Path(path).is_file():
        expected[path] = actual_hash
        if backup_path.is_file():
            try:
                shutil.copy2(path, backup_path)
            except OSError:
                pass
    else:
        backup_path.unlink(missing_ok=True)


def verify(conversation_id: str = "", stdin_data: dict | None = None) -> None:
    """Verify protected files against snapshot, revert if tampered (called by --post)."""
    snapshot_file = _get_snapshot_file(conversation_id)
    snapshot_dir = _get_snapshot_dir(conversation_id)
    if not snapshot_file.exists():
        return

    try:
        with open(snapshot_file) as f:
            expected = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    trusted_subcmd = (
        _trusted_management_subcommand(stdin_data, conversation_id)
        if stdin_data is not None
        else None
    )
    trusted_lifecycle = (
        stdin_data is not None
        and _was_trusted_lifecycle_script(stdin_data, conversation_id)
    )

    for path, expected_hash in expected.items():
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            # Check if this was a legitimate management mutation
            basename = os.path.basename(path)

            if basename == "security_refusals.jsonl":
                # Check if it's a valid hash-chained append
                try:
                    backup_name = hashlib.sha256(path.encode("utf-8")).hexdigest()
                    backup_path = snapshot_dir / backup_name
                    if security_audit.verify_log_chain(Path(path), backup_path):
                        expected[path] = actual_hash
                        if backup_path.is_file():
                            shutil.copy2(path, backup_path)
                        sys.stderr.write(
                            f"INTEGRITY: {path} valid hash-chained append — accepted.\n"
                        )
                        continue
                except Exception as e:
                    sys.stderr.write(f"INTEGRITY: failed to verify {path} chain: {e}\n")

            if basename in MANAGEMENT_MUTABLE_BASENAMES and (
                trusted_subcmd is not None or trusted_lifecycle
            ):
                _accept_modified_file(path, actual_hash, expected, snapshot_dir)
                sys.stderr.write(
                    f"INTEGRITY: {path} updated by trusted management command — accepted.\n"
                )
                continue

            if _is_staged_or_plugins_json(path) and (
                trusted_subcmd in _STAGED_MUTABLE_SUBCOMMANDS or trusted_lifecycle
            ):
                _accept_modified_file(path, actual_hash, expected, snapshot_dir)
                sys.stderr.write(
                    f"INTEGRITY: {path} updated by trusted workspace staging — accepted.\n"
                )
                continue

            if _is_under_daemon_roots(path) and trusted_lifecycle:
                _accept_modified_file(path, actual_hash, expected, snapshot_dir)
                sys.stderr.write(
                    f"INTEGRITY: {path} updated by trusted lifecycle script — accepted.\n"
                )
                continue

            # TAMPERING DETECTED — revert from snapshot
            sys.stderr.write(
                f"INTEGRITY VIOLATION: {path} was modified by an "
                f"indirect write (bypassing PreToolUse gate). Reverting.\n"
            )
            backup_name = hashlib.sha256(path.encode("utf-8")).hexdigest()
            backup_path = snapshot_dir / backup_name
            if backup_path.is_file():
                try:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_path, path)
                    sys.stderr.write(f"REVERTED: {path}\n")
                except Exception as e:
                    sys.stderr.write(f"REVERT FAILED for {path}: {e}\n")
            else:
                sys.stderr.write(f"REVERT FAILED for {path}: snapshot backup not found\n")

    # Clean up snapshot
    try:
        snapshot_file.unlink(missing_ok=True)
    except OSError:
        pass
    if snapshot_dir.is_dir():
        try:
            shutil.rmtree(snapshot_dir)
        except OSError:
            pass


def _resolve_config_from_workspace(workspace_paths: list[str]) -> None:
    """Try to set BUG_FIXER_CONFIG from workspacePaths by scanning sidecars."""
    # Find daemon dir by walking up from this script
    script_dir = Path(__file__).resolve().parent
    daemon_dir = script_dir
    for _ in range(5):
        if (daemon_dir / "plugin.json").is_file():
            try:
                manifest = json.loads((daemon_dir / "plugin.json").read_text())
                if manifest.get("name") == "bug-fixer-daemon":
                    break
            except (json.JSONDecodeError, OSError):
                pass
        daemon_dir = daemon_dir.parent
    else:
        daemon_dir = Path.home() / ".gemini" / "config" / "plugins" / "bug-fixer-daemon"
    sidecars_dir = daemon_dir / "sidecars"
    if not sidecars_dir.is_dir():
        return
    for sidecar in sidecars_dir.iterdir():
        cfg_path = sidecar / "config.json"
        if not cfg_path.is_file():
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
            repo_dir = cfg.get("repo_dir", "")
            if not repo_dir:
                continue
            repo_resolved = Path(repo_dir).resolve()
            for wp in workspace_paths:
                if Path(wp).resolve().is_relative_to(repo_resolved):
                    os.environ["BUG_FIXER_CONFIG"] = str(cfg_path)
                    return
        except (json.JSONDecodeError, OSError):
            continue


def main() -> int:
    """Entry point — uses --pre or --post flag to determine mode."""
    # Parse stdin (hooks always send payload on stdin)
    stdin_data = {}
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                stdin_data = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # If BUG_FIXER_CONFIG isn't set, try to find it from workspacePaths
    if not os.environ.get("BUG_FIXER_CONFIG"):
        _resolve_config_from_workspace(stdin_data.get("workspacePaths", []))

    raw_cid = (
        str(stdin_data.get("conversationId") or stdin_data.get("conversation_id") or "")
        if isinstance(stdin_data, dict)
        else ""
    )
    # Sanitise to prevent path traversal — only allow UUID-like IDs
    conversation_id = raw_cid if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw_cid) else ""

    if "--post" in sys.argv:
        verify(conversation_id, stdin_data)
        # PostToolUse contract: return empty object
        print(json.dumps({}))
    else:
        # Default: --pre (snapshot)
        snapshot(conversation_id)
        # PreToolUse contract: return decision
        print(json.dumps({"decision": "allow"}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
