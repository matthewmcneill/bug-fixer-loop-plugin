import subprocess
import unittest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from liveness import agentapi_is_reachable

class TestLiveness(unittest.TestCase):
    
    @patch('subprocess.run')
    def test_agentapi_is_reachable_success(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        self.assertTrue(agentapi_is_reachable())
        mock_run.assert_called_once_with(["agentapi", "--help"], capture_output=True, timeout=5)

    @patch('subprocess.run')
    def test_agentapi_is_reachable_failure_code(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        self.assertFalse(agentapi_is_reachable())

    @patch('subprocess.run')
    def test_agentapi_is_reachable_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["agentapi", "--help"], timeout=5)
        
        self.assertFalse(agentapi_is_reachable())

    @patch('subprocess.run')
    def test_agentapi_is_reachable_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'agentapi'")
        
        self.assertFalse(agentapi_is_reachable())

if __name__ == '__main__':
    unittest.main()
