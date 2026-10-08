# Contributing to Bug Fixer Loop

Thanks for your interest in contributing! This guide covers how to set up a development environment, run tests, submit changes, and understand the project's architecture well enough to make meaningful contributions.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Architecture](#project-architecture)
- [Running Tests](#running-tests)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Security Considerations](#security-considerations)
- [Reporting Issues](#reporting-issues)
- [Documentation](#documentation)

---

## Code of Conduct

Be respectful, constructive, and professional. We're building safety-critical tooling that runs autonomously — the same care should extend to how we collaborate.

---

## Getting Started

### Prerequisites

- **Python 3.10+**
- **Git** with GitHub CLI (`gh`) installed and authenticated
- **Google Antigravity** (`agy`) — the plugin runtime
- **rsync** — used by installation scripts
- A basic understanding of the [Antigravity plugin system](docs/antigravity-hooks-reference.md)

### Fork & Clone

```bash
# Fork via GitHub, then:
git clone https://github.com/<your-username>/bug-fixer-loop-plugin.git
cd bug-fixer-loop-plugin
```

---

## Development Setup

The project is pure Python with no external dependencies beyond the standard library. No virtual environment or `pip install` is required for development — the test suite is self-contained.

```bash
# Verify everything works
python3 -m unittest discover -s tests -v
```

If all 258+ tests pass, you're ready to contribute.

### Linting

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting:

```bash
# Install ruff (if you don't have it)
pip install ruff

# Lint the entire project
ruff check .

# Auto-fix safe issues
ruff check --fix .
```

Configuration lives in [`pyproject.toml`](pyproject.toml) — line length is 100, targeting Python 3.10.

---

## Project Architecture

Before making changes, understand the split-daemon architecture. The full guide is in [`docs/plugin-architecture.md`](docs/plugin-architecture.md), but here's the essential map:

```
bug-fixer-loop-plugin/
├── daemon/                          ← Installed to ~/.gemini/config/plugins/
│   ├── plugin.json                  ← Global daemon manifest
│   ├── hooks.json                   ← PreToolUse/PostToolUse security hooks
│   ├── scripts/
│   │   ├── tick.py                  ← Heartbeat entry point (runs every 5 min)
│   │   └── manage.py               ← Management CLI (status, logs, enroll, etc.)
│   └── workspace-plugin/bug-fixer/  ← Shared workspace plugin bundle
│       ├── scripts/                 ← Core logic (gates, dispatchers, ingestion)
│       ├── rules/                   ← Agent safety rules (AGENTS.md)
│       ├── skills/                  ← Agent skill definitions
│       └── agents/                  ← Agent profiles (leak-auditor)
├── scripts/                         ← User-facing install/enroll/unenroll scripts
├── tests/                           ← Unit tests (258+ tests)
└── docs/                            ← Architecture and reference documentation
```

### Key Modules

| Module | Location | Responsibility |
|--------|----------|---------------|
| `pre_tool_gate.py` | `workspace-plugin/bug-fixer/scripts/` | PreToolUse hook — structural command analysis, forbidden operation blocking, leak scanning |
| `safe_ingest.py` | `workspace-plugin/bug-fixer/scripts/` | Safe GitHub content ingestion — approval gate, TOCTOU protection, secret redaction |
| `safe_post.py` | `workspace-plugin/bug-fixer/scripts/` | Egress leak scanning for outbound GitHub content |
| `shell_analysis.py` | `workspace-plugin/bug-fixer/scripts/` | Recursive shell command parser used by `pre_tool_gate.py` |
| `engineering_dispatcher.py` | `workspace-plugin/bug-fixer/scripts/` | Engineering loop control plane — claim management, conversation spawning |
| `triage_dispatcher.py` | `workspace-plugin/bug-fixer/scripts/` | Triage loop control plane — issue classification routing |
| `architect_dispatcher.py` | `workspace-plugin/bug-fixer/scripts/` | PR review loop control plane — adversarial review orchestration |
| `trajectory_dispatcher.py` | `workspace-plugin/bug-fixer/scripts/` | Daily quality audit — test quality, leak receipts, failure patterns |
| `bugfixer_api.py` | `workspace-plugin/bug-fixer/scripts/` | Consumer hook API — session context for autonomous vs interactive detection |
| `tick_receiver.py` | `workspace-plugin/bug-fixer/scripts/` | Unified tick handler — interval-based dispatch delegation |
| `manage.py` | `daemon/scripts/` | Management CLI — status, logs, config, enroll/unenroll |

---

## Running Tests

The test suite uses Python's built-in `unittest` framework with no external test runners required:

```bash
# Run everything
python3 -m unittest discover -s tests -v

# Run a specific module
python3 -m unittest tests/test_pre_tool_gate.py -v

# Run a specific test class
python3 -m unittest tests.test_safe_ingest.TestSafeIngest -v

# Run a specific test method
python3 -m unittest tests.test_pre_tool_gate.TestPreToolGate.test_blocks_gh_pr_merge -v
```

### Test Categories

| Test File | What It Tests |
|-----------|--------------|
| `test_pre_tool_gate.py` | PreToolUse gate decisions — command blocking, file protection, leak detection |
| `test_gate_b_adversarial.py` | Adversarial prompt injection and bypass attempts against the gate |
| `test_safe_ingest.py` | Ingestion pipeline — approval gates, TOCTOU, secret redaction |
| `test_safe_post.py` | Egress leak scanning for outbound content |
| `test_injection_scanner.py` | Prompt injection detection patterns |
| `test_github_url_gate.py` | URL fetch gate — GitHub URL redirect and leak-pattern blocking |
| `test_bugfixer_api.py` | Consumer hook API — session context resolution |
| `test_architect_dispatcher.py` | Architect review loop control plane |
| `test_dispatcher_models.py` | Model tier selection and routing |
| `test_engineering_dispatcher_prompts.py` | Engineering prompt generation |
| `test_liveness.py` | Liveness and health monitoring |
| `test_import_hygiene.py` | Import cleanliness — ensures no leaked runtime dependencies |
| `test_wo2a.py`, `test_wo2b.py`, `test_wo3.py` | Work-order integration tests (setup, config, tick lifecycle) |

### Writing Tests

- Tests must be **self-contained** — no network calls, no filesystem side-effects outside of `tempfile` directories
- Use `unittest.mock.patch` to isolate external dependencies (`subprocess`, `os.environ`, file I/O)
- Security gate tests (`test_pre_tool_gate.py`, `test_gate_b_adversarial.py`) intentionally contain **synthetic secrets** as test fixtures. These are allowlisted in [`.betterleaks.toml`](.betterleaks.toml) — don't remove the allowlist entries
- When adding a new test file, ensure it's discoverable by `unittest discover` (it must start with `test_` and contain a class inheriting from `unittest.TestCase`)

---

## Making Changes

### Branch Naming

Follow the existing convention:

```
fix/<issue-number>-<short-description>    # Bug fixes
feat/<short-description>                  # New features
```

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
fix: block gh pr merge with --auto flag
feat: add trajectory review loop
security: wrap user-controlled content in prompt delimiters
chore: update .betterleaks.toml allowlist
docs: update configuration reference
```

### What to Change (and What Not To)

**Good first contributions:**
- Improving test coverage for edge cases in `pre_tool_gate.py` or `safe_ingest.py`
- Adding new adversarial bypass test cases to `test_gate_b_adversarial.py`
- Documentation improvements and corrections
- Bug fixes with clear reproduction steps

**Requires discussion first (open an issue):**
- Changes to the 12 safety invariants
- New dispatchers or loop types
- Modifications to the security gate logic
- Changes to the hook schema or plugin manifest
- Architectural changes to the split-daemon pattern

**Protected — do not modify without security review:**
- `pre_tool_gate.py` — the core security gate
- `safe_ingest.py` — the ingestion boundary
- `safe_post.py` — the egress boundary
- `shell_analysis.py` — the command parser
- `integrity_monitor.py` — runtime self-defense
- `.betterleaks.toml` — leak detection allowlist

---

## Pull Request Process

1. **Ensure all tests pass** — PRs with failing tests will not be reviewed
2. **Run the linter** — `ruff check .` should produce no errors
3. **One concern per PR** — keep changes atomic and focused
4. **Describe the "why"** — the PR description should explain the problem and the rationale, not just list the changes
5. **Link related issues** — use `Resolves #<number>` or `Related to #<number>`
6. **Security-sensitive changes** — flag explicitly in the PR title with a `security:` prefix

### Review Expectations

- All PRs require at least one review
- Security gate changes (`pre_tool_gate.py`, `safe_ingest.py`, `safe_post.py`) require additional scrutiny and adversarial test coverage
- Expect questions about edge cases — this is safety-critical software

---

## Security Considerations

Bug Fixer Loop runs **autonomously** with access to your codebase and GitHub API. Security isn't a feature — it's the foundation. If you're contributing to security-related code:

- **Assume adversarial input.** Issue titles, PR bodies, and comments are untrusted content that may contain prompt injection attempts. All ingestion must go through `safe_ingest.py`.
- **Fail closed.** If the gate can't determine whether an operation is safe, it must deny. Never fail open.
- **Test the bypass.** For every security rule you add, write a test in `test_gate_b_adversarial.py` that attempts to circumvent it.
- **No auto-merge, ever.** This is the project's most fundamental invariant. Changes that could weaken this guarantee will be rejected.

---

## Reporting Issues

### Bugs

Open a GitHub issue with:
- Steps to reproduce
- Expected vs actual behaviour
- Relevant log output (from `manage.py logs <loop>`)
- Your Antigravity version (`agy --version`)

### Security Vulnerabilities

If you discover a security vulnerability — especially one that could allow the agent to bypass safety invariants, leak secrets, or auto-merge — **do not open a public issue**. Instead, contact the maintainer directly via the security advisory process on GitHub.

---

## Documentation

The project maintains documentation at multiple levels:

| Document | Purpose | When to Update |
|----------|---------|---------------|
| [README.md](README.md) | User-facing overview, quick start, configuration | When adding user-visible features or changing install steps |
| [docs/plugin-architecture.md](docs/plugin-architecture.md) | Deep architecture guide — internals, patterns, anti-patterns | When changing dispatcher logic, hook wiring, or install scripts |
| [docs/label-taxonomy.md](docs/label-taxonomy.md) | Label registry, state machines, routing tables | When adding/modifying labels or changing dispatcher routing |
| [docs/antigravity-hooks-reference.md](docs/antigravity-hooks-reference.md) | Hook system reference, plugin discovery | When discovering new Antigravity hook behaviours |
| `CONTRIBUTING.md` (this file) | How to contribute | When changing development process or tooling |

When your change affects user-visible behaviour, update the relevant docs in the same PR.

---

## Questions?

Open a discussion or issue on GitHub. We're happy to help new contributors find their way around the codebase.
