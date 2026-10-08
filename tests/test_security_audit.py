import sys
import os
import tempfile
import json
import hashlib
from pathlib import Path

# Add scripts directory to path to import security_audit
scripts_dir = str(Path(__file__).resolve().parent.parent / "daemon/workspace-plugin/bug-fixer/scripts")
sys.path.insert(0, scripts_dir)

import security_audit

def test_hash_chaining():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            f.write("{}")
        
        # Log first event
        security_audit.log_security_event("test_provider", "reason_1", 1.0, "input 1", config_path)
        
        log_path = security_audit._get_audit_log_path(config_path)
        assert log_path.exists(), "Log file was not created"
        
        # Verify chain with 1 event
        assert security_audit.verify_log_chain(log_path), "Chain verification failed for 1 event"
        
        # Make a backup (snapshot) of the log
        backup_path = Path(tmpdir) / "backup.jsonl"
        with open(log_path, "r") as src, open(backup_path, "w") as dst:
            dst.write(src.read())
            
        # Log second event
        security_audit.log_security_event("test_provider", "reason_2", 0.5, "input 2", config_path)
        
        # Verify chain with 2 events, passing backup
        assert security_audit.verify_log_chain(log_path, backup_path), "Chain verification failed with old log path"
        
        # Log third event
        security_audit.log_security_event("test_provider", "reason_3", 0.1, "input 3", config_path)
        
        # Tamper with the second event (middle of chain)
        with open(log_path, "r") as f:
            lines = f.readlines()
        record = json.loads(lines[1])
        record["confidence"] = 0.99
        lines[1] = json.dumps(record) + "\\n"
        with open(log_path, "w") as f:
            f.writelines(lines)
            
        # Verify chain detects tampering in the middle event
        assert not security_audit.verify_log_chain(log_path, backup_path), "Failed to detect tampering in middle event"
        
        # Truncation bypass: Replace log with a completely new 1-item chain
        with open(log_path, "w") as f:
            f.write(lines[1])
            
        # Verify chain detects truncation bypass
        assert not security_audit.verify_log_chain(log_path, backup_path), "Failed to detect truncation bypass"

        print("All tests passed.")

if __name__ == "__main__":
    test_hash_chaining()
