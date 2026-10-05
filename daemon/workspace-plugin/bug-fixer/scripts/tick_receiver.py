#!/usr/bin/env python3
"""Tick receiver — dispatches overdue loops.

Called by tick.py (daemon plugin). BUG_FIXER_CONFIG and BUG_FIXER_STATE_DIR
must be in the environment (set by the sidecar's env block).
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    config_path = os.environ.get("BUG_FIXER_CONFIG")
    if not config_path or not Path(config_path).is_file():
        print("BUG_FIXER_CONFIG not set or missing", file=sys.stderr)
        return 1

    CONFIG = json.loads(Path(config_path).read_text())

    _state_env = os.environ.get("BUG_FIXER_STATE_DIR")
    STATE_DIR = Path(_state_env) if _state_env else Path(config_path).parent / "state"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR = Path(__file__).resolve().parent

    LOOPS = {
        "triage":      {"script": "triage_dispatcher.py",     "key": "triage_interval_min",      "default": 60},
        "engineering": {"script": "engineering_dispatcher.py", "key": "engineering_interval_min",  "default": 10},
        "architect":   {"script": "architect_dispatcher.py",   "key": "architect_interval_min",    "default": 15},
        "trajectory":  {"script": "trajectory_dispatcher.py",  "key": "trajectory_interval_min",   "default": 1440},
    }

    launched = 0
    for name, loop in LOOPS.items():
        interval = CONFIG.get(loop["key"], loop["default"])  # flat key
        marker = STATE_DIR / f"{name}.last"
        lock_file = STATE_DIR / f"{name}.lock"
        log_file = STATE_DIR / f"{name}.log"

        # Check if due
        if marker.exists():
            elapsed = time.time() - marker.stat().st_mtime
            if elapsed < interval * 60:
                continue

        # Try lock (non-blocking)
        lock_fd = open(lock_file, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_fd.close()
            continue  # already running

        # Launch — pass the lock fd to the child so IT holds the lock
        marker.touch()
        child_fd = lock_fd.fileno()
        log_fh = open(log_file, "a")
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIR / loop["script"])],
            env=os.environ,
            stdout=log_fh,
            stderr=log_fh,
            pass_fds=(child_fd,),
            start_new_session=True,  # survive parent exit
        )
        lock_fd.close()  # parent releases its copy; child still holds it
        log_fh.close()   # parent closes its copy; child inherits the fd
        launched += 1

        # Write exit status when child finishes (best-effort, non-blocking)
        # The child writes its own exit code on completion — this is for
        # the case where a subsequent tick wants to check the last result.
        exit_file = STATE_DIR / f"{name}.exit"
        # We don't wait — the child runs independently.

    return 0


if __name__ == "__main__":
    sys.exit(main())
