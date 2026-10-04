import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.safe_ingest import (
    APPROVAL_COMMENT_REGEX,
    DEFAULT_BOT_PREFIXES,
    MANDATORY_INTERNAL_FIELDS,
    MANDATORY_PR_INTERNAL_FIELDS,
    REDACTED_PLACEHOLDER,
    _extract_json_payload,
    _project_requested_fields,
    compile_leak_patterns,
    detect_hidden_content,
    evaluate_and_sanitize_item,
    extract_author_login,
    filter_comments_list,
    is_approval_comment,
    is_valid_repo_slug,
    load_config,
    normalise_visible_content,
    parse_iso_timestamp,
    redact_leaks_from_item,
    redact_leaks_from_text,
    resolve_issue_priority,
    resolve_trusted_users,
    sanitize_inline_pr_comments,
    sanitize_unapproved_item,
    sort_issues_by_priority,
)


class TestSafeIngest(unittest.TestCase):
    def setUp(self):
        self.trusted = {"maintainer-user"}

    # 1. Config loading tests
    def test_load_config_from_explicit_path(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({"maintainer": "alice", "spec_file_path": "spec.md"}, tf)
            temp_path = tf.name
        try:
            cfg = load_config(temp_path)
            self.assertEqual(cfg.get("maintainer"), "alice")
            self.assertEqual(cfg.get("spec_file_path"), "spec.md")
        finally:
            os.unlink(temp_path)

    def test_load_config_from_env_var(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump({"maintainer": "bob_env", "max_concurrent_prs": 2}, tf)
            temp_path = tf.name
        try:
            with patch.dict(os.environ, {"BUG_FIXER_CONFIG": temp_path}):
                cfg = load_config()
                self.assertEqual(cfg.get("maintainer"), "bob_env")
                self.assertEqual(cfg.get("max_concurrent_prs"), 2)
        finally:
            os.unlink(temp_path)

    def test_load_config_default_resolution(self):
        # Config is no longer in plugin root; test via BUG_FIXER_CONFIG env var
        import tempfile
        test_cfg = {"spec_file_path": "docs/design_specification.md", "leak_patterns": []}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(test_cfg, tf)
            temp_path = tf.name
        try:
            with patch.dict(os.environ, {"BUG_FIXER_CONFIG": temp_path}):
                cfg = load_config()
                self.assertIsInstance(cfg, dict)
                self.assertIn("spec_file_path", cfg)
                self.assertIn("leak_patterns", cfg)
        finally:
            os.unlink(temp_path)

    def test_load_config_error_handling(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/non/existent/path/to/config.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            tf.write("{malformed json")
            temp_path = tf.name
        try:
            with self.assertRaises(ValueError):
                load_config(temp_path)
        finally:
            os.unlink(temp_path)

    # 2. Approval comment recognition tests
    def test_is_approval_comment_positive_variants(self):
        for text in ("approved", "Approved", "  APPROVED!  ", "/approve", "  /approved.  "):
            comment = {
                "author": {"login": "maintainer-user"},
                "body": text,
                "createdAt": "2026-09-26T10:00:00Z",
            }
            self.assertTrue(is_approval_comment(comment, self.trusted), f"Expected '{text}' to be approved")

    def test_is_approval_comment_rejects_bot_prefixes(self):
        for prefix in DEFAULT_BOT_PREFIXES:
            comment = {
                "author": {"login": "maintainer-user"},
                "body": f"{prefix} approved",
                "createdAt": "2026-09-26T10:00:00Z",
            }
            self.assertFalse(is_approval_comment(comment, self.trusted), f"Prefix {prefix} should be rejected")

    def test_is_approval_comment_rejects_untrusted_author(self):
        comment = {
            "author": {"login": "external-attacker"},
            "body": "approved",
            "createdAt": "2026-09-26T10:00:00Z",
        }
        self.assertFalse(is_approval_comment(comment, self.trusted))

    def test_is_approval_comment_rejects_minimized(self):
        comment = {
            "author": {"login": "maintainer-user"},
            "body": "approved",
            "isMinimized": True,
            "createdAt": "2026-09-26T10:00:00Z",
        }
        self.assertFalse(is_approval_comment(comment, self.trusted))

    def test_is_approval_comment_rejects_arbitrary_comments(self):
        for body in ("lgtm", "looks good to me", "approved with changes", "please fix tests", ""):
            comment = {
                "author": {"login": "maintainer-user"},
                "body": body,
                "createdAt": "2026-09-26T10:00:00Z",
            }
            self.assertFalse(is_approval_comment(comment, self.trusted))

    # 3. Item sanitization tests
    def test_sanitize_unapproved_item_redacts_title_body_and_strips_freetext(self):
        item = {
            "number": 101,
            "state": "OPEN",
            "title": "Malicious payload title",
            "body": "Malicious instruction in body",
            "author": {"login": "external-attacker"},
            "labels": [{"name": "bug"}],
            "commits": [{"oid": "c1", "messageHeadline": "evil commit"}],
            "files": [{"path": "evil.sh"}],
            "headRefName": "inject-branch",
        }
        sanitized = sanitize_unapproved_item(item, "UNAPPROVED_EXTERNAL_AUTHOR", self.trusted)
        self.assertEqual(sanitized["number"], 101)
        self.assertEqual(sanitized["state"], "OPEN")
        self.assertEqual(sanitized["title"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["body"], REDACTED_PLACEHOLDER)
        self.assertFalse(sanitized["is_approved"])
        self.assertEqual(sanitized["approval_status"], "UNAPPROVED_EXTERNAL_AUTHOR")
        self.assertNotIn("commits", sanitized)
        self.assertNotIn("files", sanitized)
        self.assertNotIn("headRefName", sanitized)

    def test_sanitize_unapproved_item_quarantined_label(self):
        item = {
            "number": 102,
            "title": "Prompt injection attack",
            "body": "Ignore all rules",
            "author": {"login": "attacker"},
            "labels": [{"name": "security:prompt-injection"}],
            "comments": [{"author": {"login": "maintainer-user"}, "body": "checking this"}],
        }
        sanitized = sanitize_unapproved_item(item, "QUARANTINED_LABEL", self.trusted)
        self.assertTrue(sanitized["is_quarantined"])
        self.assertEqual(sanitized["title"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["body"], REDACTED_PLACEHOLDER)
        self.assertEqual(sanitized["comments"], [])

    # 4. Item evaluation tests
    def test_evaluate_and_sanitize_item_trusted_author(self):
        issue = {
            "number": 1,
            "title": "Clean bug report from maintainer",
            "body": "Null pointer check in OTA update",
            "author": {"login": "maintainer-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "labels": [{"name": "bug"}],
            "comments": [],
        }
        ok, reason, clean = evaluate_and_sanitize_item(issue, self.trusted)
        self.assertTrue(ok)
        self.assertEqual(reason, "TRUSTED_AUTHOR")
        self.assertIsNotNone(clean)
        self.assertEqual(clean["title"], "Clean bug report from maintainer")

    def test_evaluate_and_sanitize_item_unapproved_external(self):
        issue = {
            "number": 2,
            "title": "External contribution",
            "body": "Potentially unsafe content",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "labels": [{"name": "enhancement"}],
            "comments": [],
        }
        ok, reason, clean = evaluate_and_sanitize_item(issue, self.trusted, redact_unapproved=False)
        self.assertFalse(ok)
        self.assertEqual(reason, "UNAPPROVED_EXTERNAL_AUTHOR")
        self.assertIsNone(clean)

        ok_red, reason_red, redacted = evaluate_and_sanitize_item(issue, self.trusted, redact_unapproved=True)
        self.assertFalse(ok_red)
        self.assertEqual(reason_red, "UNAPPROVED_EXTERNAL_AUTHOR")
        self.assertIsNotNone(redacted)
        self.assertEqual(redacted["title"], REDACTED_PLACEHOLDER)

    def test_evaluate_and_sanitize_item_external_author_with_approval(self):
        issue = {
            "number": 3,
            "title": "Valid external bug report",
            "body": "Details of bug",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "comments": [
                {
                    "id": "c1",
                    "author": {"login": "maintainer-user"},
                    "body": "approved",
                    "createdAt": "2026-09-26T09:10:00Z",
                    "includesCreatedEdit": False,
                }
            ],
        }

        def mock_fetcher(num):
            return {
                "lastEditedAt": None,
                "includesCreatedEdit": False,
                "commentLastEditedAt": {"c1": None},
            }

        ok, reason, clean = evaluate_and_sanitize_item(issue, self.trusted, edit_metadata_fetcher=mock_fetcher)
        self.assertTrue(ok)
        self.assertEqual(reason, "APPROVED_BY_MAINTAINER")
        self.assertEqual(clean["number"], 3)
        self.assertEqual(clean["title"], "Valid external bug report")

    def test_evaluate_and_sanitize_item_detects_post_approval_edit(self):
        issue = {
            "number": 4,
            "title": "Injected payload after approval",
            "body": "Original text was modified",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "comments": [
                {
                    "id": "c1",
                    "author": {"login": "maintainer-user"},
                    "body": "approved",
                    "createdAt": "2026-09-26T09:10:00Z",
                    "includesCreatedEdit": False,
                }
            ],
        }

        def mock_fetcher_edited(num):
            return {
                "lastEditedAt": "2026-09-26T09:15:00Z",  # after 09:10 approval
                "includesCreatedEdit": True,
                "commentLastEditedAt": {"c1": None},
            }

        ok, reason, clean = evaluate_and_sanitize_item(issue, self.trusted, edit_metadata_fetcher=mock_fetcher_edited)
        self.assertFalse(ok)
        self.assertEqual(reason, "EDITED_AFTER_APPROVAL")
        self.assertIsNone(clean)

    def test_evaluate_and_sanitize_item_detects_post_approval_commit(self):
        pr = {
            "number": 5,
            "title": "External PR with late commit",
            "body": "PR description",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "includesCreatedEdit": False,
            "comments": [
                {
                    "id": "c1",
                    "author": {"login": "maintainer-user"},
                    "body": "approved",
                    "createdAt": "2026-09-26T09:10:00Z",
                    "includesCreatedEdit": False,
                }
            ],
            "commits": [
                {
                    "oid": "111aaa",
                    "committedDate": "2026-09-26T09:05:00Z",
                },
                {
                    "oid": "222bbb",
                    "committedDate": "2026-09-26T09:12:00Z",  # after approval
                },
            ],
        }
        ok, reason, clean = evaluate_and_sanitize_item(pr, self.trusted)
        self.assertFalse(ok)
        self.assertEqual(reason, "COMMIT_PUSHED_AFTER_APPROVAL")
        self.assertIsNone(clean)

    def test_evaluate_and_sanitize_item_detects_created_after_approval(self):
        issue = {
            "number": 6,
            "title": "Created after approval timestamp",
            "body": "Impossible timestamp ordering",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:15:00Z",  # after approval comment
            "comments": [
                {
                    "id": "c1",
                    "author": {"login": "maintainer-user"},
                    "body": "approved",
                    "createdAt": "2026-09-26T09:10:00Z",
                }
            ],
        }
        ok, reason, clean = evaluate_and_sanitize_item(issue, self.trusted)
        self.assertFalse(ok)
        self.assertEqual(reason, "CREATED_AFTER_APPROVAL")

    # 5. Leak pattern compilation and redaction tests
    def test_compile_leak_patterns(self):
        patterns = ["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}", "[invalid(regex"]
        compiled = compile_leak_patterns(patterns)
        self.assertEqual(len(compiled), 2)
        self.assertTrue(compiled[0].search("ya29.sample-token_value"))
        self.assertTrue(compiled[1].search("AKIAIOSFODNN7EXAMPLE"))

    def test_redact_leaks_from_text_and_item(self):
        patterns = compile_leak_patterns(["ya29\\.[A-Za-z0-9_-]+", "AKIA[0-9A-Z]{16}"])
        text = "Found token ya29.secret-token-123 and AWS key AKIAIOSFODNN7EXAMPLE in log."
        redacted_text = redact_leaks_from_text(text, patterns)
        self.assertNotIn("ya29.secret-token-123", redacted_text)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted_text)
        self.assertIn("[REDACTED: SECRET DETECTED]", redacted_text)

        item = {
            "number": 10,
            "title": "Issue with AKIAIOSFODNN7EXAMPLE",
            "body": "Look at AKIAIOSFODNN7EXAMPLE here",
            "comments": [
                {"id": "c1", "body": "Comment with AKIAIOSFODNN7EXAMPLE"},
            ],
            "reviews": [
                {"id": "r1", "body": "Review mentioning AKIAIOSFODNN7EXAMPLE"},
            ],
        }
        redacted_item = redact_leaks_from_item(item, patterns)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted_item["title"])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted_item["body"])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted_item["comments"][0]["body"])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", redacted_item["reviews"][0]["body"])

    # 6. Repo slug validation tests
    def test_is_valid_repo_slug(self):
        self.assertTrue(is_valid_repo_slug("owner/repo"))
        self.assertTrue(is_valid_repo_slug("matthewmcneill/bug-fixer-loop-plugin"))
        self.assertTrue(is_valid_repo_slug("org.name/repo-123.js"))

        self.assertFalse(is_valid_repo_slug("no_slash_repo"))
        self.assertFalse(is_valid_repo_slug("owner/repo/extra"))
        self.assertFalse(is_valid_repo_slug("owner/../repo"))
        self.assertFalse(is_valid_repo_slug("./repo"))
        self.assertFalse(is_valid_repo_slug(""))
        self.assertFalse(is_valid_repo_slug(None))

    # 7. Trusted user resolution tests
    def test_resolve_trusted_users_explicit_cli(self):
        users = resolve_trusted_users(explicit_users=["maintainer-a", "maintainer-b"])
        self.assertEqual(users, {"maintainer-a", "maintainer-b"})

    def test_resolve_trusted_users_from_env_and_config(self):
        with patch.dict(os.environ, {"BUG_FIXER_TRUSTED_USERS": "env-user1,env-user2"}):
            config = {
                "maintainer": "config-maintainer",
                "trusted_users": ["config-trusted1"],
            }
            users = resolve_trusted_users(config=config)
            self.assertIn("env-user1", users)
            self.assertIn("env-user2", users)
            self.assertIn("config-maintainer", users)
            self.assertIn("config-trusted1", users)

    def test_resolve_trusted_users_fallback_gh_cli(self):
        with patch("subprocess.check_output", return_value="gh-current-user\n"):
            users = resolve_trusted_users()
            self.assertIn("gh-current-user", users)

    # 8. Inline review comments and priority sorting
    def test_sanitize_inline_pr_comments(self):
        inline = [
            {"id": "ic1", "author": {"login": "maintainer-user"}, "body": "maintainer comment"},
            {"id": "ic2", "author": {"login": "external-user"}, "body": "external comment"},
        ]
        # When unapproved, external comments stripped
        res_unapproved = sanitize_inline_pr_comments(
            inline, self.trusted, parent_pr={"author": {"login": "external-user"}, "is_approved": False}
        )
        self.assertEqual(len(res_unapproved), 1)
        self.assertEqual(res_unapproved[0]["id"], "ic1")

    def test_resolve_issue_priority_and_sorting(self):
        self.assertEqual(resolve_issue_priority({"labels": [{"name": "p0"}]}), ("Urgent", 0))
        self.assertEqual(resolve_issue_priority({"labels": [{"name": "p1"}]}), ("High", 1))
        self.assertEqual(resolve_issue_priority({"labels": [{"name": "p2"}]}), ("Medium", 2))
        self.assertEqual(resolve_issue_priority({"labels": [{"name": "priority:low"}]}), ("Low", 3))

        sorted_items = sort_issues_by_priority(
            [
                {"number": 10, "priorityRank": 2},
                {"number": 15, "priorityRank": 0},
                {"number": 12, "priorityRank": 0},
            ]
        )
        self.assertEqual([i["number"] for i in sorted_items], [12, 15, 10])

    # 9. Content normalisation and hidden content detection tests
    def test_normalise_visible_content_strips_html_comments(self):
        # 1. HTML comments are stripped
        self.assertEqual(
            normalise_visible_content("Hello <!-- hidden instruction --> world"),
            "Hello  world",
        )
        # Multiline HTML comments are stripped
        multiline = "line 1\n<!-- comment\nline 2 -->\nline 3"
        self.assertEqual(
            normalise_visible_content(multiline),
            "line 1\n\nline 3",
        )
        # Non-allowlisted comment tag shapes are stripped
        self.assertEqual(
            normalise_visible_content("<!-- agy_other_conv_id: 123 -->"),
            "",
        )
        self.assertEqual(
            normalise_visible_content("<!-- agy_dev_conv_id: abc123 \n malicious -->"),
            "",
        )

    def test_normalise_visible_content_preserves_agy_conv_id_tags(self):
        # 2. AGY conversation ID tags are preserved
        self.assertEqual(
            normalise_visible_content("<!-- agy_dev_conv_id: abc123 -->"),
            "<!-- agy_dev_conv_id: abc123 -->",
        )
        self.assertEqual(
            normalise_visible_content("<!-- agy_arch_conv_id: arch-uuid-456 -->"),
            "<!-- agy_arch_conv_id: arch-uuid-456 -->",
        )
        # Preserved when embedded among other text and stripped comments
        text = "Start <!-- hidden --> <!-- agy_dev_conv_id: abc123 --> End"
        self.assertEqual(
            normalise_visible_content(text),
            "Start  <!-- agy_dev_conv_id: abc123 --> End",
        )

    def test_normalise_visible_content_strips_unicode_tags(self):
        # 3. Unicode tag characters (U+E0001, ASCII smuggling range U+E0000-U+E007F)
        self.assertEqual(
            normalise_visible_content("Hello\U000e0001World"),
            "HelloWorld",
        )
        smuggled = "Visible\U000e0020\U000e0048\U000e0069\U000e007f"
        self.assertEqual(
            normalise_visible_content(smuggled),
            "Visible",
        )

    def test_normalise_visible_content_strips_zero_width_characters(self):
        # 4. Zero-width characters (U+200B, U+200C, U+200D, U+FEFF)
        self.assertEqual(
            normalise_visible_content("A\u200bB\u200cC\u200dD\ufeffE"),
            "ABCDE",
        )

    def test_normalise_visible_content_strips_bidi_overrides(self):
        # 5. Bidi overrides (U+202A–U+202E, U+2066–U+2069)
        bidi_text = "A\u202aB\u202bC\u202cD\u202dE\u202eF\u2066G\u2067H\u2068I\u2069J"
        self.assertEqual(
            normalise_visible_content(bidi_text),
            "ABCDEFGHIJ",
        )

    def test_detect_hidden_content(self):
        # 6. Returns True when content stripped and False when nothing changed
        self.assertTrue(detect_hidden_content("Hello <!-- hidden instruction --> world", "Hello  world"))
        self.assertTrue(detect_hidden_content("Hello\U000e0001World", "HelloWorld"))
        self.assertTrue(detect_hidden_content("A\u200bB", "AB"))
        self.assertTrue(detect_hidden_content("A\u202aB", "AB"))

        # False when nothing changed
        self.assertFalse(detect_hidden_content("Hello world", "Hello world"))
        self.assertFalse(detect_hidden_content("<!-- agy_dev_conv_id: abc123 -->", "<!-- agy_dev_conv_id: abc123 -->"))
        self.assertFalse(detect_hidden_content("", ""))
        # False when only whitespace differs
        self.assertFalse(detect_hidden_content("Hello   world\n", "Hello world"))

    def test_evaluate_and_sanitize_item_sets_has_hidden_content(self):
        # 7. Sets has_hidden_content: true on an item with hidden content (trusted author path)
        issue_with_hidden = {
            "number": 101,
            "title": "Clean Title <!-- hidden payload -->",
            "body": "Normal body with zero-width space\u200b injected",
            "author": {"login": "maintainer-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "labels": [{"name": "bug"}],
            "comments": [
                {
                    "id": "c1",
                    "author": {"login": "maintainer-user"},
                    "body": "Comment with \U000e0001 tag",
                    "createdAt": "2026-09-26T09:05:00Z",
                }
            ],
        }
        ok, reason, clean = evaluate_and_sanitize_item(issue_with_hidden, self.trusted)
        self.assertTrue(ok)
        self.assertEqual(reason, "TRUSTED_AUTHOR")
        self.assertIsNotNone(clean)
        self.assertTrue(clean.get("has_hidden_content"))
        self.assertEqual(clean["title"], "Clean Title ")
        self.assertEqual(clean["body"], "Normal body with zero-width space injected")
        self.assertEqual(clean["comments"][0]["body"], "Comment with  tag")

        # Clean item without hidden content does not set has_hidden_content
        issue_clean = {
            "number": 102,
            "title": "Clean Title",
            "body": "Normal body\n<!-- agy_dev_conv_id: abc123 -->",
            "author": {"login": "maintainer-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "labels": [{"name": "bug"}],
            "comments": [],
        }
        ok_c, reason_c, clean_c = evaluate_and_sanitize_item(issue_clean, self.trusted)
        self.assertTrue(ok_c)
        self.assertNotIn("has_hidden_content", clean_c)
        self.assertIn("<!-- agy_dev_conv_id: abc123 -->", clean_c["body"])

        # External author with approval also sets has_hidden_content: true when hidden content present
        issue_external = {
            "number": 103,
            "title": "External Title <!-- prompt injection -->",
            "body": "External Body",
            "author": {"login": "external-user"},
            "createdAt": "2026-09-26T09:00:00Z",
            "comments": [
                {
                    "id": "c2",
                    "author": {"login": "maintainer-user"},
                    "body": "approved",
                    "createdAt": "2026-09-26T09:10:00Z",
                    "includesCreatedEdit": False,
                }
            ],
        }
        mock_fetcher = lambda n: {"lastEditedAt": None, "includesCreatedEdit": False, "commentLastEditedAt": {"c2": None}}
        ok_ext, reason_ext, clean_ext = evaluate_and_sanitize_item(issue_external, self.trusted, edit_metadata_fetcher=mock_fetcher)
        self.assertTrue(ok_ext)
        self.assertEqual(reason_ext, "APPROVED_BY_MAINTAINER")
        self.assertTrue(clean_ext.get("has_hidden_content"))
        self.assertEqual(clean_ext["title"], "External Title ")


if __name__ == "__main__":
    unittest.main()
