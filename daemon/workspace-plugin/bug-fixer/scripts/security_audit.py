import json
import os
import hashlib
import fcntl
from datetime import datetime, timezone
from pathlib import Path

def _get_audit_log_path(config_path: str = "") -> Path:
    config_env = os.environ.get("BUG_FIXER_CONFIG", config_path)
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / "security_refusals.jsonl"
    return Path(__file__).resolve().parent.parent.parent.parent / "bug-fixer-loop-workspace" / "security_refusals.jsonl"

def log_security_event(provider: str, reason: str, confidence: float, input_text: str, config_path: str = "") -> None:
    log_path = _get_audit_log_path(config_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest() if input_text else ""
    
    with open(log_path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last_line = ""
        for line in f:
            if line.strip():
                last_line = line.strip()
                
        if not last_line:
            prev_hash = ""
        else:
            prev_hash = hashlib.sha256(last_line.encode("utf-8")).hexdigest()
        
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "reason": reason,
            "confidence": confidence,
            "input_hash": input_hash,
            "prev_hash": prev_hash
        }
        
        record = json.dumps(event)
        f.seek(0, os.SEEK_END)
        f.write(record + "\n")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)

def verify_log_chain(log_path: Path, old_log_path: Path | None = None) -> bool:
    if not log_path.exists():
        if old_log_path and old_log_path.exists() and old_log_path.stat().st_size > 0:
            return False
        return True
    try:
        if old_log_path and old_log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f_new, open(old_log_path, "r", encoding="utf-8") as f_old:
                for old_line in f_old:
                    new_line = f_new.readline()
                    if not new_line or new_line != old_line:
                        return False

        with open(log_path, "r", encoding="utf-8") as f:
            prev_hash = ""
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("prev_hash") != prev_hash:
                    return False
                prev_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
        
        return True
    except Exception:
        return False
