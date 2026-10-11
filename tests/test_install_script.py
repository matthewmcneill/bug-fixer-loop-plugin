import os
import shutil
import subprocess
import tempfile
import tarfile
import zipfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
DAEMON_DIR = REPO_ROOT / "daemon"


class TestInstallScript(unittest.TestCase):
    def test_help_flag(self):
        result = subprocess.run(
            [str(INSTALL_SH), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--from-release", result.stdout)

    def test_unknown_flag_fails(self):
        result = subprocess.run(
            [str(INSTALL_SH), "--invalid-flag-xyz"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)

    def test_daemon_packaging_contents(self):
        """Verify daemon packaging archives only daemon/ contents without parent folder."""
        tmpdir = tempfile.mkdtemp()
        try:
            tar_path = Path(tmpdir) / "bug-fixer-daemon.tar.gz"
            zip_path = Path(tmpdir) / "bug-fixer-daemon.zip"

            # Build tarball
            subprocess.run(
                ["tar", "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.DS_Store",
                 "-czf", str(tar_path), "-C", str(DAEMON_DIR), "."],
                check=True,
            )

            # Build zip
            subprocess.run(
                ["bash", "-c", f"(cd {DAEMON_DIR} && zip -r {zip_path} . -x '*__pycache__*' '*.pyc' '*.DS_Store')"],
                check=True,
                capture_output=True,
            )

            # Verify tar contents
            with tarfile.open(tar_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any("plugin.json" in n for n in names))
                self.assertTrue(any("scripts/manage.py" in n for n in names))
                self.assertFalse(any(n.startswith("daemon/") for n in names))
                self.assertFalse(any("__pycache__" in n for n in names))

            # Verify zip contents
            with zipfile.ZipFile(zip_path, "r") as z:
                names = z.namelist()
                self.assertTrue(any("plugin.json" in n for n in names))
                self.assertTrue(any("scripts/manage.py" in n for n in names))
                self.assertFalse(any(n.startswith("daemon/") for n in names))
                self.assertFalse(any("__pycache__" in n for n in names))

        finally:
            shutil.rmtree(tmpdir)

    def test_from_release_archive_extraction_and_install(self):
        """Simulate --from-release with a local file URL archive."""
        tmpdir = tempfile.mkdtemp()
        try:
            # Package a test daemon archive
            tar_path = Path(tmpdir) / "bug-fixer-daemon.tar.gz"
            subprocess.run(
                ["tar", "--exclude=__pycache__", "--exclude=*.pyc", "-czf", str(tar_path), "-C", str(DAEMON_DIR), "."],
                check=True,
            )

            # Target install directory mock via HOME override
            fake_home = Path(tmpdir) / "fake_home"
            fake_home.mkdir()

            # Run install.sh with --from-release pointing to file:// URL
            # or file path if supported
            file_url = f"file://{tar_path}"
            env = os.environ.copy()
            env["HOME"] = str(fake_home)
            # Prevent agy plugin enable from doing anything real
            env["PATH"] = "/usr/bin:/bin"

            result = subprocess.run(
                [str(INSTALL_SH), f"--from-release={file_url}"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}, stderr: {result.stderr}")
            installed_daemon = fake_home / ".gemini" / "config" / "plugins" / "bug-fixer-daemon"
            self.assertTrue((installed_daemon / "plugin.json").exists())
            self.assertTrue((installed_daemon / "scripts" / "manage.py").exists())
            self.assertTrue((installed_daemon / "sidecars").is_dir())
        finally:
            shutil.rmtree(tmpdir)

    def test_repo_leak_scan_clean(self):
        """Verify the working tree is clean of secrets and leak patterns."""
        import sys
        scripts_dir = REPO_ROOT / "daemon" / "workspace-plugin" / "bug-fixer" / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        import pre_tool_gate
        backend = pre_tool_gate.resolve_backend({}, patterns=list(pre_tool_gate.BUILTIN_LEAK_PATTERNS))
        violations = backend.scan(
            str(REPO_ROOT),
            pre_tool_gate.ScanScope.TREE,
            exclude_paths=pre_tool_gate.RegexBackend.DEFAULT_REGEX_EXCLUDE_PATHS,
        )
        self.assertEqual(violations, [], f"Leak scan violations detected: {violations}")



