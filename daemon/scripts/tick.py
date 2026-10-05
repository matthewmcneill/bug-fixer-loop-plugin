#!/usr/bin/env python3
"""Tick entry point — invoked by the daemon sidecar's schedule builtin.

Delegates all logic to tick_receiver.py in the workspace plugin.
This minimal file lives in the daemon plugin so the sidecar can reference it.
"""
import os
import subprocess
import sys
from pathlib import Path

def main() -> int:
    # Derive workspace plugin scripts dir from daemon layout
    daemon_dir = Path(__file__).resolve().parent.parent
    receiver = daemon_dir / "workspace-plugin" / "bug-fixer" / "scripts" / "tick_receiver.py"

    if not receiver.is_file():
        print(f"tick_receiver.py not found at {receiver}", file=sys.stderr)
        return 1

    result = subprocess.run(
        [sys.executable, str(receiver)],
        env=os.environ,
    )
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
