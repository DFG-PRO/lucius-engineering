# Shift 10L — Travel Mode Unattended Endurance & Post-10J Handoff Specification

## Overview

Shift 10L prepares Lucius Engineering for multi-day unattended operation (**48 to 72 wall-clock hours**), specifically designed for Travel Mode. It addresses the idle scheduler spin defect identified during Shift 10J Attempt 2 and provides a safe, read-only handoff gate from Shift 10J Attempt 2.

## Key Principles & Semantics

### 1. Durable Idle Sleep / Backoff & Wake Semantics
- **No Hot-Looping**: When all work in the global portfolio is waiting, sleeping, or blocked, the continuation service calculates the next wake time (`next_wake`) or periodic portfolio recheck timestamp.
- **Bounded Tick Sleep**: The runtime runner enters a responsive sleep loop using `sleep_until_next_wake_or_recheck()`, sleeping in responsive 1.0s ticks to handle OS signals (SIGINT/SIGTERM) cleanly.
- **Zero Idle Spin & Audit Bounding**: While sleeping, the runtime generates 0 model provider calls and 0 unnecessary audit log events, capping CPU usage at ~0%.
- **Automatic Wake Triggers**: Execution resumes immediately when `now >= next_wake`, or when external dependency/resource availability is updated.

### 2. Post-10J Attempt 2 Handoff Gate
- **PID Isolation**: Shift 10J Attempt 2 runs independently under PID 9902.
- **Read-Only Verification**: `verify_10j_acceptance_evidence()` inspects `.lucius/state_shift_10j_attempt_2.db` via `file:.lucius/state_shift_10j_attempt_2.db?mode=ro` without acquiring write locks or corrupting running 10J evidence.
- **Acceptance Condition**: 10J must complete at least **6.0 real wall-clock hours** (21,600 seconds) from its T0 (`2026-09-18T19:37:09.956275-06:00`).
- **Fail-Closed Protection**: If 10J evidence is incomplete (< 6.0h) or unverified, 10L handoff fails closed and halts before launcher startup.

### 3. Multi-Day Observability Logging
- **Health Checkpoints**: Saved every 30 minutes to `.lucius/shift_10l_travel_mode_health_checkpoints.jsonl`.
- **Liveness Pulses**: Emitted every 60 seconds showing elapsed hours, next wake timestamp, total waiting work by wait class, total blocked work by class, unique useful completions, and audit bytes per cycle.

## Launch Interface

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_shift_10l_travel_mode.py \
  --hours 48.0 \
  --target-hours 72.0 \
  --handoff-from-10j
```
