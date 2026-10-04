---
name: scanning-infra-wo0b
description: Handoff instructions for Work Order 0b — Raw-vs-rendered content normalisation in safe_ingest.py
complexity: standard
---

# WO0b — Raw-vs-Rendered Content Normalisation

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Add a "what the maintainer sees is what the agent gets" normalisation step in `safe_ingest.py`. When a maintainer approves an issue on GitHub, they see the *rendered* markdown. But the agent receives *raw* markdown, which can contain content invisible in the rendered view — HTML comments, invisible Unicode characters, zero-width joiners, bidi overrides. This is a prompt injection vector: adversarial instructions can be hidden in content the maintainer doesn't see.

## Strict Boundary Box
- **ONLY** modify `scripts/safe_ingest.py` and `tests/test_safe_ingest.py`
- Do NOT touch `pre_tool_gate.py`, `github_url_gate.py`, config files, dispatchers, or skills
- Do NOT change the function signatures of existing public functions
- Do NOT modify the trust-gating logic (approval evaluation, timestamp checking)

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external dependencies — stdlib only
- Use only `re` and Python's built-in `str` methods for normalisation

## Line-Item Instructions

- [ ] `scripts/safe_ingest.py` — Create a new function `normalise_visible_content(text: str) -> str` that:
  1. **Strips HTML comments** (`<!-- ... -->`), EXCEPT for the narrow allowlist patterns used by the dispatchers:
     - `<!-- agy_arch_conv_id: ... -->`
     - `<!-- agy_dev_conv_id: ... -->`
     These exact tag shapes must be preserved. All other HTML comments are stripped.
  2. **Strips Unicode tag characters** (U+E0000–U+E007F — "ASCII smuggling" range)
  3. **Strips zero-width characters**: U+200B (zero-width space), U+200C (zero-width non-joiner), U+200D (zero-width joiner), U+FEFF (zero-width no-break space / BOM)
  4. **Strips bidi override characters**: U+202A–U+202E (LRE, RLE, PDF, LRO, RLO), U+2066–U+2069 (LRI, RLI, FSI, PDI)
  5. Returns the cleaned string

- [ ] `scripts/safe_ingest.py` — Create a companion function `detect_hidden_content(raw: str, normalised: str) -> bool` that returns `True` if the raw and normalised text differ materially (i.e., non-whitespace content was stripped). This is used to flag items for human attention but does NOT block ingestion (defence-in-depth, not primary gate).

- [ ] `scripts/safe_ingest.py` — In `evaluate_and_sanitize_item()`, after the trust decision is positive but before `redact_leaks_from_item`, apply `normalise_visible_content()` to the `title` and `body` fields of the sanitised item. Also apply it to each comment/review `body` in the filtered comments. If `detect_hidden_content()` returns `True` for any field, add a key `"has_hidden_content": true` to the sanitised item dict.

- [ ] `tests/test_safe_ingest.py` — Add tests:
  1. Test that HTML comments are stripped: `"Hello <!-- hidden instruction --> world"` → `"Hello  world"`
  2. Test that AGY conversation ID tags are preserved: `"<!-- agy_dev_conv_id: abc123 -->"` stays intact
  3. Test that Unicode tag characters (U+E0001) are stripped
  4. Test that zero-width characters are stripped
  5. Test that bidi overrides are stripped
  6. Test that `detect_hidden_content` returns `True` when content was stripped and `False` when nothing changed
  7. Test that `evaluate_and_sanitize_item` sets `has_hidden_content: true` on an item with hidden content (trusted author path)

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass (existing + new).

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly. The Principal Architect will triage and file them under the correct author identity.
