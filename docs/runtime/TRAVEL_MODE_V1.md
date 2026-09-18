# Lucius Travel Mode v1 Specification & Operational Contract

**Version:** 1.0.1
**Status:** CANONICAL OPERATIONAL SPECIFICATION  
**Scope:** Multi-Hour Unattended Continuation Across DFG Universe

---

## 1. Operational Philosophy

Travel Mode allows Lucius to operate safely and productively for bounded durations (e.g. 4 to 6 hours) without requiring interactive human prompting between task transitions.

### Safety Invariants
1. **Zero Real Money Actions:** No live cryptocurrency, forex, or equities orders; zero Binance live execution API calls.
2. **Zero External Outreach:** No external customer emails, messaging, or live outreach.
3. **Zero Publishing or Deployment:** No code deployments to production, no package publishing, no public commits.
4. **Zero Credential Mutation:** Secret stores and credentials remain strictly read-only.
5. **Class A / Class B Unattended Only:** Only deterministic inspection (Class A) and bounded research synthesis (Class B) are permitted unattended. Class C code mutation is strictly blocked unattended.
6. **Non-Blocking Contempt:** Blocked tasks bypass non-fatally and do not stall the remaining queue.

---

## 2. Architecture & Components

- **`TravelLauncher` (`src/lucius/runtime/launcher.py`):**
  Performs preflight checks (repository health, git porcelain worktree count <= 150, disk space >= 5GB, canonical registry integrity, Ollama model availability, Darwin backlog feeder discovery). Initializes the execution runtime loop service, session budget, dynamic backlog feeder, and model execution router.
- **`DarwinBacklogFeeder` (`src/lucius/runtime/feeder.py`):**
  Dynamically ingests validated backlog items from Darwin when local queues empty, enforcing regression guard rules.
- **`BoundedContinuationService` (`src/lucius/runtime/continuation.py`):**
  Manages session budgets (max wall time, max cycles, failure thresholds) and executes multi-project task cycles via `MultiProjectDispatcher`.
- **`ModelExecutionRouter` (`src/lucius/runtime/router.py`):**
  Routes requests to registered execution providers (e.g. local Ollama `qwen3:8b` or scripted test adapter), recording full audit trails and plan freeze enforcement.

---

## 3. Preflight & Launch Commands

### Fast Runtime Smoke Verification (< 1s)
Verifies preflight checks and constructs the full runtime loop, router, provider registry, and session budget for 1 cycle without consuming backlog items:
```bash
./.venv/bin/python -m lucius.runtime.launcher --mode travel --smoke
```

### Preflight Inspection Only
Runs preflight environment checks without starting a session:
```bash
./.venv/bin/python -m lucius.runtime.launcher --preflight-only
```

### Canonical 4-Hour Travel Mode Launch
Executes unattended multi-project continuation bounded to 4.0 hours or 25 cycles:
```bash
./.venv/bin/python -m lucius.runtime.launcher --mode travel --hours 4.0 --max-cycles 25
```

### Scripted Offline / Test Provider
Executes travel mode using deterministic scripted provider adapters:
```bash
./.venv/bin/python -m lucius.runtime.launcher --mode travel --hours 1.0 --max-cycles 10 --provider scripted
```
