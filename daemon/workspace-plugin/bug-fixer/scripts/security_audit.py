import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

def _get_audit_log_path(config_path: str = "") -> Path:
    config_env = os.environ.get("BUG_FIXER_CONFIG", config_path)
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / "security_refusals.jsonl"
    return Path(__file__).resolve().parent.parent.parent.parent / "bug-fixer-loop-workspace" / "security_refusals.jsonl"

def _get_last_hash(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return ""
            last_line = lines[-1].strip()
            if not last_line:
                return ""
            # The hash of the previous record is the sha256 of its JSON string
            return hashlib.sha256(last_line.encode("utf-8")).hexdigest()
    except Exception:
        return ""

def log_security_event(provider: str, reason: str, confidence: float, input_text: str, config_path: str = "") -> None:
    log_path = _get_audit_log_path(config_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest() if input_text else ""
    prev_hash = _get_last_hash(log_path)
    
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "reason": reason,
        "confidence": confidence,
        "input_hash": input_hash,
        "prev_hash": prev_hash
    }
    
    record = json.dumps(event)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(record + "\n")

def verify_log_chain(log_path: Path) -> bool:
    if not log_path.exists():
        return True
    try:
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
