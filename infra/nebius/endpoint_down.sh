#!/usr/bin/env bash
# Stop the Nebius GPU endpoint and wait until STOPPED.
set -euo pipefail

NEBIUS_DIR=${NEBIUS_DIR:-/home/agent/workspace/nebius}
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

pushd "$NEBIUS_DIR" >/dev/null
export NEBIUS_IAM_TOKEN=${NEBIUS_IAM_TOKEN:-$(nebius iam get-access-token)}
EP=$(pulumi stack output endpointId)
popd >/dev/null

state() { nebius ai endpoint get "$EP" --format jsonpath='{.status.state}' 2>/dev/null || echo unknown; }

log "stopping endpoint $EP"
nebius ai endpoint stop "$EP" >/dev/null || log "WARNING: stop command failed"
for _ in $(seq 60); do
  s=$(state)
  [ "$s" = STOPPED ] && { log "endpoint STOPPED"; exit 0; }
  sleep 10
done
log "WARNING: endpoint still in state '$(state)', check manually: nebius ai endpoint get $EP"
