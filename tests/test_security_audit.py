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
        
        # Log second event
        security_audit.log_security_event("test_provider", "reason_2", 0.5, "input 2", config_path)
        
        # Verify chain with 2 events
        assert security_audit.verify_log_chain(log_path), "Chain verification failed for 2 events"
        
        # Tamper with the log
        with open(log_path, "r") as f:
            lines = f.readlines()
            
        record = json.loads(lines[0])
        record["confidence"] = 0.99
        lines[0] = json.dumps(record) + "\n"
        
        with open(log_path, "w") as f:
            f.writelines(lines)
            
        # Verify chain detects tampering
        assert not security_audit.verify_log_chain(log_path), "Chain verification failed to detect tampering"
        
        print("All tests passed.")

if __name__ == "__main__":
    test_hash_chaining()
