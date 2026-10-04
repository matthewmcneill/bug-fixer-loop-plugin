---
name: scanning-infra-wo5
description: Handoff instructions for Work Order 5 — Injection classifier POC with Model Armor
complexity: architect
---

# WO5 — Injection Classifier POC (Model Armor)

## Role Definition
> You are an expert senior software engineer and execution-focused worker agent. You are NOT the Principal Architect. Do not make high-level architectural decisions, do not branch, and do not plan. Your task is strictly limited to executing the checklist below.

## Objective
Implement a vendor-neutral `InjectionScanner` that runs in the **dispatchers** (control plane) to classify incoming issue/PR content for prompt injection before the agent sees it. The first provider is Google Cloud Model Armor. A hit applies the `security:prompt-injection` label. It fails open with an audit log by default (configurable).

## Architecture Context
- **Dispatchers** are plain Python scripts (`scripts/*_dispatcher.py`) that run on cron. They are the control plane — no LLM, no agent environment.
- The `InjectionScanner` runs **in the dispatcher process**, NOT in `safe_ingest.py`. This keeps GCP credentials out of the agent's environment.
- The triage dispatcher (`scripts/triage_dispatcher.py`) calls `safe_ingest.py issue-list` to get issues, then spawns an LLM conversation. The injection scan happens **between** those steps: after ingesting, before spawning.

## Strict Boundary Box
- **ONLY** modify/create: `scripts/injection_scanner.py` (new), `scripts/triage_dispatcher.py`, `tests/test_injection_scanner.py` (new)
- Do NOT touch `safe_ingest.py`, `pre_tool_gate.py`, `github_url_gate.py`, or skills
- Do NOT modify the `ScanBackend` protocol — injection scanning is a separate concern
- The scanner reads config from the normalised config (WO4 format)

## CRITICAL WARNING — triage_dispatcher.py import path
The dispatchers import from `scripts/liveness.py` via `sys.path` manipulation. Follow the same pattern for importing `injection_scanner.py`.

## Tooling Constraints
- Run tests with: `python3 -m unittest discover -s tests -v`
- No external Python dependencies for the scanner module itself — use stdlib `urllib.request` for the API call
- Tests must work without GCP access (mock all HTTP calls)

## GCP Auth Model (agreed constraints — A1-A4, A5 deferred)
- **A1**: Dedicated GCP project for plugin scanning
- **A2**: Dedicated service account with **only `roles/modelarmor.user`**
- **A3**: No JSON keys. Dispatcher mints a short-lived token per run via `gcloud auth print-access-token --impersonate-service-account=<sa>`, held in process memory only
- **A4**: API quota cap and budget alert on the project
- **A5 (deferred)**: Separate OS user for dispatchers — not required for this implementation

## Line-Item Instructions

- [ ] `scripts/injection_scanner.py` — Create the new module with:

  ```python
  class InjectionResult:
      """Result of an injection scan."""
      def __init__(self, is_injection: bool, confidence: float,
                   provider: str, details: str = ""):
          self.is_injection = is_injection
          self.confidence = confidence
          self.provider = provider
          self.details = details
  
  class InjectionScanner:
      """Vendor-neutral injection classifier interface."""
      
      def __init__(self, config: dict):
          """Config is the normalised scanning.injection section."""
          self.enabled = config.get("enabled", False)
          self.fail_open = config.get("fail_open", True)
          self.provider = config.get("provider", "model_armor")
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
  ```

- [ ] `scripts/injection_scanner.py` — Implement `ModelArmorProvider`:
  ```python
  class ModelArmorProvider:
      """Google Cloud Model Armor injection classifier."""
      
      ENDPOINT = (
          "https://modelarmor.{location}-aiplatform.googleapis.com/v1/"
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
          import time
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
          import hashlib, time
          
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
          injection = filter_results.get("piAndJailbreakFilterResult", {})
          match_state = injection.get("matchState", "MATCH_STATE_UNSPECIFIED")
          confidence = injection.get("confidenceLevel", "CONFIDENCE_UNSPECIFIED")
          
          is_injection = match_state == "MATCH_FOUND"
          conf_map = {
              "CONFIDENCE_LOW": 0.3,
              "CONFIDENCE_MEDIUM": 0.6,
              "CONFIDENCE_HIGH": 0.9,
              "CONFIDENCE_UNSPECIFIED": 0.0,
          }
          
          return InjectionResult(
              is_injection=is_injection,
              confidence=conf_map.get(confidence, 0.0),
              provider="model_armor",
              details=f"match_state={match_state}, confidence={confidence}",
          )
  ```

- [ ] `scripts/injection_scanner.py` — Add a cache layer:
  ```python
  class CachedInjectionScanner:
      """Wraps InjectionScanner with SHA-256 content-hash cache."""
      
      def __init__(self, scanner: InjectionScanner, max_cache: int = 1000):
          self.scanner = scanner
          self._cache: dict[str, InjectionResult] = {}
          self.max_cache = max_cache
      
      def scan(self, text: str) -> InjectionResult:
          key = hashlib.sha256(text.encode()).hexdigest()
          if key in self._cache:
              return self._cache[key]
          result = self.scanner.scan(text)
          if len(self._cache) < self.max_cache:
              self._cache[key] = result
          return result
  ```

- [ ] `scripts/triage_dispatcher.py` — Integrate the injection scanner:
  After the issue list is fetched but before the LLM conversation is spawned, scan each issue's title + body:
  ```python
  from injection_scanner import InjectionScanner, CachedInjectionScanner
  
  # After loading config and getting issues...
  injection_config = config.get("scanning", {}).get("injection", {})
  scanner = CachedInjectionScanner(InjectionScanner(injection_config))
  
  for issue in untriaged:
      # Get full issue content via safe_ingest
      text = f"{issue.get('title', '')} {issue.get('body', '')}"
      result = scanner.scan(text)
      if result.is_injection:
          log(f"INJECTION DETECTED on #{issue['number']}: "
              f"{result.provider} confidence={result.confidence}")
          # Apply label
          run(["gh", "issue", "edit", str(issue["number"]),
               "--add-label", "security:prompt-injection"])
          # Remove from untriaged list
          untriaged = [i for i in untriaged if i["number"] != issue["number"]]
  ```

- [ ] `tests/test_injection_scanner.py` — Create test file with:
  1. Test `InjectionScanner` with `enabled=False` returns non-injection result
  2. Test `InjectionScanner` with `enabled=True` and mock Model Armor response indicating injection
  3. Test `InjectionScanner` with `enabled=True` and mock Model Armor response indicating clean
  4. Test `InjectionScanner` fail-open: mock HTTP error, verify it returns non-injection with error details
  5. Test `InjectionScanner` fail-closed: mock HTTP error with `fail_open=False`, verify it raises
  6. Test `CachedInjectionScanner`: verify second call with same text returns cached result (mock only called once)
  7. Test `CachedInjectionScanner`: verify different text triggers new scan
  8. Test `ModelArmorProvider._get_access_token()` calls gcloud with correct impersonation flag (mock subprocess)
  9. Test that token is cached and not re-minted on second call
  10. Test config validation: missing project/service_account raises ValueError

## Verification Requirement
Run `python3 -m unittest discover -s tests -v` and confirm all tests pass. All tests must pass **without GCP access** (mock all HTTP and subprocess calls).

## Issue Surfacing
If you discover a defect, upstream bug, or concern *outside your boundary box*, surface it in your Completion Report below — do not file issues directly.
