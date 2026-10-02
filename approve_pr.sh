#!/bin/bash

# 1. Add approved label
gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --add-label "bug-fixer:architect:approved"

# 2. Mark PR ready (if draft)
gh pr ready 28 --repo matthewmcneill/bug-fixer-loop-plugin 2>/dev/null || true

# 3. Remove needs-iteration
gh pr edit 28 --repo matthewmcneill/bug-fixer-loop-plugin --remove-label "bug-fixer:engineer:needs-iteration" 2>/dev/null || true

# 4. Post comment
COMMENT="[ARCHITECT] Approved:
- Architectural design and module layering verified.
- Pointer safety and memory lifecycle validated (no leaks, dangling pointers, or unsafe casts).
- Architectural constraints and execution context requirements satisfied.
- PR marked ready for human maintainer review."
gh pr comment 28 --repo matthewmcneill/bug-fixer-loop-plugin -b "$COMMENT"

