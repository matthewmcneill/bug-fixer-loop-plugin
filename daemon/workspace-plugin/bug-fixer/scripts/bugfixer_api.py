#!/usr/bin/env python3
"""Consumer Hook API for bug-fixer-loop plugin.

Provides a stable, namespaced interface for consumer PreToolUse hook scripts
to query bug-fixer session context. Abstracts consumers from the plugin's
internal state files (dispatch tracking, claims, config layout).

Usage in consumer hooks::

    from bugfixer_api import BugFixer

    payload = json.loads(sys.stdin.read())
    ctx = BugFixer.get_session_context(payload.get("conversationId", ""))

    if ctx.is_autonomous:
        print(json.dumps({"decision": "deny", "reason": "..."}))
    else:
        print(json.dumps({"decision": "force_ask"}))

SessionContext fields:
    is_autonomous   -- True if spawned by a bug-fixer dispatcher
    session_type    -- 'fresh_fix', 'reattach', 'pr_iteration',
                       'architect_review', 'triage', 'trajectory', or None
    issue_number    -- GitHub issue number, if applicable
    pr_number       -- GitHub PR number, if applicable
    worktree_path   -- Path to the git worktree, if applicable
    target_repo     -- GitHub owner/repo from config
    config          -- Full plugin config.json snapshot
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SessionContext:
    """Structured context for a bug-fixer session.

    All fields have safe defaults — if the session is not autonomous
    or the plugin state can't be found, consumers get a safe "unknown
    interactive session" context.
    """

    is_autonomous: bool = False
    """True if this conversation was spawned by a bug-fixer dispatcher."""

    session_type: str | None = None
    """Dispatch type: 'fresh_fix', 'reattach', 'pr_iteration',
    'architect_review', 'triage', 'trajectory', or None."""

    issue_number: int | None = None
    """GitHub issue number this session is working on, if applicable."""

    pr_number: int | None = None
    """GitHub PR number this session is working on, if applicable."""

    worktree_path: str | None = None
    """Path to the git worktree this session is operating in."""

    target_repo: str | None = None
    """GitHub owner/repo this plugin instance targets."""

    config: dict = field(default_factory=dict)
    """Full plugin config.json contents (read-only snapshot)."""


# ---------------------------------------------------------------------------
# Internal discovery
# ---------------------------------------------------------------------------

_WORKSPACE_NAME = "bug-fixer-loop-workspace"


def _find_dispatches_dir(workspace_dir: Path | None = None) -> Path | None:
    """Find the dispatches directory.

    Order:
    1. BUG_FIXER_STATE_DIR env var -> <state_dir>/dispatches
    2. Derived from BUG_FIXER_CONFIG env var -> <config_dir>/state/dispatches or <config_dir>/dispatches
    3. Derived from workspace_dir -> <workspace_dir>/state/dispatches or <workspace_dir>/dispatches
    4. Legacy workspace fallback (_WORKSPACE_NAME) -> <workspace>/state/dispatches or <workspace>/dispatches
    """
    state_dir = os.environ.get("BUG_FIXER_STATE_DIR", "")
    if state_dir and Path(state_dir).is_dir():
        return Path(state_dir) / "dispatches"

    # Derive from BUG_FIXER_CONFIG
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env:
        p = Path(config_env).resolve()
        config_dir = p.parent if p.is_file() else p
        if (config_dir / "state" / "dispatches").is_dir():
            return config_dir / "state" / "dispatches"
        if (config_dir / "state").is_dir():
            return config_dir / "state" / "dispatches"
        if (config_dir / "dispatches").is_dir():
            return config_dir / "dispatches"

    # From provided workspace_dir
    if workspace_dir is not None:
        if (workspace_dir / "state" / "dispatches").is_dir():
            return workspace_dir / "state" / "dispatches"
        if (workspace_dir / "dispatches").is_dir():
            return workspace_dir / "dispatches"

    # Legacy fallback from _find_workspace_dir()
    ws = _find_workspace_dir()
    if ws is not None:
        if (ws / "state" / "dispatches").is_dir():
            return ws / "state" / "dispatches"
        if (ws / "dispatches").is_dir():
            return ws / "dispatches"
        return ws / "dispatches"

    return None


def _find_workspace_dir() -> Path | None:
    """Discover the bug-fixer workspace directory.

    Search order:
    1. BUG_FIXER_CONFIG env var → parent directory
    2. Derive from this script's own __file__ location (standard plugin layout:
       plugin dir and workspace dir are siblings under .agents/plugins/)
    3. Relative from cwd (hook cwd is .agents/)
    """
    # 1. Env var (set by sidecar stubs and install.sh)
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env:
        p = Path(config_env).resolve()
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p

    # 2. Script location: scripts/ → plugin dir → sibling workspace
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent
    workspace = plugin_dir.parent / _WORKSPACE_NAME
    if workspace.is_dir():
        return workspace

    # 3. Hook cwd fallback (.agents/)
    cwd_candidate = Path.cwd() / "plugins" / _WORKSPACE_NAME
    if cwd_candidate.is_dir():
        return cwd_candidate

    legacy_cwd = Path.cwd() / ".agents" / "plugins" / _WORKSPACE_NAME
    if legacy_cwd.is_dir():
        return legacy_cwd

    return None


def _load_config(workspace_dir: Path) -> dict:
    """Load config.json from the workspace directory."""
    config_file = workspace_dir / "config.json"
    if not config_file.is_file():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _find_dispatch(workspace_dir: Path, conversation_id: str) -> dict | None:
    """Scan dispatches/ for a matching conversation_id.

    Fast path: dispatch files are named ``{conv_id}.json``, so a direct
    lookup is attempted first.  Falls back to scanning all files.
    """
    if not conversation_id:
        return None

    if workspace_dir.name == "dispatches" and workspace_dir.is_dir():
        dispatches_dir = workspace_dir
    elif (workspace_dir / "state" / "dispatches").is_dir():
        dispatches_dir = workspace_dir / "state" / "dispatches"
    else:
        dispatches_dir = workspace_dir / "dispatches"

    if not dispatches_dir.is_dir():
        return None

    # Fast path: direct filename lookup
    direct = dispatches_dir / f"{conversation_id}.json"
    if direct.is_file():
        try:
            return json.loads(direct.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Slow path: scan all dispatch files
    for f in dispatches_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if data.get("conversation_id") == conversation_id:
                return data
        except (json.JSONDecodeError, OSError):
            continue

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_CONTEXT = SessionContext()


class BugFixer:
    """Consumer Hook API for the bug-fixer-loop plugin.

    All methods are static — no instantiation required.
    Import and use directly in consumer PreToolUse hook scripts::

        from bugfixer_api import BugFixer

        ctx = BugFixer.get_session_context(conversation_id)
        if ctx.is_autonomous:
            ...

    Methods:
        get_session_context(conversation_id) → SessionContext
        is_autonomous(conversation_id) → bool
        get_config() → dict
    """

    @staticmethod
    def get_session_context(conversation_id: str) -> SessionContext:
        """Look up session context for a conversation ID.

        Scans dispatch tracking files to determine if this conversation
        was spawned by a bug-fixer dispatcher, and returns the full
        session context including type, issue/PR number, worktree, and
        plugin config.

        Returns a SessionContext with safe defaults (is_autonomous=False)
        if the conversation is not found or plugin state can't be located.
        """
        if not conversation_id:
            return _DEFAULT_CONTEXT

        workspace = _find_workspace_dir()
        dispatches_dir = _find_dispatches_dir(workspace)

        dispatch = None
        if dispatches_dir is not None:
            dispatch = _find_dispatch(dispatches_dir, conversation_id)
        elif workspace is not None:
            dispatch = _find_dispatch(workspace, conversation_id)

        config = _load_config(workspace) if workspace else {}

        if dispatch is None:
            return SessionContext(
                is_autonomous=False,
                target_repo=config.get("target_repo"),
                config=config,
            )

        return SessionContext(
            is_autonomous=True,
            session_type=dispatch.get("type"),
            issue_number=dispatch.get("issue_number"),
            pr_number=dispatch.get("pr_number"),
            worktree_path=dispatch.get("worktree_path"),
            target_repo=config.get("target_repo"),
            config=config,
        )

    @staticmethod
    def is_autonomous(conversation_id: str) -> bool:
        """Convenience shorthand for the most common check.

        Equivalent to ``BugFixer.get_session_context(conv_id).is_autonomous``.
        """
        return BugFixer.get_session_context(conversation_id).is_autonomous

    @staticmethod
    def get_config() -> dict:
        """Return the plugin's config.json as a dict.

        Useful for consumers that need config values (e.g., protected_files,
        target_repo) without caring about a specific session.
        Returns an empty dict if the workspace can't be located.
        """
        workspace = _find_workspace_dir()
        if workspace is None:
            return {}
        return _load_config(workspace)
