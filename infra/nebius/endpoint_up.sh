#!/usr/bin/env bash
# Start the Nebius GPU endpoint, wait for /v1/models, and optionally sync Open WebUI.
# Logs go to stderr; KEY=VALUE lines go to stdout for `cube` lifecycle env injection.
set -euo pipefail

SYNC_OPENWEBUI=0
MODEL=${MODEL:-google/gemma-4-26B-A4B-it}
while [ $# -gt 0 ]; do
  case "$1" in
    --sync-openwebui) SYNC_OPENWEBUI=1; shift ;;
    --model) MODEL=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

REPO=$(cd "$(dirname "$0")/../.." && pwd)
NEBIUS_DIR=${NEBIUS_DIR:-/home/agent/workspace/nebius}
READY_TIMEOUT_S=${READY_TIMEOUT_S:-2400}

cd "$REPO"
set -a; [ -f .env ] && . ./.env; set +a
log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

if [ "$SYNC_OPENWEBUI" = 1 ]; then
  : "${OPENWEBUI_API_KEY:?set OPENWEBUI_API_KEY in .env}"
  curl -fsS -o /dev/null "${OPENWEBUI_URL:-http://localhost:3000}/health"
fi

pushd "$NEBIUS_DIR" >/dev/null
export NEBIUS_IAM_TOKEN=${NEBIUS_IAM_TOKEN:-$(nebius iam get-access-token)}
EP=$(pulumi stack output endpointId)
URL=$(pulumi stack output endpointUrl)
TOKEN=$(pulumi stack output endpointToken --show-secrets)
popd >/dev/null

state() { nebius ai endpoint get "$EP" --format jsonpath='{.status.state}' 2>/dev/null || echo unknown; }

s=$(state)
if [ "$s" = RUNNING ]; then
  log "endpoint $EP already RUNNING"
else
  log "starting endpoint $EP (state: $s)"
  [ "$s" != STARTING ] && nebius ai endpoint start "$EP" >/dev/null
fi

deadline=$(($(date +%s) + READY_TIMEOUT_S))
until curl -fsS -m 10 -o /dev/null -H "Authorization: Bearer $TOKEN" "$URL/v1/models"; do
  [ "$(date +%s)" -gt "$deadline" ] && { log "endpoint not ready after ${READY_TIMEOUT_S}s"; exit 1; }
  sleep 20
done
log "endpoint ready"

if [ "$SYNC_OPENWEBUI" = 1 ]; then
  NEBIUS_BASE_URL="$URL/v1" NEBIUS_TOKEN="$TOKEN" OPENWEBUI_MODEL="$MODEL" \
    uv run python infra/openwebui/setup.py >&2
fi

printf 'NEBIUS_BASE_URL=%s/v1\n' "$URL"
printf 'NEBIUS_TOKEN=%s\n' "$TOKEN"
