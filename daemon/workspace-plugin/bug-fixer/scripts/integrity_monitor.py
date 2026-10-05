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
import subprocess
import sys
from pathlib import Path


def _get_protected_files() -> list[str]:
    """Return absolute paths of files to monitor for integrity."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent
    workspace = plugin_dir / ".agents" / "plugins" / "bug-fixer-loop-workspace"

    files = []
    # Workspace config and cache
    for name in ["config.json", "injection_cache.json"]:
        p = workspace / name
        if p.exists():
            files.append(str(p))

    # Scanner config files at repo root
    for name in [".betterleaks.toml", ".betterleaksignore"]:
        p = plugin_dir / name
        if p.exists():
            files.append(str(p))

    # Host security scripts themselves
    for name in ["pre_tool_gate.py", "safe_ingest.py",
                  "safe_post.py", "injection_scanner.py",
                  "integrity_monitor.py"]:
        p = script_dir / name
        if p.exists():
            files.append(str(p))

    return files


def _sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, FileNotFoundError):
        return ""


# Snapshot file: stores checksums taken by the PreToolUse hook
SNAPSHOT_FILE = Path(__file__).resolve().parent.parent / ".agents" / "plugins" / "bug-fixer-loop-workspace" / ".integrity_snapshot.json"


def snapshot() -> None:
    """Take a snapshot of all protected file checksums (called by --pre)."""
    checksums = {}
    for path in _get_protected_files():
        h = _sha256(path)
        if h:
            checksums[path] = h

    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(checksums, f)


def verify() -> None:
    """Verify protected files against snapshot, revert if tampered (called by --post)."""
    if not SNAPSHOT_FILE.exists():
        return

    try:
        with open(SNAPSHOT_FILE) as f:
            expected = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    plugin_dir = Path(__file__).resolve().parent.parent

    for path, expected_hash in expected.items():
        actual_hash = _sha256(path)
        if actual_hash and actual_hash != expected_hash:
            # TAMPERING DETECTED — revert from git
            rel_path = os.path.relpath(path, plugin_dir)
            sys.stderr.write(
                f"INTEGRITY VIOLATION: {rel_path} was modified by an "
                f"indirect write (bypassing PreToolUse gate). Reverting.\n"
            )
            try:
                subprocess.run(
                    ["git", "checkout", "--", rel_path],
                    cwd=str(plugin_dir),
                    capture_output=True,
                    timeout=5,
                )
                sys.stderr.write(f"REVERTED: {rel_path}\n")
            except Exception as e:
                sys.stderr.write(f"REVERT FAILED for {rel_path}: {e}\n")

    # Clean up snapshot
    try:
        SNAPSHOT_FILE.unlink()
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
