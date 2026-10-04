import sys
import json
import urllib.request
import urllib.error
import subprocess
import hashlib
import time

class InjectionResult:
    """Result of an injection scan."""
    def __init__(self, is_injection: bool, confidence: float,
                 provider: str, details: str = ""):
        self.is_injection = is_injection
        self.confidence = confidence
        self.provider = provider
        self.details = details

class ModelArmorProvider:
    """Google Cloud Model Armor injection classifier."""
    
    ENDPOINT = (
        "https://modelarmor.{location}.rep.googleapis.com/v1/"
        "projects/{project}/locations/{location}/templates/{template}:"
        "sanitizeUserPrompt"
    )
    
    def __init__(self, config: dict):
        self.project = config.get("project", "")
        self.location = config.get("location", "us-central1")
        self.service_account = config.get("service_account", "")
        self.template = config.get("template", "default")
        self._token_cache: tuple[str, float] | None = None  # (token, expiry)
    
    def _get_access_token(self) -> str:
        """Mint short-lived token via impersonation. A3: no JSON keys."""
        if self._token_cache:
            token, expiry = self._token_cache
            if time.time() < expiry - 60:  # 60s buffer
                return token
        
        cmd = [
            "gcloud", "auth", "print-access-token",
            f"--impersonate-service-account={self.service_account}"
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
        token = result.stdout.strip()
        # Tokens are valid for 3600s by default
        self._token_cache = (token, time.time() + 3600)
        return token
    
    def scan(self, text: str) -> InjectionResult:
        """Call Model Armor sanitizeUserPrompt API."""
        if not self.project or not self.service_account:
            raise ValueError(
                "Model Armor requires 'project' and 'service_account' in config"
            )
        
        token = self._get_access_token()
        url = self.ENDPOINT.format(
            project=self.project,
            location=self.location,
            template=self.template,
        )
        
        payload = json.dumps({
            "userPromptData": {
                "text": text
            }
        }).encode()
        
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        
        # Parse Model Armor response
        sanitization = result.get("sanitizationResult", {})
        filter_results = sanitization.get("filterResults", {})
        pi_and_jailbreak = filter_results.get("pi_and_jailbreak", {})
        injection = pi_and_jailbreak.get("piAndJailbreakFilterResult", {})
        
        execution_state = pi_and_jailbreak.get("executionState", "EXECUTION_UNSPECIFIED")
        if execution_state != "EXECUTION_SUCCESS":
            raise RuntimeError(f"Model Armor execution failed: {execution_state}")
            
        match_state = injection.get("matchState", "MATCH_STATE_UNSPECIFIED")
        confidence = injection.get("confidenceLevel", "CONFIDENCE_UNSPECIFIED")
        
        is_injection = match_state == "MATCH_FOUND"
        conf_map = {
            "LOW_AND_ABOVE": 0.3,
            "MEDIUM_AND_ABOVE": 0.6,
            "HIGH": 0.9,
            "CONFIDENCE_UNSPECIFIED": 0.0,
        }
        
        return InjectionResult(
            is_injection=is_injection,
            confidence=conf_map.get(confidence, 0.0),
            provider="model_armor",
            details=f"match_state={match_state}, confidence={confidence}",
        )

class InjectionScanner:
    """Vendor-neutral injection classifier interface."""
    
    def __init__(self, config: dict):
        """Config is the normalised scanning.injection section."""
        self.enabled = config.get("enabled", False)
        self.fail_open = config.get("fail_open", True)
        self.provider = config.get("provider", "model_armor")
        
        if self.enabled and self.provider == "model_armor":
            ma_config = config.get("model_armor", {})
            if not ma_config.get("project") or not ma_config.get("service_account"):
                raise ValueError("Model Armor requires 'project' and 'service_account' in config")
                
        self._provider_impl = self._resolve_provider(config)
    
    def _resolve_provider(self, config):
        if self.provider == "model_armor":
            return ModelArmorProvider(config.get("model_armor", {}))
        raise ValueError(f"Unknown injection provider: {self.provider}")
    
    def scan(self, text: str) -> InjectionResult:
        """Scan text for injection. Fails open by default."""
        if not self.enabled:
            return InjectionResult(False, 0.0, "disabled")
        try:
            return self._provider_impl.scan(text)
        except Exception as exc:
            if self.fail_open:
                # Audit log the failure, allow through
                sys.stderr.write(
                    f"INJECTION_SCAN_ERROR: {self.provider}: {exc} "
                    f"(fail_open=true, allowing through)\n"
                )
                return InjectionResult(False, 0.0, self.provider,
                                      f"error: {exc}")
            raise

import os
class CachedInjectionScanner:
    """Wraps InjectionScanner with SHA-256 content-hash cache."""
    
    def __init__(self, scanner: InjectionScanner, max_cache: int = 1000):
        self.scanner = scanner
        self.max_cache = max_cache
        self.cache_path = os.path.join(
            os.getcwd(), ".agents", "plugins", "bug-fixer-loop-workspace", "injection_cache.json"
        )
        self._cache: dict[str, dict] = self._load_cache()
    
    def _load_cache(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
        
    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w") as f:
                json.dump(self._cache, f)
        except Exception:
            pass
            
    def scan(self, text: str) -> InjectionResult:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            cached = self._cache[key]
            return InjectionResult(
                is_injection=cached.get("is_injection", False),
                confidence=cached.get("confidence", 0.0),
                provider=cached.get("provider", ""),
                details=cached.get("details", "")
            )
            
        result = self.scanner.scan(text)
        
        if len(self._cache) >= self.max_cache:
            self._cache.pop(next(iter(self._cache)))
            
        self._cache[key] = {
            "is_injection": result.is_injection,
            "confidence": result.confidence,
            "provider": result.provider,
            "details": result.details
        }
        self._save_cache()
        return result
