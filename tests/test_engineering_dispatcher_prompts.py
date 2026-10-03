"""Unit tests for engineering_dispatcher prompt generation.

Verifies that prompts generated for agents instruct them to apply both
bug-fixer:system:pr and bug-fixer:architect:awaiting-review to the PR,
and transition the issue, preventing PRs from getting stuck in limbo.
"""

import unittest
from pathlib import Path


class TestEngineeringDispatcherPrompts(unittest.TestCase):
    def setUp(self):
        self.script_path = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "engineering_dispatcher.py"
        )
        self.content = self.script_path.read_text()

    def test_pr_label_in_fresh_fix_prompt(self):
        """Ensure fresh-fix prompt instructs agent to label PR with awaiting-review and system:pr."""
        self.assertIn(
            "--add-label bug-fixer:system:pr,bug-fixer:architect:awaiting-review",
            self.content,
        )
        self.assertIn(
            "Add labels to the PR:  gh pr edit <PR> --repo {TARGET_REPO} --add-label bug-fixer:system:pr,bug-fixer:architect:awaiting-review",
            self.content,
        )

    def test_pr_label_in_iteration_prompts(self):
        """Ensure PR iteration prompts instruct agent to add awaiting-review when removing needs-iteration."""
        self.assertIn(
            "--remove-label bug-fixer:engineer:needs-iteration --add-label bug-fixer:architect:awaiting-review",
            self.content,
        )

    def test_issue_transition_in_prompt(self):
        """Ensure fresh-fix and reattach prompts instruct transitioning issue to awaiting-review."""
        self.assertIn(
            "gh issue edit {issue_num} --repo {TARGET_REPO} --add-label bug-fixer:architect:awaiting-review --remove-label bug-fixer:engineer:in-progress",
            self.content,
        )


if __name__ == "__main__":
    unittest.main()
