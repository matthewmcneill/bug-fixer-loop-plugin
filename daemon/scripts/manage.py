#!/usr/bin/env python3
"""
manage.py — Bug-fixer management CLI.

All mutable operations require explicit CLI arguments — no environment variable
dependencies. The agent reads env vars and passes them as arguments; the human
confirms via ask_question before execution.

Read-only operations (status, logs, config --show) can be run freely.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_DIR = Path(__file__).resolve().parent.parent
SIDECARS_DIR = DAEMON_DIR / "sidecars"
SCRIPTS_DIR = DAEMON_DIR / "scripts"
WORKSPACE_PLUGIN_ENTRY = "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin/bug-fixer"

REQUIRED_LABELS = {
    "bug-fixer:engineer:needs-fix":            "0E8A16",
    "bug-fixer:engineer:in-progress":          "FBCA04",
    "bug-fixer:engineer:needs-iteration":      "FBCA04",
    "bug-fixer:human:required":                "D93F0B",
    "bug-fixer:human:needs-approach-review":   "C5DEF5",
    "bug-fixer:human:input-needed":            "BFD4F2",
    "bug-fixer:human:project-candidate":       "C2E0C6",
    "bug-fixer:human:spec-change-request":     "E4E669",
    "bug-fixer:human:needs-intervention":      "D93F0B",
    "bug-fixer:architect:awaiting-review":     "1D76DB",
    "bug-fixer:system:pr":                     "5319E7",
    "bug-fixer:system:health":                 "006B75",
    "bug-fixer:complexity:mechanical":         "BFDADC",
    "bug-fixer:complexity:standard":           "D4C5F9",
    "bug-fixer:complexity:architect":          "F9D0C4",
    "security:prompt-injection":               "B60205",
}

DEFAULT_CONFIG = {
    "target_repo": "",
    "repo_dir": "",
    "spec_file_path": "docs/design_specification.md",
    "protected_files": [],
    "test_command": "",
    "maintainer": "",
    "trusted_users": [],
    "triage_interval_min": 60,
    "engineering_interval_min": 10,
    "architect_interval_min": 15,
    "trajectory_interval_min": 1440,
    "triage_model": "flash",
    "engineering_model": "pro",
    "architect_model": "pro",
    "trajectory_model": "flash",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def repo_to_slug(repo: str) -> str:
    return repo.replace("/", "--")


def slug_to_repo(slug: str) -> str:
    return slug.replace("--", "/", 1)


def get_sidecar_dir(repo: str) -> Path:
    return SIDECARS_DIR / repo_to_slug(repo)


def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_config(repo: str) -> dict:
    return load_json(get_sidecar_dir(repo) / "config.json")


def load_sidecar(repo: str) -> dict:
    return load_json(get_sidecar_dir(repo) / "sidecar.json")


def list_enrolled_repos() -> list[str]:
    if not SIDECARS_DIR.exists():
        return []
    return sorted(
        slug_to_repo(d.name)
        for d in SIDECARS_DIR.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    )


def build_sidecar(repo: str, repo_dir: str, project_id: str,
                  sidecar_dir: Path, path_env: str) -> dict:
    """Build sidecar.json content from explicit values."""
    return {
        "builtin": "schedule",
        "args": ["*/5 * * * *", "python3", str(SCRIPTS_DIR / "tick.py")],
        "restart_policy": "always",
        "display_name": f"Bug Fixer \u2014 {repo}",
        "agent_permissions": {
            "workspace_uris": [f"file://{repo_dir}"]
        },
        "env": {
            "BUG_FIXER_CONFIG": str(sidecar_dir / "config.json"),
            "BUG_FIXER_STATE_DIR": str(sidecar_dir / "state"),
            "ANTIGRAVITY_PROJECT_ID": project_id,
            "PATH": path_env,
        }
    }


# ---------------------------------------------------------------------------
# status — read-only
# ---------------------------------------------------------------------------
def cmd_status(args):
    repos = [args.repo] if args.repo else list_enrolled_repos()
    if not repos:
        print("No enrolled repositories.")
        return 0

    for repo in repos:
        sidecar_dir = get_sidecar_dir(repo)
        state_dir = sidecar_dir / "state"
        config = load_config(repo)
        sidecar = load_sidecar(repo)
        env = sidecar.get("env", {})

        print(f"\n{'='*60}")
        print(f"Repository: {repo}")
        print(f"  Repo dir:   {config.get('repo_dir', 'N/A')}")
        print(f"  Config:     {sidecar_dir / 'config.json'}")
        pid = env.get("ANTIGRAVITY_PROJECT_ID", "")
        print(f"  Project ID: {'✅ ' + pid[:12] + '...' if pid else '❌ MISSING'}")
        print(f"  PATH:       {'✅ set' if env.get('PATH') else '❌ MISSING'}")

        # Loop status
        loops = ["triage", "engineering", "architect", "trajectory"]
        print(f"\n  Loop Status:")
        for loop in loops:
            marker = state_dir / f"{loop}.last"
            log_file = state_dir / f"{loop}.log"

            if marker.exists():
                age = int(time.time() - marker.stat().st_mtime)
                h, m = divmod(age // 60, 60)
                age_str = f"{h}h {m}m ago" if h else f"{m}m ago"
            else:
                age_str = "never"

            status_icon = "⏸️"
            last_msg = ""
            if log_file.exists():
                lines = log_file.read_text().strip().splitlines()
                if lines:
                    last_line = lines[-1]
                    if "FATAL" in last_line or "ERROR" in last_line:
                        status_icon = "❌"
                    elif "Spawned" in last_line or "Reattached" in last_line:
                        status_icon = "✅"
                    elif "No " in last_line or "EXIT" in last_line:
                        status_icon = "💤"
                    else:
                        status_icon = "✅"
                    last_msg = last_line.split("\u2014", 1)[-1].strip() if "\u2014" in last_line else ""

            print(f"    {status_icon} {loop:15s} last={age_str}")
            if status_icon == "❌" and last_msg:
                print(f"       └─ {last_msg[:120]}")

        # Cooldowns
        for fname, name in [("cooldown.json", "engineering"), ("triage_cooldown.json", "triage")]:
            cf = state_dir / fname
            if cf.exists():
                cd = load_json(cf)
                if cd:
                    print(f"\n  ⚠️  {name} cooldown: {cd.get('reason', 'unknown')}")

        # Active claims
        claims_dir = state_dir / "claims"
        if claims_dir.exists():
            claims = list(claims_dir.glob("*.lock"))
            if claims:
                print(f"\n  Active claims ({len(claims)}):")
                for c in claims:
                    claim = load_json(c)
                    cid = claim.get("conversation_id", "?")
                    print(f"    Issue #{claim.get('issue', '?')} \u2014 conv {cid[:8]}...")

        # Active dispatches
        dispatches_dir = state_dir / "dispatches"
        if dispatches_dir.exists():
            dispatches = list(dispatches_dir.glob("*.json"))
            if dispatches:
                print(f"\n  Active dispatches ({len(dispatches)}):")
                for d_file in dispatches:
                    disp = load_json(d_file)
                    cid = disp.get("conversation_id", "?")
                    print(f"    {disp.get('type', '?')} \u2014 conv {cid[:8]}...")

    print()
    return 0


# ---------------------------------------------------------------------------
# logs — read-only
# ---------------------------------------------------------------------------
def cmd_logs(args):
    repos = [args.repo] if args.repo else list_enrolled_repos()
    for repo in repos:
        log_file = get_sidecar_dir(repo) / "state" / f"{args.loop}.log"
        if not log_file.exists():
            print(f"No log file for {args.loop} in {repo}")
            continue
        if len(repos) > 1:
            print(f"\n--- {repo} / {args.loop} ---")
        lines = log_file.read_text().strip().splitlines()
        for line in lines[-args.n:]:
            print(line)
    return 0


# ---------------------------------------------------------------------------
# config — read-only (--show) or mutable (--set)
# ---------------------------------------------------------------------------
def cmd_config(args):
    if args.set:
        if not args.repo:
            print("ERROR: --set requires --repo", file=sys.stderr)
            return 1
        config_path = get_sidecar_dir(args.repo) / "config.json"
        if not config_path.exists():
            print(f"ERROR: not enrolled: {args.repo}", file=sys.stderr)
            return 1

        config = json.loads(config_path.read_text())
        key, sep, value = args.set.partition("=")
        if not key or not sep:
            print("ERROR: format is KEY=VALUE", file=sys.stderr)
            return 1

        # Type inference
        if value.isdigit():
            value = int(value)
        elif value.replace(".", "", 1).isdigit():
            value = float(value)
        elif value.lower() in ("true", "false"):
            value = value.lower() == "true"
        elif value.startswith("[") or value.startswith("{"):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass

        old = config.get(key, "<not set>")
        config[key] = value
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"Updated {args.repo}: {key} = {json.dumps(value)} (was: {json.dumps(old)})")
        return 0

    # Show
    repos = [args.repo] if args.repo else list_enrolled_repos()
    for repo in repos:
        config = load_config(repo)
        if not config:
            print(f"Not enrolled: {repo}")
            continue
        if len(repos) > 1:
            print(f"\n--- {repo} ---")
        print(json.dumps(config, indent=2))
    return 0


# ---------------------------------------------------------------------------
# enroll — mutable
# ---------------------------------------------------------------------------
def cmd_enroll(args):
    repo = args.repo
    repo_dir = os.path.abspath(args.dir)
    project_id = args.project_id
    path_env = args.path or os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    if not os.path.isdir(repo_dir):
        print(f"ERROR: directory does not exist: {repo_dir}", file=sys.stderr)
        return 1
    if not project_id:
        print("ERROR: --project-id is required", file=sys.stderr)
        return 1

    sidecar_dir = get_sidecar_dir(repo)
    state_dir = sidecar_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # 1. Config (preserve if exists)
    config_path = sidecar_dir / "config.json"
    if config_path.exists():
        print(f"Config exists \u2014 keeping: {config_path}")
        # Ensure target_repo and repo_dir are current
        config = json.loads(config_path.read_text())
        config["target_repo"] = repo
        config["repo_dir"] = repo_dir
        config_path.write_text(json.dumps(config, indent=2) + "\n")
    else:
        config = dict(DEFAULT_CONFIG)
        config["target_repo"] = repo
        config["repo_dir"] = repo_dir
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"Created default config: {config_path}")
        print(f"  \u26a0\ufe0f  Edit to set: maintainer, test_command, protected_files, spec_file_path")

    # 2. Sidecar
    sidecar = build_sidecar(repo, repo_dir, project_id, sidecar_dir, path_env)
    sidecar_path = sidecar_dir / "sidecar.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
    print(f"{'Updated' if sidecar_path.exists() else 'Created'} sidecar: {sidecar_path}")

    # 3. State subdirectories
    for subdir in ["claims", "dispatches", "tmp"]:
        (state_dir / subdir).mkdir(exist_ok=True)

    # 4. Consumer .agents/plugins.json
    plugins_json = Path(repo_dir) / ".agents" / "plugins.json"
    plugins_json.parent.mkdir(parents=True, exist_ok=True)
    entry = {"path": WORKSPACE_PLUGIN_ENTRY}
    data = load_json(plugins_json)
    if "entries" not in data:
        data["entries"] = []
    if entry not in data["entries"]:
        data["entries"].append(entry)
        plugins_json.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Updated consumer plugins.json: {plugins_json}")
    else:
        print(f"Consumer plugins.json already has entry: {plugins_json}")

    print(f"\n\u2705 Enrollment complete for {repo}")
    print(f"   Sidecar will start ticking within 5 minutes.")
    print(f"   Run 'status' to verify loops are running.")
    return 0


# ---------------------------------------------------------------------------
# unenroll — mutable
# ---------------------------------------------------------------------------
def cmd_unenroll(args):
    repo = args.repo
    sidecar_dir = get_sidecar_dir(repo)

    if not sidecar_dir.exists():
        print(f"Not enrolled: {repo}")
        return 1

    # Check for active claims
    claims_dir = sidecar_dir / "state" / "claims"
    if claims_dir.exists():
        active = list(claims_dir.glob("*.lock"))
        if active:
            print(f"ERROR: {len(active)} active claim(s). Reset locks first or wait.", file=sys.stderr)
            return 1

    config = load_config(repo)
    repo_dir = config.get("repo_dir", "")

    shutil.rmtree(sidecar_dir)
    print(f"Removed sidecar: {sidecar_dir}")

    # Clean consumer plugins.json
    if repo_dir:
        plugins_json = Path(repo_dir) / ".agents" / "plugins.json"
        if plugins_json.exists():
            data = load_json(plugins_json)
            remove_paths = {
                WORKSPACE_PLUGIN_ENTRY,
                "~/.gemini/config/plugins/bug-fixer-daemon/workspace-plugin",
            }
            entries = data.get("entries", [])
            new_entries = [e for e in entries if e.get("path") not in remove_paths]
            if len(new_entries) != len(entries):
                data["entries"] = new_entries
                plugins_json.write_text(json.dumps(data, indent=2) + "\n")
                print(f"Removed from consumer plugins.json: {plugins_json}")

    print(f"\n\u2705 Unenrolled {repo}")
    return 0


# ---------------------------------------------------------------------------
# reset — mutable
# ---------------------------------------------------------------------------
def cmd_reset(args):
    repos = [args.repo] if args.repo else list_enrolled_repos()
    do_all = args.all
    anything = args.cooldowns or args.locks or args.dispatches or do_all

    if not anything:
        print("Specify --cooldowns, --locks, --dispatches, or --all")
        return 1

    for repo in repos:
        state_dir = get_sidecar_dir(repo) / "state"
        if not state_dir.exists():
            continue
        print(f"\n--- {repo} ---")

        if args.cooldowns or do_all:
            for f in ["cooldown.json", "triage_cooldown.json",
                       "failure_count", "triage_failure_count",
                       "worktree_failures.json",
                       "issue_dispatch_failures.json"]:
                p = state_dir / f
                if p.exists():
                    p.unlink()
                    print(f"  Cleared: {f}")

        if args.locks or do_all:
            for f in state_dir.glob("*.lock"):
                f.unlink()
                print(f"  Cleared: {f.name}")
            claims_dir = state_dir / "claims"
            if claims_dir.exists():
                for f in claims_dir.glob("*.lock"):
                    f.unlink()
                    print(f"  Cleared claim: {f.name}")

        if args.dispatches or do_all:
            dispatches_dir = state_dir / "dispatches"
            if dispatches_dir.exists():
                for f in dispatches_dir.glob("*.json"):
                    f.unlink()
                    print(f"  Cleared dispatch: {f.name}")

    print("\n\u2705 Reset complete")
    return 0


# ---------------------------------------------------------------------------
# labels — mutable
# ---------------------------------------------------------------------------
def cmd_labels(args):
    repo = args.repo
    r = subprocess.run(
        ["gh", "label", "list", "--repo", repo, "--json", "name", "--limit", "200"],
        capture_output=True, text=True,
    )
    existing = set()
    if r.returncode == 0:
        try:
            existing = {l["name"] for l in json.loads(r.stdout)}
        except (json.JSONDecodeError, KeyError):
            pass

    created = 0
    for label, color in REQUIRED_LABELS.items():
        if label in existing:
            continue
        r = subprocess.run(
            ["gh", "label", "create", label, "--repo", repo, "--color", color, "--force"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"  Created: {label}")
            created += 1
        else:
            print(f"  Failed:  {label} \u2014 {r.stderr.strip()}")

    if created == 0:
        print(f"All {len(REQUIRED_LABELS)} labels already exist on {repo}")
    else:
        print(f"\n\u2705 Created {created} label(s) on {repo}")
    return 0


# ---------------------------------------------------------------------------
# refresh-sidecars — for use by install.sh during upgrades
# ---------------------------------------------------------------------------
def cmd_refresh(args):
    """Regenerate sidecar.json for all enrolled repos, preserving per-repo values."""
    repos = list_enrolled_repos()
    if not repos:
        print("No enrolled repositories to refresh.")
        return 0

    refreshed = 0
    for repo in repos:
        sidecar_dir = get_sidecar_dir(repo)
        config = load_config(repo)
        existing = load_sidecar(repo)
        existing_env = existing.get("env", {})

        project_id = existing_env.get("ANTIGRAVITY_PROJECT_ID", "")
        if not project_id:
            print(f"  \u26a0\ufe0f  {repo}: no project ID in sidecar \u2014 re-enroll to fix")
            continue

        repo_dir = config.get("repo_dir", "")
        if not repo_dir:
            print(f"  \u26a0\ufe0f  {repo}: no repo_dir in config \u2014 skipping")
            continue

        # Preserve PATH from existing sidecar
        path_env = existing_env.get("PATH",
                                    os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))

        sidecar = build_sidecar(repo, repo_dir, project_id, sidecar_dir, path_env)
        sidecar_path = sidecar_dir / "sidecar.json"
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
        print(f"  \u2705 Refreshed: {repo}")
        refreshed += 1

    print(f"\nRefreshed {refreshed}/{len(repos)} sidecar(s)")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Bug-fixer management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Read-only: status, logs, config --show\n"
               "Mutable (HITL required): enroll, unenroll, config --set, reset, labels",
    )
    sub = parser.add_subparsers(dest="command", help="Management command")

    p = sub.add_parser("status", help="Show health status")
    p.add_argument("--repo", help="Filter to repo (owner/repo)")

    p = sub.add_parser("logs", help="View loop logs")
    p.add_argument("loop", choices=["triage", "engineering", "architect", "trajectory"])
    p.add_argument("--repo", help="Filter to repo (owner/repo)")
    p.add_argument("-n", type=int, default=30, help="Lines to show (default: 30)")

    p = sub.add_parser("config", help="View or update config")
    p.add_argument("--show", action="store_true", help="Show config")
    p.add_argument("--set", metavar="KEY=VALUE", help="Set a config value")
    p.add_argument("--repo", help="Target repo (owner/repo)")

    p = sub.add_parser("enroll", help="Enroll a repo")
    p.add_argument("--repo", required=True, help="Target repo (owner/repo)")
    p.add_argument("--dir", required=True, help="Local checkout path")
    p.add_argument("--project-id", required=True, help="Antigravity project ID")
    p.add_argument("--path", help="PATH for sidecar env (default: current PATH)")

    p = sub.add_parser("unenroll", help="Unenroll a repo")
    p.add_argument("--repo", required=True, help="Target repo (owner/repo)")

    p = sub.add_parser("reset", help="Clear stuck state")
    p.add_argument("--cooldowns", action="store_true")
    p.add_argument("--locks", action="store_true")
    p.add_argument("--dispatches", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--repo", help="Target repo, or all if omitted")

    p = sub.add_parser("labels", help="Sync labels to GitHub")
    p.add_argument("--sync", action="store_true", required=True)
    p.add_argument("--repo", required=True, help="Target repo (owner/repo)")

    sub.add_parser("refresh-sidecars", help="Refresh sidecar.json for all repos (preserves values)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "status": cmd_status,
        "logs": cmd_logs,
        "config": cmd_config,
        "enroll": cmd_enroll,
        "unenroll": cmd_unenroll,
        "reset": cmd_reset,
        "labels": cmd_labels,
        "refresh-sidecars": cmd_refresh,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
