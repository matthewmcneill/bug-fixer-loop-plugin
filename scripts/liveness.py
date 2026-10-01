#!/usr/bin/env python3
"""
liveness.py — Conversation liveness detection and recovery for the bug-fixer loop.

Shared utility used by both engineering_dispatcher.py and architect_dispatcher.py.
Detects stalled LLM conversations, attempts to resume them, and posts structured
state dumps to GitHub issues/PRs when resume fails.

Design principles:
  - GitHub issue/PR comments are the durable source of truth
  - Resume first — don't waste tokens by re-dispatching
  - If resume fails, dump context so humans or architects can pick up
"""

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"
STALE_THRESHOLD = 300       # seconds — transcript must be fresher than this
DEFAULT_TIMEOUT = 45 * 60   # seconds — hard ceiling before forced recovery
MAX_RESUME_ATTEMPTS = 2

# Resume message for plan-gate stalls (Rule 17 override)
RESUME_MSG_PLAN_GATE = (
    "The plan gate rule does not apply to autonomous bug-fixer dispatches (Rule 17 "
    "in the bug-fixer-loop plugin rules). Proceed with implementation immediately. "
    "Do not ask for approval — there is no one listening.\n\n"
    "If the working tree is dirty:\n"
    "- Check if the other agent session is still alive (PID check).\n"
    "- If alive, exit cleanly with ENGINEERING_EXIT.\n"
    "- If dead, stash dirty state and proceed (Rule 18)."
)

# Resume message for generic stalls (timeout, unclear blocker)
RESUME_MSG_GENERIC = (
    "Your session appears to have stalled. Please continue with your task.\n\n"
    "If you are blocked and cannot proceed, post your current state and open "
    "questions as a comment on the issue/PR (prefix with [BUG-FIXER]) and then "
    "exit cleanly."
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _gh(*args) -> subprocess.CompletedProcess:
    return _run(["gh", *args])


# ---------------------------------------------------------------------------
# Transcript inspection
# ---------------------------------------------------------------------------

def transcript_path(conv_id: str) -> Path:
    return BRAIN_DIR / conv_id / ".system_generated" / "logs" / "transcript.jsonl"


def transcript_age_seconds(conv_id: str) -> int | None:
    """Seconds since transcript.jsonl was last modified. None if missing."""
    p = transcript_path(conv_id)
    if not p.exists():
        return None
    return int(datetime.now().timestamp() - p.stat().st_mtime)


def read_last_transcript_step(conv_id: str) -> dict | None:
    """Parse the last line of transcript.jsonl."""
    p = transcript_path(conv_id)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            # Seek to end and read backwards to find last newline
            f.seek(0, 2)
            pos = f.tell()
            if pos == 0:
                return None
            # Read last 64KB — should be more than enough for one step
            read_size = min(pos, 65536)
            f.seek(pos - read_size)
            chunk = f.read(read_size).decode("utf-8", errors="replace")
            lines = chunk.strip().split("\n")
            return json.loads(lines[-1])
    except (json.JSONDecodeError, OSError, IndexError):
        return None


def is_stalled(conv_id: str) -> bool:
    """A conversation is stalled if:
    1. Transcript mtime > STALE_THRESHOLD
    2. Last step is PLANNER_RESPONSE with no tool_calls
    (Agent emitted text and went idle — waiting for input that won't come)
    """
    age = transcript_age_seconds(conv_id)
    if age is None or age < STALE_THRESHOLD:
        return False

    step = read_last_transcript_step(conv_id)
    if step is None:
        return False

    return (
        step.get("type") == "PLANNER_RESPONSE"
        and not step.get("tool_calls")
    )


# ---------------------------------------------------------------------------
# Context extraction from transcript
# ---------------------------------------------------------------------------

def extract_context_from_transcript(conv_id: str) -> dict:
    """Read the transcript and extract a structured summary of what the agent
    accomplished before stalling.

    Returns:
        {
            "approach": str,
            "files_investigated": list[str],
            "blocker": str,
            "last_action": str,
            "artifacts": list[str],
        }
    """
    p = transcript_path(conv_id)
    result = {
        "approach": "",
        "files_investigated": [],
        "blocker": "",
        "last_action": "",
        "artifacts": [],
    }

    if not p.exists():
        result["blocker"] = "Transcript not found — conversation may have been cleaned up."
        return result

    try:
        with open(p) as f:
            steps = [json.loads(line) for line in f if line.strip()]
    except (json.JSONDecodeError, OSError):
        result["blocker"] = "Failed to parse transcript."
        return result

    files_seen = set()
    artifact_paths = []
    last_model_content = ""
    last_tool_name = ""

    artifact_dir = str(BRAIN_DIR / conv_id)

    for step in steps:
        # Collect files from tool calls
        tool_calls = step.get("tool_calls") or []
        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {}) if isinstance(tc.get("arguments"), dict) else {}

            if name == "view_file":
                fp = args.get("AbsolutePath", "")
                if fp:
                    files_seen.add(fp)

            elif name in ("write_to_file", "replace_file_content"):
                fp = args.get("TargetFile", "")
                if fp:
                    files_seen.add(fp)
                    if artifact_dir in fp:
                        artifact_paths.append(fp)

            elif name == "run_command":
                cmd = args.get("CommandLine", "")
                # Extract file paths from grep/find/cat commands
                for match in re.findall(r'(?:/[\w./-]+)+', cmd):
                    if os.path.splitext(match)[1]:  # has extension
                        files_seen.add(match)

            last_tool_name = name

        # Collect model responses
        if step.get("type") == "PLANNER_RESPONSE" and step.get("content"):
            last_model_content = step["content"]

    # Extract approach from the longest model response (usually the plan)
    approach_candidates = []
    for step in steps:
        if step.get("type") == "PLANNER_RESPONSE" and step.get("content"):
            content = step["content"]
            if len(content) > 200:  # substantial responses
                approach_candidates.append(content)

    if approach_candidates:
        # Use the longest substantial response as the approach summary
        longest = max(approach_candidates, key=len)
        # Truncate to first 1500 chars to keep the comment reasonable
        result["approach"] = longest[:1500]
        if len(longest) > 1500:
            result["approach"] += "\n\n_(truncated — see transcript for full context)_"

    # Filter files to only project files (not artifacts, not system files)
    result["files_investigated"] = sorted(
        f for f in files_seen
        if not f.startswith(str(BRAIN_DIR))
        and "/." not in f  # skip dotfiles/dirs
    )

    # Blocker is the last model response (usually ends with the stall question)
    if last_model_content:
        # Take last 500 chars — the blocker is at the end
        result["blocker"] = last_model_content[-500:]

    result["last_action"] = last_tool_name or "unknown"
    result["artifacts"] = artifact_paths

    return result


# ---------------------------------------------------------------------------
# Resume and state dump
# ---------------------------------------------------------------------------

def try_resume(conv_id: str, message: str, log_fn=print) -> bool:
    """Attempt to wake a stalled conversation via agentapi send-message.
    Returns True if the message was accepted."""
    r = _run(["agentapi", "send-message", conv_id, message])
    if r.returncode == 0:
        log_fn(f"Resumed conversation {conv_id[:8]}...")
        return True
    else:
        log_fn(f"Failed to resume {conv_id[:8]}...: {r.stderr or r.stdout}")
        return False


def _format_state_dump(conv_id: str, context: dict, dispatch: dict) -> str:
    """Format a structured comment for posting to a GitHub issue or PR."""
    spawned = dispatch.get("spawned_at", "unknown")
    dtype = dispatch.get("type", "unknown")
    duration = ""
    try:
        spawned_dt = datetime.fromisoformat(spawned)
        elapsed = datetime.now(timezone.utc) - spawned_dt
        minutes = int(elapsed.total_seconds() / 60)
        duration = f" ({minutes} min)"
    except (ValueError, TypeError):
        pass

    lines = [
        f"[BUG-FIXER] ⚠️ Session stalled — context preserved",
        "",
        f"**Session:** `{conv_id}`  ",
        f"**Task:** {dtype.replace('_', ' ')}{duration}  ",
        f"**Last action:** `{context.get('last_action', 'unknown')}`",
        "",
    ]

    if context.get("approach"):
        lines += [
            "### What was accomplished",
            "",
            context["approach"],
            "",
        ]

    if context.get("files_investigated"):
        lines += ["### Files investigated", ""]
        for fp in context["files_investigated"][:20]:  # cap at 20
            lines.append(f"- `{fp}`")
        if len(context["files_investigated"]) > 20:
            lines.append(f"- _...and {len(context['files_investigated']) - 20} more_")
        lines.append("")

    if context.get("blocker"):
        lines += [
            "### Blocker",
            "",
            context["blocker"],
            "",
        ]

    if context.get("artifacts"):
        lines += ["### Artifacts", ""]
        for ap in context["artifacts"]:
            name = os.path.basename(ap)
            lines.append(f"- `{name}` → `{ap}`")
        lines.append("")

    lines += [
        "---",
        "_This context was automatically extracted from the stalled session's "
        "transcript. A human or the next bug-fixer cycle can continue from here._",
    ]

    return "\n".join(lines)


def post_state_dump(target_repo: str, target_type: str, target_number: int,
                    conv_id: str, context: dict, dispatch: dict,
                    log_fn=print) -> bool:
    """Post a structured state dump comment to an issue or PR.

    Args:
        target_type: "issue" or "pr"
        target_number: issue or PR number
    """
    body = _format_state_dump(conv_id, context, dispatch)
    cmd = "issue" if target_type == "issue" else "pr"
    r = _gh(cmd, "comment", str(target_number), "--repo", target_repo,
            "--body", body)
    if r.returncode == 0:
        log_fn(f"Posted state dump to {target_type} #{target_number}")
        return True
    else:
        log_fn(f"Failed to post state dump: {r.stderr}")
        return False


# ---------------------------------------------------------------------------
# Dispatch tracking and sweep
# ---------------------------------------------------------------------------

def record_dispatch(dispatches_dir: Path, conv_id: str,
                    dispatch_type: str, **kwargs) -> None:
    """Write a dispatch tracking file."""
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "conversation_id": conv_id,
        "type": dispatch_type,
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "timeout_minutes": kwargs.pop("timeout_minutes", 45),
        "resume_attempts": 0,
        **kwargs,
    }
    (dispatches_dir / f"{conv_id}.json").write_text(json.dumps(data, indent=2))


def _check_success(dispatch: dict, target_repo: str) -> bool:
    """Check whether the dispatched conversation achieved its goal."""
    dtype = dispatch["type"]
    spawned = dispatch.get("spawned_at", "")

    if dtype in ("fresh_fix", "reattach"):
        issue_num = dispatch.get("issue_number")
        if not issue_num:
            return False
        # Check if issue transitioned to awaiting-review
        r = _gh("issue", "view", str(issue_num), "--repo", target_repo,
                "--json", "labels", "--jq",
                '[.labels[].name] | any(. == "status:awaiting-review")')
        return r.stdout.strip() == "true"

    elif dtype == "pr_iteration":
        pr_num = dispatch.get("pr_number")
        if not pr_num:
            return False
        # Check if needs-iteration label is gone (agent removed it after pushing)
        r = _gh("pr", "view", str(pr_num), "--repo", target_repo,
                "--json", "labels", "--jq",
                '[.labels[].name] | any(. == "needs-iteration")')
        return r.stdout.strip() == "false"

    elif dtype == "architect_review":
        pr_num = dispatch.get("pr_number")
        if not pr_num:
            return False
        # Check for [ARCHITECT] comment posted after spawn
        r = _gh("pr", "view", str(pr_num), "--repo", target_repo,
                "--comments", "--json", "comments")
        if r.returncode != 0:
            return False
        try:
            data = json.loads(r.stdout)
            for c in data.get("comments", []):
                if "[ARCHITECT]" in c.get("body", ""):
                    # Good enough — architect posted something
                    return True
        except (json.JSONDecodeError, KeyError):
            pass
        return False

    return False


def _choose_resume_message(dispatch: dict, last_step: dict | None) -> str:
    """Select the appropriate resume message based on the stall signature."""
    blocker = ""
    if last_step and last_step.get("content"):
        blocker = last_step["content"][-300:].lower()

    # Detect plan-gate stalls
    plan_gate_phrases = [
        "shall i proceed",
        "would you like",
        "waiting for",
        "explicit approval",
        "plan gate",
        "shall i implement",
        "shall i go ahead",
        "do you want me to",
    ]
    if any(phrase in blocker for phrase in plan_gate_phrases):
        return RESUME_MSG_PLAN_GATE

    return RESUME_MSG_GENERIC


def _recover(dispatch: dict, dispatches_dir: Path, target_repo: str,
             claims_dir: Path | None, log_fn) -> dict:
    """Execute the full recovery cascade for a stalled dispatch.

    Returns a summary dict for logging.
    """
    conv_id = dispatch["conversation_id"]
    dtype = dispatch["type"]
    dispatch_file = dispatches_dir / f"{conv_id}.json"

    summary = {
        "conversation_id": conv_id,
        "type": dtype,
        "action": "unknown",
    }

    # --- Step 1: Try to resume ---
    attempts = dispatch.get("resume_attempts", 0)
    if attempts < MAX_RESUME_ATTEMPTS:
        last_step = read_last_transcript_step(conv_id)
        message = _choose_resume_message(dispatch, last_step)

        if try_resume(conv_id, message, log_fn):
            # Update tracking — increment attempts, reset timeout
            dispatch["resume_attempts"] = attempts + 1
            dispatch["last_resume_at"] = datetime.now(timezone.utc).isoformat()
            dispatch_file.write_text(json.dumps(dispatch, indent=2))
            summary["action"] = f"resumed (attempt {attempts + 1})"
            return summary

        log_fn(f"Resume failed for {conv_id[:8]}... (attempt {attempts + 1})")

    # --- Step 2: Extract context and post state dump ---
    context = extract_context_from_transcript(conv_id)

    if dtype in ("fresh_fix", "reattach"):
        issue_num = dispatch.get("issue_number")
        if issue_num:
            post_state_dump(target_repo, "issue", issue_num,
                            conv_id, context, dispatch, log_fn)
            # Clean up: unclaim the issue
            _gh("issue", "edit", str(issue_num), "--repo", target_repo,
                "--remove-label", "status:in-progress")
            # Delete lock file
            if claims_dir:
                lock = claims_dir / f"{issue_num}.lock"
                lock.unlink(missing_ok=True)
            summary["action"] = f"state dumped to issue #{issue_num}, unclaimed"

    elif dtype == "pr_iteration":
        pr_num = dispatch.get("pr_number")
        if pr_num:
            post_state_dump(target_repo, "pr", pr_num,
                            conv_id, context, dispatch, log_fn)
            # needs-iteration label stays — it was never removed
            # This means the next tick will re-dispatch (with the state dump
            # visible in the PR comments for context)
            summary["action"] = f"state dumped to PR #{pr_num}, needs-iteration preserved"

    elif dtype == "architect_review":
        pr_num = dispatch.get("pr_number")
        if pr_num:
            post_state_dump(target_repo, "pr", pr_num,
                            conv_id, context, dispatch, log_fn)
            summary["action"] = f"state dumped to PR #{pr_num}"

    # Clean up tracking file
    dispatch_file.unlink(missing_ok=True)
    return summary


def sweep_dispatches(dispatches_dir: Path, target_repo: str,
                     log_fn, claims_dir: Path | None = None) -> list[dict]:
    """Main liveness sweep. Check all tracked dispatches for completion or stalls.

    Called at the start of every dispatcher tick.

    Returns list of recovery summaries for logging.
    """
    if not dispatches_dir.exists():
        return []

    recovered = []

    for dispatch_file in list(dispatches_dir.glob("*.json")):
        try:
            dispatch = json.loads(dispatch_file.read_text())
        except (json.JSONDecodeError, OSError):
            dispatch_file.unlink(missing_ok=True)
            continue

        conv_id = dispatch["conversation_id"]

        # --- Check if work completed successfully ---
        if _check_success(dispatch, target_repo):
            log_fn(f"Dispatch {conv_id[:8]}... ({dispatch['type']}) completed successfully.")
            dispatch_file.unlink(missing_ok=True)
            continue

        # --- Check if conversation is still actively working ---
        age = transcript_age_seconds(conv_id)
        stalled = is_stalled(conv_id)

        if age is not None and age < STALE_THRESHOLD and not stalled:
            continue  # Still working

        # --- Check timeout ---
        timed_out = False
        try:
            spawned_dt = datetime.fromisoformat(dispatch["spawned_at"])
            elapsed = (datetime.now(timezone.utc) - spawned_dt).total_seconds()
            timeout = dispatch.get("timeout_minutes", 45) * 60
            timed_out = elapsed > timeout
        except (ValueError, KeyError):
            pass

        if not stalled and not timed_out:
            continue  # Not stalled and not timed out — give it more time

        # --- Stalled or timed out: recover ---
        reason = "stalled" if stalled else "timed out"
        log_fn(f"Dispatch {conv_id[:8]}... ({dispatch['type']}) {reason}. "
               f"Initiating recovery.")
        summary = _recover(dispatch, dispatches_dir, target_repo,
                           claims_dir, log_fn)
        recovered.append(summary)

    return recovered
