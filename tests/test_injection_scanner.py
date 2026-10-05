import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import time
import json
import hashlib
import sys
import os

from pathlib import Path

# Ensure we can import from scripts
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"))

from injection_scanner import (
    InjectionScanner,
    CachedInjectionScanner,
    ModelArmorProvider,
    InjectionResult,
)

class TestInjectionScanner(unittest.TestCase):
    def setUp(self):
        self.config_enabled = {
            "enabled": True,
            "fail_open": True,
            "provider": "model_armor",
            "model_armor": {
                "project": "test-project",
                "service_account": "test@test.iam.gserviceaccount.com"
            }
        }
        
    def test_disabled_returns_non_injection(self):
        config = {"enabled": False}
        scanner = InjectionScanner(config)
        result = scanner.scan("some text")
        self.assertFalse(result.is_injection)
        self.assertEqual(result.provider, "disabled")

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_enabled_mock_injection(self, mock_run, mock_urlopen):
        # mock subprocess.run for token
        mock_run.return_value.stdout = "mock-token\n"
        
        # mock urlopen
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "sanitizationResult": {
                "filterResults": {
                    "pi_and_jailbreak": {
                        "piAndJailbreakFilterResult": {
                            "executionState": "EXECUTION_SUCCESS",
                            "matchState": "MATCH_FOUND",
                            "confidenceLevel": "HIGH"
                        }
                    }
                }
            }
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = InjectionScanner(self.config_enabled)
        result = scanner.scan("some bad text")
        self.assertTrue(result.is_injection)
        self.assertEqual(result.confidence, 0.9)

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_enabled_mock_clean(self, mock_run, mock_urlopen):
        mock_run.return_value.stdout = "mock-token\n"
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "sanitizationResult": {
                "filterResults": {
                    "pi_and_jailbreak": {
                        "piAndJailbreakFilterResult": {
                            "executionState": "EXECUTION_SUCCESS",
                            "matchState": "MATCH_NOT_FOUND",
                            "confidenceLevel": "LOW_AND_ABOVE"
                        }
                    }
                }
            }
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = InjectionScanner(self.config_enabled)
        result = scanner.scan("some clean text")
        self.assertFalse(result.is_injection)

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_fail_open_http_error(self, mock_run, mock_urlopen):
        mock_run.return_value.stdout = "mock-token\n"
        mock_urlopen.side_effect = urllib.error.URLError("mock error")

        scanner = InjectionScanner(self.config_enabled)
        result = scanner.scan("some text")
        self.assertFalse(result.is_injection)
        self.assertIn("error", result.details)

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_fail_closed_http_error(self, mock_run, mock_urlopen):
        mock_run.return_value.stdout = "mock-token\n"
        mock_urlopen.side_effect = urllib.error.URLError("mock error")
        
        config = self.config_enabled.copy()
        config["fail_open"] = False
        scanner = InjectionScanner(config)
        with self.assertRaises(urllib.error.URLError):
            scanner.scan("some text")

    @patch.object(CachedInjectionScanner, "_save_cache")
    @patch.object(CachedInjectionScanner, "_load_cache", return_value={})
    @patch.object(InjectionScanner, "scan")
    def test_cached_scanner_same_text(self, mock_scan, mock_load, mock_save):
        mock_scan.return_value = InjectionResult(False, 0.0, "test")
        scanner = CachedInjectionScanner(InjectionScanner(self.config_enabled))
        
        res1 = scanner.scan("test text")
        res2 = scanner.scan("test text")
        
        self.assertEqual(res1.is_injection, res2.is_injection)
        self.assertEqual(res1.confidence, res2.confidence)
        self.assertEqual(res1.provider, res2.provider)
        self.assertEqual(res1.details, res2.details)
        mock_scan.assert_called_once()

    @patch.object(CachedInjectionScanner, "_save_cache")
    @patch.object(CachedInjectionScanner, "_load_cache", return_value={})
    @patch.object(InjectionScanner, "scan")
    def test_cached_scanner_different_text(self, mock_scan, mock_load, mock_save):
        mock_scan.return_value = InjectionResult(False, 0.0, "test")
        scanner = CachedInjectionScanner(InjectionScanner(self.config_enabled))
        
        scanner.scan("test text 1")
        scanner.scan("test text 2")
        
        self.assertEqual(mock_scan.call_count, 2)

    @patch("subprocess.run")
    def test_get_access_token_subprocess_args(self, mock_run):
        mock_run.return_value.stdout = "mock-token\n"
        provider = ModelArmorProvider(self.config_enabled["model_armor"])
        token = provider._get_access_token()
        
        self.assertEqual(token, "mock-token")
        mock_run.assert_called_once_with(
            [
                "gcloud", "auth", "print-access-token",
                "--impersonate-service-account=test@test.iam.gserviceaccount.com"
            ],
            capture_output=True, text=True, check=True
        )

    @patch("subprocess.run")
    def test_token_is_cached(self, mock_run):
        mock_run.return_value.stdout = "mock-token\n"
        provider = ModelArmorProvider(self.config_enabled["model_armor"])
        
        tok1 = provider._get_access_token()
        tok2 = provider._get_access_token()
        
        self.assertEqual(tok1, tok2)
        mock_run.assert_called_once()

    def test_config_validation_missing_project(self):
        import copy
        config = copy.deepcopy(self.config_enabled)
        config["fail_open"] = False
        config["model_armor"]["project"] = ""
        with self.assertRaises(ValueError):
            scanner = InjectionScanner(config)

if __name__ == '__main__':
    unittest.main()


    def test_cached_scanner_does_not_cache_disabled(self):
        """Disabled results should not be cached — they'd poison the cache."""
        config = {"enabled": False}
        scanner = CachedInjectionScanner(
            InjectionScanner(config), cache_dir="/tmp/test_injection_cache"
        )
        result = scanner.scan("test text")
        self.assertFalse(result.is_injection)
        self.assertEqual(result.provider, "disabled")
        # Cache should still be empty
        self.assertEqual(scanner._cache, {})

    @patch.object(CachedInjectionScanner, "_save_cache")
    @patch.object(CachedInjectionScanner, "_load_cache", return_value={})
    @patch.object(InjectionScanner, "scan")
    def test_cached_scanner_does_not_cache_errors(self, mock_scan, mock_load, mock_save):
        """Error results should not be cached — outage shouldn't permanently whitelist."""
        mock_scan.return_value = InjectionResult(False, 0.0, "model_armor", "error: timeout")
        scanner = CachedInjectionScanner(InjectionScanner(self.config_enabled))
        scanner.scan("test text")
        # Cache should be empty — errors aren't cached
        self.assertEqual(scanner._cache, {})

