import os
import sys
from pathlib import Path
import json
import urllib.request
import urllib.error
import subprocess
import hashlib
import random
import time

class InjectionResult:
    """Result of an injection scan."""
    def __init__(self, is_injection: bool, confidence: float,
                 provider: str, details: str = ""):
        self.is_injection = is_injection
        self.confidence = confidence
        self.provider = provider
        self.details = details

class RateLimitError(Exception):
    """Raised when Model Armor returns 429 after retry exhaustion."""

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
    
    # Retry config for 429 quota errors
    MAX_RETRIES = 5
    BASE_DELAY = 2.0  # seconds — enough to cross a minute boundary

    def scan(self, text: str) -> InjectionResult:
        """Call Model Armor sanitizeUserPrompt API with retry on 429."""
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
        
        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                break  # success — exit retry loop
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.MAX_RETRIES:
                    delay = self.BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    sys.stderr.write(
                        f"MODEL_ARMOR_RATE_LIMITED: 429 on attempt {attempt + 1}/"
                        f"{self.MAX_RETRIES + 1}, retrying in {delay:.1f}s\n"
                    )
                    time.sleep(delay)
                    last_error = e
                    continue
                if e.code == 429:
                    # Final 429 — raise as RateLimitError so scanner fails closed
                    raise RateLimitError(
                        f"429 after {self.MAX_RETRIES + 1} attempts"
                    ) from e
                raise  # non-429 — bubble up immediately
        
        # Parse Model Armor response
        sanitization = result.get("sanitizationResult", {})
        filter_results = sanitization.get("filterResults", {})
        pi_and_jailbreak = filter_results.get("pi_and_jailbreak", {})
        injection = pi_and_jailbreak.get("piAndJailbreakFilterResult", {})
        
        execution_state = injection.get("executionState", "EXECUTION_UNSPECIFIED")
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
        """Scan text for injection.

        Rate-limit errors (429) always fail closed to prevent abuse.
        Other errors respect the fail_open setting.
        """
        if not self.enabled:
            return InjectionResult(False, 0.0, "disabled")
        try:
            return self._provider_impl.scan(text)
        except RateLimitError as exc:
            # 429 after retries — ALWAYS fail closed (potential DDoS bypass)
            sys.stderr.write(
                f"INJECTION_SCAN_RATE_LIMITED: {self.provider}: {exc} "
                f"(fail_closed — potential abuse)\n"
            )
            return InjectionResult(True, 1.0, self.provider,
                                   f"rate_limited: {exc}")
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


def _get_cache_path(config_path: str | None = None) -> Path:
    config_env = os.environ.get("BUG_FIXER_CONFIG", "")
    if config_env and Path(config_env).is_file():
        return Path(config_env).resolve().parent / "injection_cache.json"
    if config_path and Path(config_path).is_file():
        return Path(config_path).resolve().parent / "injection_cache.json"
    # Legacy fallback
    return Path(__file__).resolve().parent.parent / "injection_cache.json"


class CachedInjectionScanner:
    """Wraps InjectionScanner with SHA-256 content-hash cache.

    Only caches successful provider verdicts (not disabled/error results).
    Cache key includes provider and template to prevent cross-config hits.
    """

    def __init__(self, scanner: InjectionScanner, cache_dir: str | None = None,
                 max_cache: int = 1000):
        self.scanner = scanner
        self.max_cache = max_cache
        if cache_dir is not None:
            self.cache_path = str(Path(cache_dir) / "injection_cache.json")
        else:
            self.cache_path = str(_get_cache_path())
        self._cache: dict[str, dict] = self._load_cache()

    def _cache_key(self, text: str) -> str:
        """Include provider + template in key to prevent cross-config hits."""
        provider = self.scanner.provider if self.scanner.enabled else "disabled"
        template = ""
        if hasattr(self.scanner, '_provider_impl') and hasattr(self.scanner._provider_impl, 'template'):
            template = self.scanner._provider_impl.template
        prefix = f"{provider}:{template}:"
        return hashlib.sha256((prefix + text).encode()).hexdigest()

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
        # Don't use cache when scanner is disabled — no verdict to cache
        if not self.scanner.enabled:
            return self.scanner.scan(text)

        key = self._cache_key(text)
        if key in self._cache:
            cached = self._cache[key]
            return InjectionResult(
                is_injection=cached.get("is_injection", False),
                confidence=cached.get("confidence", 0.0),
                provider=cached.get("provider", ""),
                details=cached.get("details", "")
            )

        result = self.scanner.scan(text)

        # Only cache successful provider verdicts, not errors
        if result.provider != "disabled" and not result.details.startswith("error:"):
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
