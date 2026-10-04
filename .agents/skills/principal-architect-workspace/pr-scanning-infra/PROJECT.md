# Scanning Infrastructure Hardening — PROJECT.md

**Parent Issue:** [#22 — Investigate external scanning integrations](https://github.com/matthewmcneill/bug-fixer-loop-plugin/issues/22)
**Base Branch:** `main`
**PR Title:** `[Scanning Infra]: Harden scanning pipeline — redaction, normalisation, egress gate, Betterleaks backend`

## Guiding Principles (from Issue #22 consensus)

1. Identity-gating remains the primary defence. Content scanning is defence-in-depth.
2. Deterministic containment before probabilistic detection. Free, local, verifiable controls first.
3. Scanners never become a leak channel. Raw match text is never printed; nothing makes network calls with a found secret by default.
4. Anything that can silence a finding is protected (configs, ignore files, env vars).
5. The core stays stdlib-only. External tools called as subprocesses, fall back gracefully.

## Status

- **Human approval of decomposition:** ✅ APPROVED (2026-10-04T20:05:07+01:00)
- **Current WO:** WO2

## Work Order Ledger

- **[x] [wo0a-redact-violations](wo0a-redact-violations/WORKORDER.md):** Redact matched secrets in `scan_repository()` violation output (~10 lines) ✅
- **[x] [wo0b-content-normalisation](wo0b-content-normalisation/WORKORDER.md):** Raw-vs-rendered normalisation in `safe_ingest.evaluate_and_sanitize_item()` (~50 lines + tests) ✅
- **[x] [wo1-egress-leak-gate](wo1-egress-leak-gate/WORKORDER.md):** Egress leak gate on outbound `gh` bodies in `pre_tool_gate` (~80 lines + tests) ✅
- **[ ] [wo2-scan-backend-interface](wo2-scan-backend-interface/WORKORDER.md):** `ScanBackend` interface with scope (`staged`/`tree`/`history`) + backend recorded in receipt (~100 lines)
- **[ ] [wo3-betterleaks-backend](wo3-betterleaks-backend/WORKORDER.md):** Betterleaks v2 backend + regex fallback with degraded-mode reporting (~80 lines + tests)

## Concurrency & Conflict Analysis

All five WOs are **serialised** (stacked). They touch overlapping files:
- WO0a + WO2 + WO3 all touch `scripts/pre_tool_gate.py`
- WO0b touches `scripts/safe_ingest.py`
- WO1 touches `scripts/pre_tool_gate.py`

No parallel execution — strict sequential stacking.

## Modules & Components

| Module | Files | WOs |
|--------|-------|-----|
| `pre_tool_gate` | `scripts/pre_tool_gate.py` | 0a, 1, 2, 3 |
| `safe_ingest` | `scripts/safe_ingest.py` | 0b |
| `config` | `.agents/plugins/bug-fixer-loop-workspace/config.json` | 2, 3 |
| `tests` | `tests/test_pre_tool_gate.py`, `tests/test_safe_ingest.py` | 0a, 0b, 1, 2, 3 |

## Session Log

### Session 1 — 2026-10-04 — Architecture Assessment & Planning
- Completed architecture assessment of Issue #22 (posted to issue)
- Three-way review (Antigravity, Claude Code) reached consensus on work package ordering
- Key decisions: Betterleaks v2 as default, verification off by default, egress scanning promoted to WP1, raw-vs-rendered normalisation added as WP0b
- Decomposition created, awaiting human approval to begin execution
