#!/usr/bin/env bash
# uninstall.sh — Remove all global artifacts created by install.sh
# Safe: only removes known plugin artifacts, never deletes user files
set -euo pipefail

echo "Bug Fixer Loop Plugin — Global Cleanup"
echo "========================================"

# 1. Global scripts
for f in "$HOME/.gemini/config/scripts/safe_ingest.py" \
         "$HOME/.gemini/config/scripts/pre_tool_gate.py"; do
  if [[ -f "$f" ]]; then
    chmod u+w "$f" 2>/dev/null || true
    rm -f "$f"
    echo "Removed: $f"
  fi
done

# 2. Global rules (only AGENTS.md if it contains bug-fixer content)
RULES_FILE="$HOME/.gemini/config/rules/AGENTS.md"
if [[ -f "$RULES_FILE" ]] && grep -q 'Bug Fixer Loop' "$RULES_FILE" 2>/dev/null; then
  rm -f "$RULES_FILE"
  echo "Removed: $RULES_FILE"
fi

# 3. Global skills (only bug-fixer related)
for skill in bug-fixer bug-fixer-tick bug-fixer-triage test-quality-audit trajectory-review systematic-debugging; do
  for base in "$HOME/.gemini/config/skills" "$HOME/.gemini/antigravity/skills"; do
    if [[ -d "$base/$skill" ]]; then
      rm -rf "$base/$skill"
      echo "Removed: $base/$skill"
    fi
  done
done

# 4. Global agents
for base in "$HOME/.gemini/config/agents" "$HOME/.gemini/antigravity/agents"; do
  if [[ -f "$base/leak-auditor.md" ]]; then
    rm -f "$base/leak-auditor.md"
    echo "Removed: $base/leak-auditor.md"
  fi
done

# 5. Global sidecars (all three directories)
for sidecar in bug-fixer-tick bug-fixer-triage trajectory-review; do
  for base in "$HOME/.gemini/config/sidecars" "$HOME/.gemini/antigravity/sidecars" "$HOME/.gemini/jetski/sidecars"; do
    if [[ -d "$base/$sidecar" ]]; then
      rm -rf "$base/$sidecar"
      echo "Removed: $base/$sidecar"
    fi
  done
done

# 6. Global hooks.json (only if it contains bug-fixer hooks)
HOOKS_FILE="$HOME/.gemini/config/hooks.json"
if [[ -f "$HOOKS_FILE" ]] && grep -q 'bug-fixer' "$HOOKS_FILE" 2>/dev/null; then
  # Try to remove just the bug-fixer keys, leaving other hooks intact
  python3 -c "
import json, sys
try:
    with open('$HOOKS_FILE') as f:
        hooks = json.load(f)
    for key in list(hooks.keys()):
        if 'bug-fixer' in key:
            del hooks[key]
    if hooks:
        with open('$HOOKS_FILE', 'w') as f:
            json.dump(hooks, f, indent=2)
            f.write('\n')
    else:
        import os
        os.remove('$HOOKS_FILE')
    print(f'Cleaned bug-fixer hooks from: $HOOKS_FILE')
except Exception as e:
    print(f'Warning: Could not clean hooks: {e}', file=sys.stderr)
" 2>/dev/null || true
fi

echo ""
echo "Done. All global bug-fixer artifacts removed."
echo "The plugin source at $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) is untouched."
