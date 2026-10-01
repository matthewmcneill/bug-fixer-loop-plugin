#!/usr/bin/env bash
set -euo pipefail

# migrate_labels.sh — Rename bug-fixer labels on a target repo
# Usage: ./scripts/migrate_labels.sh <owner/repo> [--dry-run]
#
# This script renames old-style bug-fixer labels to the new
# bug-fixer:<actor>:<name> routed-segment namespace.
# Uses `gh label create --force` which renames in-place.

TARGET_REPO="${1:-}"
if [[ -z "$TARGET_REPO" ]]; then
  echo "Usage: migrate_labels.sh <owner/repo> [--dry-run]"
  exit 1
fi

DRY_RUN=false
if [[ "${2:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

declare -A RENAME_MAP=(
  ["auto-fixable"]="bug-fixer:engineer:needs-fix"
  ["human-required"]="bug-fixer:human:required"
  ["needs-approach-review"]="bug-fixer:human:needs-approach-review"
  ["project-candidate"]="bug-fixer:human:project-candidate"
  ["input-needed"]="bug-fixer:human:input-needed"
  ["status:in-progress"]="bug-fixer:engineer:in-progress"
  ["status:awaiting-review"]="bug-fixer:architect:awaiting-review"
  ["bug-fixer"]="bug-fixer:engineer:pr"
  ["bug-fixer-health"]="bug-fixer:system:health"
  ["spec-change-request"]="bug-fixer:human:spec-change-request"
  ["needs-iteration"]="bug-fixer:engineer:needs-iteration"
  ["architect-approved"]="bug-fixer:architect:approved"
  ["needs-human-intervention"]="bug-fixer:human:needs-intervention"
  ["discovered-in-review"]="bug-fixer:triage:discovered-in-review"
  ["complexity:mechanical"]="bug-fixer:complexity:mechanical"
  ["complexity:standard"]="bug-fixer:complexity:standard"
  ["complexity:architect"]="bug-fixer:complexity:architect"
)

declare -A LABELS_META=(
  ["bug-fixer:engineer:needs-fix"]="0E8A16:Issues eligible for autonomous fixing"
  ["bug-fixer:human:required"]="D93F0B:Issues requiring human intervention or architectural review"
  ["bug-fixer:human:needs-approach-review"]="FBCA04:Fix approach requires human maintainer sign-off"
  ["bug-fixer:human:project-candidate"]="5319E7:Large multi-step initiative candidate"
  ["bug-fixer:human:input-needed"]="D4C5F9:Waiting for human input or clarification"
  ["bug-fixer:engineer:in-progress"]="1D76DB:Currently being worked on by an agent or human"
  ["bug-fixer:engineer:pr"]="0075CA:Managed by autonomous bug-fixer loop"
  ["bug-fixer:system:health"]="C5DEF5:Bug fixer health, metrics, or diagnostic report"
  ["bug-fixer:human:spec-change-request"]="F9D0C4:Proposed modification to design specification"
  ["bug-fixer:engineer:needs-iteration"]="E4E669:PR needs engineering rework after architect review"
  ["bug-fixer:architect:approved"]="0E8A16:Architect approved the PR for merge"
  ["bug-fixer:architect:awaiting-review"]="BFD4F2:PR ready for architect review"
  ["bug-fixer:human:needs-intervention"]="D93F0B:Architect escalated — needs human resolution"
  ["bug-fixer:triage:discovered-in-review"]="d4c5f9:Auto-filed during PR review, requires human approval before auto-fix"
  ["bug-fixer:complexity:mechanical"]="ffffff:Model tier: rote/deterministic work"
  ["bug-fixer:complexity:standard"]="ffffff:Model tier: standard engineering work"
  ["bug-fixer:complexity:architect"]="ffffff:Model tier: needs architect-grade reasoning"
)

for old_label in "${!RENAME_MAP[@]}"; do
  new_label="${RENAME_MAP[$old_label]}"
  meta="${LABELS_META[$new_label]:-ededed:Managed by bug-fixer-loop}"
  color="${meta%%:*}"
  description="${meta#*:}"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would rename: '${old_label}' → '${new_label}' (${color})"
  else
    echo "Renaming: '${old_label}' → '${new_label}'"
    # Create new label (in-place if same name, or creates new one)
    gh label create "${new_label}" --repo "${TARGET_REPO}" --force \
      --color "${color}" --description "${description}" 2>/dev/null || true
    
    # Delete the old label if it differs from the new one (case-insensitive check)
    if [[ "${old_label,,}" != "${new_label,,}" ]]; then
      gh label delete "${old_label}" --repo "${TARGET_REPO}" --yes 2>/dev/null || true
    fi
  fi
done

echo ""
echo "Migration complete. Verify at: https://github.com/${TARGET_REPO}/labels"
