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
    import shell_analysis
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import security_audit
    import shell_analysis
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
    return candidate


def _canonical_manage_paths() -> set[Path]:
    """Return canonical resolved paths for the daemon's manage.py."""
    paths = {(_find_daemon_root() / "scripts" / "manage.py").resolve()}
    global_manage = (
        Path.home() / ".gemini" / "config" / "plugins" / "bug-fixer-daemon" / "scripts" / "manage.py"
    )
    paths.add(global_manage.expanduser().resolve())
    return paths


def _was_trusted_management_command(stdin_data: dict) -> bool:
    """True when the just-executed command was a single trusted manage.py mutation.

    Uses structural shell parsing (shell_analysis.parse) so comments, compound
    commands (&&, ;, |), command substitutions, environment variable overrides,
    non-canonical script paths, or inline interpreter flags (-c) cannot spoof
    a manage.py invocation.
    """
    tool_call = stdin_data.get("toolCall", {})
    if not isinstance(tool_call, dict):
        return False
    if tool_call.get("name") != "run_command":
        return False
    cmd = str((tool_call.get("args") or {}).get("CommandLine") or "").strip()
    if _TRUSTED_MANAGER not in cmd:
        return False

    try:
        parsed = shell_analysis.parse(cmd)
    except shell_analysis.ShellParseError:
        return False

    if parsed.substitutions:
        return False

    active_segments = [
        s for s in parsed.segments
        if s.words or s.redirects_out or s.redirects_in or s.assignments
    ]
    if len(active_segments) != 1:
        return False

    seg = active_segments[0]
    if seg.assignments or seg.redirects_out or seg.piped_from is not None:
        return False

    argv = seg.argv
    if not argv or any(not w.literal for w in argv):
        return False

    if argv[0].text == "env" or Path(argv[0].text).name == "env":
        return False

    idx = 0
    if idx < len(argv) and _PYTHON_INTERP_RE.match(Path(argv[idx].text).name):
        idx += 1
        while idx < len(argv) and argv[idx].text.startswith("-"):
            if argv[idx].text not in _SAFE_PYTHON_FLAGS:
                return False
            idx += 1

    if idx >= len(argv):
        return False

    try:
        script_path = Path(argv[idx].text).expanduser().resolve()
    except OSError:
        return False
    if script_path not in _canonical_manage_paths():
        return False
    idx += 1

    if idx >= len(argv) or argv[idx].text not in _MUTABLE_SUBCOMMANDS:
        return False

    subcommand = argv[idx].text
    rest_args = [w.text for w in argv[idx + 1 :]]
    if subcommand == "config":
        if not any(a == "--set" or a.startswith("--set=") for a in rest_args):
            return False

    return True


def _find_sidecar_config(workspace_paths: list[str] | None = None) -> Path | None:
    """Locate the active sidecar config.json via BUG_FIXER_CONFIG or workspace_paths."""
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve()

    search_wps = list(workspace_paths) if workspace_paths else []
    if not search_wps:
        try:
            search_wps = [str(Path.cwd().resolve())]
        except OSError:
            search_wps = []

    if search_wps:
        daemon_roots = [_find_daemon_root()]
        global_daemon = Path.home() / ".gemini" / "config" / "plugins" / "bug-fixer-daemon"
        if global_daemon not in daemon_roots:
            daemon_roots.append(global_daemon)
        for d_root in daemon_roots:
            sidecars_dir = d_root / "sidecars"
            if not sidecars_dir.is_dir():
                continue
            for sidecar_sub in sorted(sidecars_dir.iterdir()):
                if not sidecar_sub.is_dir():
                    continue
                cand_conf = sidecar_sub / "config.json"
                if not cand_conf.is_file():
                    continue
                try:
                    c_data = json.loads(cand_conf.read_text(encoding="utf-8"))
                    r_dir = c_data.get("repo_dir")
                    if r_dir:
                        r_path = Path(r_dir).resolve()
                        for wp in search_wps:
                            if Path(wp).resolve().is_relative_to(r_path):
                                return cand_conf.resolve()
                except (json.JSONDecodeError, OSError):
                    continue
    return None


def _get_snapshot_file(
    conversation_id: str = "", workspace_paths: list[str] | None = None
) -> Path:
    """Snapshot file lives alongside the config in the sidecar dir, scoped by conversation."""
    suffix = f".{conversation_id}" if conversation_id else ""
    cfg_path = _find_sidecar_config(workspace_paths)
    if cfg_path is not None:
        return cfg_path.parent / f".integrity_snapshot{suffix}.json"
    # Legacy fallback
    return Path(__file__).resolve().parent.parent / f".integrity_snapshot{suffix}.json"


def _get_snapshot_dir(
    conversation_id: str = "", workspace_paths: list[str] | None = None
) -> Path:
    """Directory where copies of protected files are saved for reversion."""
    suffix = f".{conversation_id}" if conversation_id else ""
    return _get_snapshot_file(conversation_id, workspace_paths).parent / f".integrity_snapshot{suffix}"


# Module-level alias for backward compatibility (no conversation scoping)
SNAPSHOT_FILE = _get_snapshot_file("")


def _get_protected_files(workspace_paths: list[str] | None = None) -> list[str]:
    """Return absolute paths of files to monitor for integrity."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent

    files: list[str] = []
    discovered_configs: list[Path] = []
    # Sidecar / workspace config and cache
    cfg_path = _find_sidecar_config(workspace_paths)
    if cfg_path is not None and cfg_path.is_file():
        discovered_configs.append(cfg_path)
        sidecar_dir = cfg_path.parent
        for name in ["config.json", "injection_cache.json", "security_refusals.jsonl", "sidecar.json"]:
            p = sidecar_dir / name
            if p.exists() and str(p) not in files:
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
                    if name == "config.json" and p not in discovered_configs:
                        discovered_configs.append(p)

    # Read config.json and monitor target repository's spec_file_path, protected_files, and .agents/plugins.json
    for conf_file in discovered_configs:
        try:
            cfg_data = json.loads(conf_file.read_text(encoding="utf-8"))
            if not isinstance(cfg_data, dict):
                continue
        except (json.JSONDecodeError, OSError):
            continue

        repo_dir_str = str(cfg_data.get("repo_dir") or "").strip()
        if not repo_dir_str and workspace_paths:
            repo_dir_str = str(workspace_paths[0]).strip()
        repo_base = Path(repo_dir_str).expanduser().resolve() if repo_dir_str else Path.cwd().resolve()

        spec_file = str(cfg_data.get("spec_file_path") or "").strip()
        if spec_file:
            sp = Path(spec_file).expanduser()
            sp_res = sp.resolve() if sp.is_absolute() else (repo_base / sp).resolve()
            if sp_res.exists() and str(sp_res) not in files:
                files.append(str(sp_res))

        for pf in cfg_data.get("protected_files") or []:
            if not pf or not isinstance(pf, str):
                continue
            pf_p = Path(pf).expanduser()
            pf_res = pf_p.resolve() if pf_p.is_absolute() else (repo_base / pf_p).resolve()
            if pf_res.exists() and str(pf_res) not in files:
                files.append(str(pf_res))

        plugins_json = repo_base / ".agents" / "plugins.json"
        if plugins_json.exists() and str(plugins_json) not in files:
            files.append(str(plugins_json))

        for name in [".betterleaks.toml", ".betterleaksignore"]:
            bl_p = repo_base / name
            if bl_p.exists() and str(bl_p) not in files:
                files.append(str(bl_p))

    # Plugin manifest and hooks files (daemon root is two levels above scripts/)
    daemon_root = plugin_dir.parent.parent
    for d in [plugin_dir, plugin_dir.parent, daemon_root]:
        for name in ["hooks.json", "plugin.json"]:
            p = d / name
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


def snapshot(
    conversation_id: str = "", workspace_paths: list[str] | None = None
) -> None:
    """Take a snapshot of all protected file checksums (called by --pre)."""
    snapshot_file = _get_snapshot_file(conversation_id, workspace_paths)
    snapshot_dir = _get_snapshot_dir(conversation_id, workspace_paths)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    if workspace_paths is not None:
        try:
            protected_files = _get_protected_files(workspace_paths)
        except TypeError:
            protected_files = _get_protected_files()
    else:
        protected_files = _get_protected_files()

    checksums = {}
    for path in protected_files:
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
    workspace_paths = (
        stdin_data.get("workspacePaths")
        if isinstance(stdin_data, dict) and isinstance(stdin_data.get("workspacePaths"), list)
        else None
    )
    snapshot_file = _get_snapshot_file(conversation_id, workspace_paths)
    snapshot_dir = _get_snapshot_dir(conversation_id, workspace_paths)
    if not snapshot_file.exists():
        cmd_text = ""
        if isinstance(stdin_data, dict):
            tool_call = stdin_data.get("toolCall", {})
            if isinstance(tool_call, dict):
                cmd_text = str((tool_call.get("args") or {}).get("CommandLine") or "")
        cfg_path = _find_sidecar_config(workspace_paths)
        sys.stderr.write(
            f"INTEGRITY WARNING: snapshot file missing at {snapshot_file} during post-hook verification.\n"
        )
        try:
            security_audit.log_security_event(
                "integrity_snapshot_missing",
                f"Integrity snapshot missing at {snapshot_file}",
                1.0,
                cmd_text or str(snapshot_file),
                str(cfg_path) if cfg_path else "",
            )
        except Exception as exc:
            sys.stderr.write(f"INTEGRITY: failed to log missing snapshot event: {exc}\n")
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
    for sidecar in sorted(sidecars_dir.iterdir()):
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
