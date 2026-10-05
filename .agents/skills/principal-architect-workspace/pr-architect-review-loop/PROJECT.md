# Project: Architect Review Loop

## Architectural Context
The `bug-fixer-loop-plugin` currently implements a 3-loop system (Triage, Fix, Test, PR) allowing the agent to automatically fix bugs. However, without a dedicated review phase, the developer agent may accrue technical debt by focusing solely on making tests pass rather than structural correctness.

This project implements an automated, adversarial **Principal Architect Review Loop**. Before a human reviews a PR, a separate Architect agent reviews the Developer agent's output. 
Crucially, this loop is **Stateless and Context-Cached**:
- The Developer agent embeds its `conversation_id` in the PR body.
- The Architect agent embeds its `conversation_id` in the PR body upon rejecting.
- Both agents can be re-ignited by the Python sidecars by reading the PR body, seamlessly reloading their previous context utilizing Gemini Context Caching.

## Modules & Components
- `skills/bug-fixer-tick`: The developer agent.
- `skills/architect-review`: The new architect agent.
- `scripts/architect_dispatcher.py`: The new sidecar to orchestrate the Architect agent.
- `scripts/tick_dispatcher.py`: The existing sidecar updated to orchestrate iteration.

## Work Order Ledger
- **[X] [wo1-developer-skill](wo1-developer-skill/WORKORDER.md):** Update the `bug-fixer-tick/SKILL.md` to open PRs as Drafts and inject `agy_dev_conv_id` as a hidden HTML comment.
- **[X] [wo2-architect-skill](wo2-architect-skill/WORKORDER.md):** Create `skills/architect-review/SKILL.md` to define the adversarial review logic and PR approval/rejection mechanics.
- **[X] [wo3-architect-sidecar](wo3-architect-sidecar/WORKORDER.md):** Create `scripts/architect_dispatcher.py` to poll GitHub, spawn new Architect sessions, or resume existing ones based on PR body IDs.
- **[X] [wo4-iteration-loop](wo4-iteration-loop/WORKORDER.md):** Update `scripts/tick_dispatcher.py` to detect `needs-iteration` PRs, extract the `agy_dev_conv_id`, and resume the Developer agent to fix the Architect's findings.

## Session Log
- **WO1 (Developer Skill):** Completed. The Developer agent is now instructed to open Draft PRs and embed its `agy_dev_conv_id` as an HTML comment, providing the foundation for stateless re-ignition.
- **WO2 (Architect Skill):** Completed. The `architect-review` skill was created, establishing the adversarial persona, the approval mechanism (`gh pr ready`), and the rejection mechanism (injecting `agy_arch_conv_id` and applying the `needs-iteration` label).
- **WO3 (Architect Dispatcher):** Completed. `architect_dispatcher.py` sidecar implemented and registered. It polls for unreviewed Drafts, extracts the Architect's context ID (if present), and resumes/spawns the `architect-review` agent.
- **WO4 (Iteration Loop):** Completed. `tick_dispatcher.py` updated to detect `needs-iteration` labels, extract the Developer's context ID, and resume the Developer agent with the Architect's comments, correctly incrementing slot concurrency.

### Project Wrap-Up
The **Architect Review Loop** has been successfully designed, decomposed, and implemented across 4 stacked branches. The system is now fully stateless and leverages Gemini Context Caching for efficient, multi-turn adversarial reviews. The `bug-fixer-loop-plugin` is now capable of producing deeply vetted, architecturally sound PRs automatically.



