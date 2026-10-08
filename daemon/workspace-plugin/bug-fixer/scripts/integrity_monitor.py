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
import re
import json
import os
import subprocess
import shutil
import sys
try:
    import security_audit
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import security_audit
from pathlib import Path


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
    "labels", "refresh-sidecars",
})


def _was_trusted_management_command(stdin_data: dict) -> bool:
    """True when the just-executed command was a trusted manage.py mutation.

    Inspects the PostToolUse hook payload to determine whether the command
    that just ran was 'python3 .../manage.py <mutable-subcommand>'.
    """
    tool_call = stdin_data.get("toolCall", {})
    if not isinstance(tool_call, dict):
        return False
    if tool_call.get("name") != "run_command":
        return False
    cmd = str((tool_call.get("args") or {}).get("CommandLine") or "").strip()
    if _TRUSTED_MANAGER not in cmd:
        return False
    # Tokenise and check: must contain manage.py and a mutable subcommand
    parts = cmd.split()
    has_manager = any(_TRUSTED_MANAGER in p for p in parts)
    has_subcmd = any(p in _MUTABLE_SUBCOMMANDS for p in parts)
    return has_manager and has_subcmd


def _get_snapshot_file(conversation_id: str = "") -> Path:
    """Snapshot file lives alongside the config in the sidecar dir, scoped by conversation."""
    suffix = f".{conversation_id}" if conversation_id else ""
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / f".integrity_snapshot{suffix}.json"
    # Legacy fallback
    return Path(__file__).resolve().parent.parent / f".integrity_snapshot{suffix}.json"


def _get_snapshot_dir(conversation_id: str = "") -> Path:
    """Directory where copies of protected files are saved for reversion."""
    suffix = f".{conversation_id}" if conversation_id else ""
    return _get_snapshot_file(conversation_id).parent / f".integrity_snapshot{suffix}"


# Module-level alias for backward compatibility (no conversation scoping)
SNAPSHOT_FILE = _get_snapshot_file("")


def _get_protected_files() -> list[str]:
    """Return absolute paths of files to monitor for integrity."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent

    files = []
    # Sidecar / workspace config and cache
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        sidecar_dir = Path(config_env).resolve().parent
        for name in ["config.json", "injection_cache.json", "security_refusals.jsonl"]:
            p = sidecar_dir / name
            if p.exists():
                files.append(str(p))
    else:
        # Legacy fallback workspaces
        legacy_workspaces = [
            plugin_dir.parent / "bug-fixer-loop-workspace",
            Path.cwd() / ".agents" / "plugins" / "bug-fixer-loop-workspace",
            plugin_dir / ".agents" / "plugins" / "bug-fixer-loop-workspace",
        ]
        for workspace in legacy_workspaces:
            for name in ["config.json", "injection_cache.json", "security_refusals.jsonl"]:
                p = workspace / name
                if p.exists() and str(p) not in files:
                    files.append(str(p))

    # Plugin manifest and hooks files (daemon root is two levels above scripts/)
    daemon_root = plugin_dir.parent.parent
    for d in [plugin_dir, plugin_dir.parent, daemon_root]:
        for name in ["hooks.json", "plugin.json"]:
            p = d / name
            if p.exists() and str(p) not in files:
                files.append(str(p))

    # Consumer plugins.json and sidecar.json
    if config_env and Path(config_env).is_file():
        sidecar_json = Path(config_env).resolve().parent / "sidecar.json"
        if sidecar_json.exists() and str(sidecar_json) not in files:
            files.append(str(sidecar_json))

    # Scanner config files at repo root or plugin dir
    for d in [plugin_dir, plugin_dir.parent, Path.cwd()]:
        for name in [".betterleaks.toml", ".betterleaksignore"]:
            p = d / name
            if p.exists() and str(p) not in files:
                files.append(str(p))

    # Host security scripts themselves
    for name in ["pre_tool_gate.py", "safe_ingest.py",
                  "safe_post.py", "injection_scanner.py",
                  "integrity_monitor.py", "shell_analysis.py", "security_audit.py",
                  "github_url_gate.py"]:
        p = script_dir / name
        if p.exists() and str(p) not in files:
            files.append(str(p))

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
                        sys.stderr.write(f"INTEGRITY: {path} valid hash-chained append — accepted.\n")
                        continue
                except Exception as e:
                    sys.stderr.write(f"INTEGRITY: failed to verify {path} chain: {e}\n")

            if (basename in MANAGEMENT_MUTABLE_BASENAMES
                    and stdin_data is not None
                    and _was_trusted_management_command(stdin_data)):
                # Legitimate management operation — update baseline
                expected[path] = actual_hash
                backup_name = hashlib.sha256(path.encode("utf-8")).hexdigest()
                backup_path = snapshot_dir / backup_name
                if backup_path.is_file():
                    try:
                        shutil.copy2(path, backup_path)
                    except OSError:
                        pass
                sys.stderr.write(
                    f"INTEGRITY: {path} updated by trusted management command — accepted.\n"
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

    raw_cid = stdin_data.get("conversationId", "") if isinstance(stdin_data, dict) else ""
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
