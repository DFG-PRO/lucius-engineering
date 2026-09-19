# Shift 10M — Boot / Power-Loss Recovery & Durable Auto-Resume Specification

## Architecture & Overview

Shift 10M implements durable machine-level boot recovery and power-loss auto-resume for Lucius Engineering. It guarantees that if a Mac mini suffers an AC power interruption or unexpected system reboot, Lucius automatically inspects durable state, verifies process single-ownership, and resumes execution safely.

## Key Components

### 1. Boot Identity & Single-Owner Guarantee
- **Boot Identity**: Queries `sysctl -n kern.bootuuid` (or `kern.boottime`) to obtain a unique UUID for the active macOS boot session.
- **Durable Runtime Lease**: Process PIDs from past boot sessions are flagged as stale (`owner_stale = True`). Stale PIDs cannot impersonate active owners.
- **Process Table Audit**: Scans system process table (`ps -ax`) prior to recovery. If an active production supervisor or watchdog is already running on the current boot session, recovery halts immediately with `RECOVERY_ALREADY_RUNNING`.

### 2. Decision Tree & Recovery Actions
- `WAITING_STORAGE`: If `/Volumes/BLACKBOX` or repository path is unmounted, recovery enters bounded sleep without creating duplicate databases or cloning repositories.
- `RECOVERY_ALREADY_RUNNING`: Active supervisor/watchdog detected on current boot session. No duplicate launch.
- `RECOVERY_BLOCKED`: Shift 10J process terminated or rebooted prior to 6.0h acceptance threshold. Real wall-clock endurance time is NOT falsely credited across downtime.
- `RECOVER_10J_HANDOFF_WATCHDOG`: Shift 10J reached 6.0h acceptance threshold. Recovers the post-10J handoff watchdog process.
- `HANDOFF_BLOCKED`: Handoff state is explicitly `HANDOFF_BLOCKED`. Handoff remains blocked.
- `RECOVER_10L`: Machine rebooted while Shift 10L Travel Mode was active. Recovers the existing 10L durable mission from SQLite state.
- `MISSION_COMPLETED`: Shift 10L Travel Mode has completed. No restart.

### 3. Resource & Environmental Readiness
- **External Storage**: Handles delayed mounting of `/Volumes/BLACKBOX`. Waits cleanly until volume is mounted.
- **Provider Readiness**: Temporary Ollama service absence after boot does not fail the mission; runtime enters `WAITING_RESOURCE_SHORT` durable backoff sleep.
- **Network Readiness**: Outbound network absence after boot enters bounded backoff sleep until connectivity is restored.

### 4. launchd Agent Configuration
- Prepared LaunchAgent plist at `config/com.lucius.boot-recovery.plist`.
- Validated structurally via `plutil -lint`.
- Configured to run in the user GUI session context (`LaunchAgent`), preserving venv python paths, volume mount permissions, and user Ollama process access.
- **Note**: Plist is prepared but NOT installed/loaded into production launchd domain during this shift.

## Operational Commands

```bash
# View recovery supervisor status report
python scripts/lucius_boot_recovery.py --status

# Execute recovery decision logic
python scripts/lucius_boot_recovery.py --execute
```
