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
import shutil
import subprocess
import sys
from pathlib import Path


def _get_snapshot_file() -> Path:
    """Snapshot file lives alongside the config in the sidecar dir."""
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / ".integrity_snapshot.json"
    # Legacy fallback
    return Path(__file__).resolve().parent.parent / ".integrity_snapshot.json"


def _get_snapshot_dir() -> Path:
    """Directory where copies of protected files are saved for reversion."""
    return _get_snapshot_file().parent / ".integrity_snapshot"


# Module-level alias for backward compatibility
SNAPSHOT_FILE = _get_snapshot_file()


def _get_protected_files() -> list[str]:
    """Return absolute paths of files to monitor for integrity."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent

    files = []
    # Sidecar / workspace config and cache
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        sidecar_dir = Path(config_env).resolve().parent
        for name in ["config.json", "injection_cache.json"]:
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
            for name in ["config.json", "injection_cache.json"]:
                p = workspace / name
                if p.exists() and str(p) not in files:
                    files.append(str(p))

    # Scanner config files at repo root or plugin dir
    for d in [plugin_dir, plugin_dir.parent, Path.cwd()]:
        for name in [".betterleaks.toml", ".betterleaksignore"]:
            p = d / name
            if p.exists() and str(p) not in files:
                files.append(str(p))

    # Host security scripts themselves
    for name in ["pre_tool_gate.py", "safe_ingest.py",
                  "safe_post.py", "injection_scanner.py",
                  "integrity_monitor.py"]:
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


def snapshot() -> None:
    """Take a snapshot of all protected file checksums (called by --pre)."""
    snapshot_file = _get_snapshot_file()
    snapshot_dir = _get_snapshot_dir()
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


def verify() -> None:
    """Verify protected files against snapshot, revert if tampered (called by --post)."""
    snapshot_file = _get_snapshot_file()
    snapshot_dir = _get_snapshot_dir()
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


def main() -> int:
    """Entry point — uses --pre or --post flag to determine mode."""
    # Consume stdin (hooks always send payload on stdin)
    if not sys.stdin.isatty():
        sys.stdin.read()

    if "--post" in sys.argv:
        verify()
        # PostToolUse contract: return empty object
        print(json.dumps({}))
    else:
        # Default: --pre (snapshot)
        snapshot()
        # PreToolUse contract: return decision
        print(json.dumps({"decision": "allow"}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
