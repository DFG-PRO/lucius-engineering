#!/usr/bin/env bash
# Lucius Engineering — Boot Recovery Internal-Disk Bootstrap Shim
#
# Solves external-volume bootstrapping paradox by waiting with bounded backoff for
# /Volumes/BLACKBOX to be mounted before executing the canonical Lucius recovery supervisor.

set -euo pipefail

REPO_DIR="/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering"
MAX_ATTEMPTS=60
SLEEP_SECONDS=5

echo "[LUCIUS SHIM] Starting boot recovery bootstrap shim..."

attempt=0
while [ $attempt -lt $MAX_ATTEMPTS ]; do
    if [ -d "$REPO_DIR" ] && [ -f "$REPO_DIR/scripts/lucius_boot_recovery.py" ]; then
        echo "[LUCIUS SHIM] Target volume and repository verified at '$REPO_DIR'."
        cd "$REPO_DIR"
        exec "./.venv/bin/python" "scripts/lucius_boot_recovery.py" "--execute"
    fi
    attempt=$((attempt + 1))
    echo "[LUCIUS SHIM] Waiting for external volume '$REPO_DIR' (attempt $attempt/$MAX_ATTEMPTS)..."
    sleep $SLEEP_SECONDS
done

echo "[LUCIUS SHIM ERROR] External volume '$REPO_DIR' not mounted after $((MAX_ATTEMPTS * SLEEP_SECONDS)) seconds. Exiting fail-closed."
exit 1
